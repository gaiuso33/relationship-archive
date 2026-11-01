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
import ipaddress
import json
import logging
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import jwt

from app.core.config import JWKS_TIMEOUT_DEFAULT_SECONDS

logger = logging.getLogger("app.auth")

ALLOWED_ALGORITHMS = ("ES256", "RS256")
MAX_TOKEN_LENGTH = 4096
MAX_KID_LENGTH = 128
# Supabase issues tokens for signed-in users with this role and audience. The anon and
# service-role API keys are also JWTs, but they carry other roles and have no user subject.
REQUIRED_ROLE = "authenticated"
LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ("exp", "sub", "iss", "aud")

# JWKS fetching limits. The total deadline for one fetch (DNS, connect, TLS, headers and body)
# is the `SUPABASE_JWKS_TIMEOUT_SECONDS` setting. Each connect attempt and each read may also
# take at most these many seconds, and never longer than the time left in the total deadline.
JWKS_CONNECT_TIMEOUT_SECONDS = 3.0
JWKS_READ_TIMEOUT_SECONDS = 3.0
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


class FailureCategory(StrEnum):
    """Why the signing keys could not be obtained. Fixed words, safe to log and print.

    Set only by our own code, never from exception text or anything the network returned.
    """

    DNS = "dns"  # the resolver returned an error or no addresses
    CONNECT = "connect"  # no address could be connected to
    TLS = "tls"  # the TLS handshake or certificate or host name verification failed
    STATUS = "status"  # the response status was not 200 (including redirects)
    DEADLINE = "deadline"  # the total deadline expired, in any phase
    SIZE = "size"  # the response was larger than allowed
    BODY = "body"  # incomplete, inconsistent with its declared length, or not a usable key set


class KeyUnavailable(Exception):
    """The signing keys cannot be obtained right now (HTTP 503). Never treated as success.

    Carries only a `FailureCategory`, so nothing from the network can travel with it.
    """

    def __init__(self, category: FailureCategory = FailureCategory.BODY) -> None:
        super().__init__()
        self.category = category


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


def _abort_connection(holder: list[socket.socket | None], expired: threading.Event) -> None:
    """Called by the watchdog at the deadline: wake any read that is blocked on the socket."""
    expired.set()
    sock = holder[0]
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise KeyUnavailable(FailureCategory.DEADLINE)
    return remaining


def _arm(sock: socket.socket, deadline: float) -> None:
    """Allow the next socket operation at most the read timeout, and never more than the time
    left in the total deadline."""
    sock.settimeout(min(JWKS_READ_TIMEOUT_SECONDS, _remaining(deadline)))


class _Lookup:
    __slots__ = ("addresses", "done", "failed", "key")

    def __init__(self, key: tuple[str, int]) -> None:
        self.key = key
        self.done = threading.Event()
        self.addresses: list[Any] = []
        self.failed = False


def _system_resolve(host: str, port: int) -> list[Any]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


class SingleFlightResolver:
    """Name resolution with a bounded caller wait and at most one lookup in flight.

    The operating system's resolver call cannot be cancelled. So the lookup runs in a helper
    (daemon) thread and the caller waits only until its deadline. A lookup that outlives its
    caller keeps running until the resolver gives up and its result is discarded, but it is
    never joined by a second thread: while it is outstanding, new callers wait for that same
    lookup, up to their own deadline, instead of starting another. So repeated timeouts cannot
    pile up resolver threads, and the number of outstanding lookups stays at most one for the
    lifetime of this object (one per provider).
    """

    def __init__(self, resolve: Callable[[str, int], list[Any]] = _system_resolve) -> None:
        self._resolve = resolve
        self._lock = threading.Lock()
        self._current: _Lookup | None = None

    def lookup(self, host: str, port: int, deadline: float) -> list[Any]:
        key = (host, port)
        while True:
            with self._lock:
                current = self._current
                if current is None or current.done.is_set():
                    current = self._current = _Lookup(key)
                    threading.Thread(
                        target=self._run, args=(current, host, port), daemon=True, name="jwks-dns"
                    ).start()
                    break
                if current.key == key:
                    break  # join the lookup that is already outstanding
            # A lookup for something else is outstanding: wait for it, within our deadline.
            if not current.done.wait(_remaining(deadline)):
                raise KeyUnavailable(FailureCategory.DEADLINE)
        if not current.done.wait(_remaining(deadline)):
            raise KeyUnavailable(FailureCategory.DEADLINE)
        if current.failed or not current.addresses:
            raise KeyUnavailable(FailureCategory.DNS)
        return current.addresses

    def _run(self, lookup: _Lookup, host: str, port: int) -> None:
        try:
            lookup.addresses = list(self._resolve(host, port))
        except BaseException:  # noqa: BLE001 - the reason is deliberately discarded
            lookup.failed = True
        finally:
            lookup.done.set()


def _addresses(resolver: SingleFlightResolver, host: str, port: int, deadline: float) -> list[Any]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return resolver.lookup(host, port, deadline)
    # A literal address needs no lookup, and the numeric-only flag means this never blocks.
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST)
    except OSError:
        raise KeyUnavailable(FailureCategory.DNS) from None
    if not found:
        raise KeyUnavailable(FailureCategory.DNS)
    return list(found)


def _connect(addresses: list[Any], deadline: float) -> socket.socket:
    for family, socktype, proto, _canonical_name, sockaddr in addresses:
        remaining = _remaining(deadline)
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(min(JWKS_CONNECT_TIMEOUT_SECONDS, remaining))
            sock.connect(sockaddr)
            return sock
        except OSError:
            sock.close()
    if time.monotonic() >= deadline:
        raise KeyUnavailable(FailureCategory.DEADLINE)
    raise KeyUnavailable(FailureCategory.CONNECT)


class JwksFetcher:
    """GET the key set within one total deadline, never following a redirect.

    `timeout` is a single monotonic deadline for the whole exchange: name resolution, connect,
    TLS, response headers and body. A server (or resolver) that is slow, or that sends a byte
    at a time, cannot hold the caller or the provider lock beyond it:

    * name resolution is single-flight and waited for only until the deadline;
    * each connect attempt and each read is limited to 3 seconds and to the time left;
    * the body is read in small chunks, and a watchdog timer shuts the socket down at the
      deadline, which also covers a dripped TLS handshake or dripped response headers.

    Every failure is a `KeyUnavailable` with one fixed `FailureCategory`. The connection is made
    directly: proxy environment variables are not used, certificates are verified against the
    original host name, and a redirect is an error (the status must be 200).
    """

    def __init__(self, resolver: SingleFlightResolver | None = None) -> None:
        self._resolver = resolver or SingleFlightResolver()

    def __call__(self, url: str, timeout: float, max_bytes: int) -> bytes:
        deadline = time.monotonic() + timeout
        parts = urlsplit(url)
        try:
            host = parts.hostname
            port = parts.port
        except ValueError:
            raise KeyUnavailable(FailureCategory.CONNECT) from None
        if parts.scheme not in ("https", "http") or not host:
            raise KeyUnavailable(FailureCategory.CONNECT)
        secure = parts.scheme == "https"
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

        addresses = _addresses(self._resolver, host, port or (443 if secure else 80), deadline)
        sock = _connect(addresses, deadline)
        holder: list[socket.socket | None] = [sock]
        expired = threading.Event()
        watchdog = threading.Timer(
            max(deadline - time.monotonic(), 0.001), _abort_connection, args=(holder, expired)
        )
        watchdog.daemon = True
        watchdog.start()
        category = FailureCategory.BODY
        connection: http.client.HTTPConnection | None = None
        chunks: list[bytes] = []
        try:
            if secure:
                category = FailureCategory.TLS
                context = ssl.create_default_context()
                # wrap_socket takes over the file descriptor, so from here on the watchdog
                # must act on the TLS socket.
                sock = context.wrap_socket(
                    sock, server_hostname=host, do_handshake_on_connect=False
                )
                holder[0] = sock
                _arm(sock, deadline)
                sock.do_handshake()
                category = FailureCategory.BODY
            # `sock` is already connected, so http.client only speaks HTTP over it.
            connection = (http.client.HTTPSConnection if secure else http.client.HTTPConnection)(
                host, port
            )
            connection.sock = sock
            _arm(sock, deadline)
            connection.request("GET", target, headers={"Accept": "application/json"})
            _arm(sock, deadline)
            response = connection.getresponse()
            if response.status != 200:
                raise KeyUnavailable(FailureCategory.STATUS)
            declared = response.getheader("Content-Length")
            total = 0
            # The response closes itself once the declared length has been read, and the
            # socket may not be used after that.
            while not response.isclosed():
                _arm(sock, deadline)
                if expired.is_set():
                    raise KeyUnavailable(FailureCategory.DEADLINE)
                chunk = response.read1(min(JWKS_CHUNK_BYTES, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise KeyUnavailable(FailureCategory.SIZE)
                chunks.append(chunk)
            if expired.is_set():
                # The watchdog cut the connection: what was read is incomplete, whatever it
                # looks like.
                raise KeyUnavailable(FailureCategory.DEADLINE)
            if declared is not None and (not declared.isdigit() or total != int(declared)):
                raise KeyUnavailable(FailureCategory.BODY)  # a short or inconsistent body
        except KeyUnavailable:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            if expired.is_set() or time.monotonic() >= deadline:
                raise KeyUnavailable(FailureCategory.DEADLINE) from None
            raise KeyUnavailable(category) from None
        finally:
            watchdog.cancel()
            if connection is not None:
                connection.close()
            for open_socket in {sock, holder[0]}:
                if open_socket is not None:
                    try:
                        open_socket.close()
                    except OSError:
                        pass
        return b"".join(chunks)


def fetch_jwks_bytes(url: str, timeout: float, max_bytes: int) -> bytes:
    """One-off fetch with its own resolver. The key provider keeps one `JwksFetcher` for its
    whole lifetime instead, so that DNS stays single-flight across fetches."""
    return JwksFetcher()(url, timeout, max_bytes)


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
        timeout: float = JWKS_TIMEOUT_DEFAULT_SECONDS,
        ttl: float = JWKS_TTL_SECONDS,
        cooldown: float = JWKS_REFRESH_COOLDOWN_SECONDS,
        failure_backoff: float = JWKS_FAILURE_BACKOFF_SECONDS,
        max_bytes: int = JWKS_MAX_BYTES,
        fetch: Callable[[str, float, int], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = jwks_url
        self._timeout = timeout
        self._ttl = ttl
        self._cooldown = cooldown
        self._failure_backoff = failure_backoff
        self._max_bytes = max_bytes
        # One fetcher for the provider's lifetime, so DNS lookups stay single-flight.
        self._fetch = fetch or JwksFetcher()
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._failed_at: float | None = None
        self._failed_category = FailureCategory.BODY

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
            raise KeyUnavailable(self._failed_category)
        try:
            keys = self._parse(self._fetch(self._url, self._timeout, self._max_bytes))
        except KeyUnavailable as exc:
            # The cache is left as it is. If it has expired, the next call tries to refresh
            # again and fails again, so stale keys are never served. Times are taken after the
            # attempt: a fetch may legitimately last several seconds.
            self._failed_at = self._clock()
            self._failed_category = exc.category
            raise
        self._keys = keys
        self._fetched_at = self._clock()
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
