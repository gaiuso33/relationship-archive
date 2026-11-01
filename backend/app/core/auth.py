"""Verification of Supabase access tokens (ADR-004).

Authentication only: this module answers "which verified user is this?". Whether that user
may touch a record is a separate decision made by each endpoint (ADR-002).

Rules that hold throughout:

* Keys come from the JWKS URL built from our own configuration. Nothing in a token (`iss`,
  `jku`, `x5u`, `jwk`, `x5c`) ever decides where keys come from.
* Only asymmetric algorithms are accepted. The legacy shared secret is not supported, so a
  token signed with HMAC is rejected whatever key material it claims to use.
* Every rejection is reduced to a fixed `RejectionReason`. Token contents, claims and library
  error text never leave this module.
"""

import http.client
import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import jwt

logger = logging.getLogger("app.auth")

ALLOWED_ALGORITHMS = ("ES256", "RS256")
MAX_TOKEN_LENGTH = 4096
MAX_KID_LENGTH = 128
# Supabase issues tokens for signed-in users with this role and audience. The anon and
# service-role API keys are also JWTs, but they carry other roles and have no user subject.
REQUIRED_ROLE = "authenticated"
LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ("exp", "sub", "iss", "aud")

# JWKS fetching limits. The timeout is a total deadline for the whole fetch.
JWKS_TIMEOUT_SECONDS = 3.0
# Supabase says not to cache keys in the application for longer than its own edge cache.
JWKS_TTL_SECONDS = 300.0
# At most one forced refresh per interval, however many unknown key ids arrive.
JWKS_REFRESH_COOLDOWN_SECONDS = 30.0
# After a failed fetch, wait before trying again so a down endpoint is not hammered.
JWKS_FAILURE_BACKOFF_SECONDS = 5.0
JWKS_MAX_BYTES = 64 * 1024
JWKS_CHUNK_BYTES = 4096
JWKS_MAX_KEYS = 20


class RejectionReason(StrEnum):
    """Why a request was not authenticated. Fixed values, safe to log."""

    MISSING_CREDENTIALS = "missing_credentials"
    MALFORMED_AUTHORIZATION = "malformed_authorization"
    MALFORMED_TOKEN = "malformed_token"
    BAD_ALGORITHM = "bad_algorithm"
    BAD_KEY_ID = "bad_key_id"
    UNKNOWN_KEY = "unknown_key"
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    BAD_ISSUER = "bad_issuer"
    BAD_AUDIENCE = "bad_audience"
    MISSING_CLAIM = "missing_claim"
    BAD_SUBJECT = "bad_subject"
    BAD_ROLE = "bad_role"
    ANONYMOUS_USER = "anonymous_user"
    INVALID_TOKEN = "invalid_token"


class AuthenticationFailed(Exception):
    """The request carries no acceptable proof of identity (HTTP 401).

    Created with a `RejectionReason` only, so no token content can travel with it.
    """

    def __init__(self, reason: RejectionReason) -> None:
        super().__init__()
        self.reason = reason


class KeyUnavailable(Exception):
    """The signing keys cannot be obtained right now (HTTP 503). Never treated as success."""


class UnknownKey(Exception):
    """The key id is not one of the project's signing keys."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """The verified identity of the caller, and nothing else.

    Deliberately minimal: no email, no name, no other claims.
    """

    user_id: UUID


class SigningKeyProvider(Protocol):
    def get_key(self, kid: str) -> Any:
        """Return the public key for `kid`, or raise `UnknownKey` or `KeyUnavailable`."""
        ...


# --- JWKS key provider -----------------------------------------------------------------


def _abort_connection(sock: socket.socket | None, expired: threading.Event) -> None:
    """Called by the watchdog at the deadline: wake any read that is blocked on the socket."""
    expired.set()
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def fetch_jwks_bytes(url: str, timeout: float, max_bytes: int) -> bytes:
    """GET the key set: bounded in time and size, and never following a redirect.

    `timeout` is a total wall-clock deadline for the whole fetch, not a per-read timeout. A
    server that sends a byte at a time, each inside the per-read timeout, cannot hold the
    caller (and the provider lock) beyond it:

    * the body is read in small chunks, and the remaining time is checked before each one;
    * a watchdog timer also shuts the socket down at the deadline, which covers a server that
      drips the response headers.

    The connection is made directly: proxy environment variables are not used, and a redirect
    is an error (the status must be 200).
    """
    deadline = time.monotonic() + timeout
    parts = urlsplit(url)
    connection_class = {"https": http.client.HTTPSConnection, "http": http.client.HTTPConnection}
    try:
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise KeyUnavailable from None
    if parts.scheme not in connection_class or not host:
        raise KeyUnavailable
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

    connection = connection_class[parts.scheme](host, port, timeout=timeout)
    expired = threading.Event()
    watchdog: threading.Timer | None = None
    chunks: list[bytes] = []
    try:
        connection.connect()
        sock = connection.sock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KeyUnavailable
        watchdog = threading.Timer(remaining, _abort_connection, args=(sock, expired))
        watchdog.daemon = True
        watchdog.start()

        connection.request("GET", target, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise KeyUnavailable
        declared = response.getheader("Content-Length")
        total = 0
        # The response closes itself once the declared length has been read, and the socket
        # may not be used after that.
        while not response.isclosed():
            remaining = deadline - time.monotonic()
            if remaining <= 0 or expired.is_set():
                raise KeyUnavailable
            sock.settimeout(remaining)
            chunk = response.read1(min(JWKS_CHUNK_BYTES, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise KeyUnavailable
            chunks.append(chunk)
        if declared is not None and (not declared.isdigit() or total != int(declared)):
            raise KeyUnavailable  # a short or inconsistent body is never trusted
        if expired.is_set():
            # The watchdog cut the connection: what was read is incomplete, whatever it looks like.
            raise KeyUnavailable
    except (OSError, http.client.HTTPException, ValueError):
        raise KeyUnavailable from None
    finally:
        if watchdog is not None:
            watchdog.cancel()
        connection.close()
    return b"".join(chunks)


class JwksKeyProvider:
    """Public signing keys from the project's JWKS endpoint, cached and rate-limited.

    * Keys are cached for `ttl` seconds. When the cache has expired and the endpoint cannot be
      reached the provider fails closed: it does not keep serving stale keys.
    * An unknown key id may force at most one refresh per `cooldown`, so a client sending
      random key ids cannot cause a stream of outbound requests.
    * After a failed fetch no new fetch is made for `failure_backoff` seconds.
    * One lock serialises fetches, so concurrent requests share a single fetch.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        timeout: float = JWKS_TIMEOUT_SECONDS,
        ttl: float = JWKS_TTL_SECONDS,
        cooldown: float = JWKS_REFRESH_COOLDOWN_SECONDS,
        failure_backoff: float = JWKS_FAILURE_BACKOFF_SECONDS,
        max_bytes: int = JWKS_MAX_BYTES,
        fetch: Callable[[str, float, int], bytes] = fetch_jwks_bytes,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = jwks_url
        self._timeout = timeout
        self._ttl = ttl
        self._cooldown = cooldown
        self._failure_backoff = failure_backoff
        self._max_bytes = max_bytes
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._failed_at: float | None = None

    def get_key(self, kid: str) -> Any:
        with self._lock:
            now = self._clock()
            if self._keys is None or now - self._fetched_at >= self._ttl:
                self._refresh(now)
            assert self._keys is not None
            key = self._keys.get(kid)
            if key is None and now - self._fetched_at >= self._cooldown:
                try:
                    self._refresh(now)
                except KeyUnavailable:
                    # The cached set is still within its lifetime, so the key id is simply
                    # unknown to us: a bad token, not an outage.
                    if self._keys is None:
                        raise
                    raise UnknownKey from None
                assert self._keys is not None
                key = self._keys.get(kid)
            if key is None:
                raise UnknownKey
            return key

    def _refresh(self, now: float) -> None:
        if self._failed_at is not None and now - self._failed_at < self._failure_backoff:
            raise KeyUnavailable
        try:
            keys = self._parse(self._fetch(self._url, self._timeout, self._max_bytes))
        except KeyUnavailable:
            # The cache is left as it is. If it has expired, the next call tries to refresh
            # again and fails again, so stale keys are never served.
            self._failed_at = now
            raise
        self._keys = keys
        self._fetched_at = now
        self._failed_at = None

    @staticmethod
    def _parse(body: bytes) -> dict[str, Any]:
        try:
            document = json.loads(body)
        except (ValueError, RecursionError):
            raise KeyUnavailable from None
        entries = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise KeyUnavailable
        keys: dict[str, Any] = {}
        for entry in entries[:JWKS_MAX_KEYS]:
            if not isinstance(entry, dict):
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or not 0 < len(kid) <= MAX_KID_LENGTH:
                continue
            if entry.get("use") not in (None, "sig") or entry.get("kty") not in ("EC", "RSA"):
                continue
            try:
                keys[kid] = jwt.PyJWK(entry).key
            except Exception:  # noqa: BLE001 - any malformed key is skipped, never reported
                continue
        if not keys:
            # An empty set is what a project still on the legacy shared secret publishes.
            raise KeyUnavailable
        return keys


# --- token verification ----------------------------------------------------------------

_JWT_ERRORS: tuple[tuple[type[Exception], RejectionReason], ...] = (
    (jwt.ExpiredSignatureError, RejectionReason.EXPIRED),
    (jwt.ImmatureSignatureError, RejectionReason.NOT_YET_VALID),
    (jwt.InvalidIssuedAtError, RejectionReason.NOT_YET_VALID),
    (jwt.InvalidSignatureError, RejectionReason.BAD_SIGNATURE),
    (jwt.InvalidIssuerError, RejectionReason.BAD_ISSUER),
    (jwt.InvalidAudienceError, RejectionReason.BAD_AUDIENCE),
    (jwt.MissingRequiredClaimError, RejectionReason.MISSING_CLAIM),
    (jwt.InvalidAlgorithmError, RejectionReason.BAD_ALGORITHM),
    (jwt.DecodeError, RejectionReason.MALFORMED_TOKEN),
)


def _reason_for(exc: Exception) -> RejectionReason:
    for error_type, reason in _JWT_ERRORS:
        if isinstance(exc, error_type):
            return reason
    return RejectionReason.INVALID_TOKEN


class TokenVerifier:
    def __init__(
        self,
        keys: SigningKeyProvider,
        *,
        issuer: str,
        audience: str,
        leeway: int = LEEWAY_SECONDS,
    ) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway

    def verify(self, token: str) -> AuthenticatedUser:
        """Return the verified user, or raise `AuthenticationFailed` or `KeyUnavailable`."""
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise AuthenticationFailed(RejectionReason.MALFORMED_TOKEN)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthenticationFailed(RejectionReason.MALFORMED_TOKEN) from None

        # The header is untrusted. It may only select among keys and algorithms we already
        # allow: its `alg` must be on the allowlist and its `kid` must name a key we hold.
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in ALLOWED_ALGORITHMS:
            raise AuthenticationFailed(RejectionReason.BAD_ALGORITHM)
        kid = header.get("kid")
        if not isinstance(kid, str) or not 0 < len(kid) <= MAX_KID_LENGTH:
            raise AuthenticationFailed(RejectionReason.BAD_KEY_ID)
        try:
            key = self._keys.get_key(kid)
        except UnknownKey:
            raise AuthenticationFailed(RejectionReason.UNKNOWN_KEY) from None

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": list(REQUIRED_CLAIMS)},
            )
        except Exception as exc:  # noqa: BLE001 - every failure is mapped to a fixed reason
            raise AuthenticationFailed(_reason_for(exc)) from None
        return self._identity(claims)

    @staticmethod
    def _identity(claims: dict[str, Any]) -> AuthenticatedUser:
        subject = claims.get("sub")
        if not isinstance(subject, str):
            raise AuthenticationFailed(RejectionReason.BAD_SUBJECT)
        try:
            user_id = UUID(subject)
        except ValueError:
            raise AuthenticationFailed(RejectionReason.BAD_SUBJECT) from None
        # Only the canonical hyphenated form: UUID() alone also accepts braces, a URN prefix,
        # missing hyphens and misplaced hyphens.
        if str(user_id) != subject.lower():
            raise AuthenticationFailed(RejectionReason.BAD_SUBJECT)
        if claims.get("role") != REQUIRED_ROLE:
            raise AuthenticationFailed(RejectionReason.BAD_ROLE)
        # Supabase marks anonymous sign-ins with `is_anonymous: true` while still using the
        # `authenticated` role. Only an explicit `false` is accepted.
        if claims.get("is_anonymous") is not False:
            raise AuthenticationFailed(RejectionReason.ANONYMOUS_USER)
        return AuthenticatedUser(user_id=user_id)
