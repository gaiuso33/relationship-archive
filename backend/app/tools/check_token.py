"""Check your own Supabase project against what the backend expects (docs/authentication.md).

Run it on your own machine:

    python -m app.tools.check_token

It prints the project's published signing keys, then asks for an access token with the input
hidden, so the token is never on the command line, in shell history or in a file. It reports
only fixed facts about the token (algorithm, whether the issuer and audience match, role,
whether the subject is a UUID, and whether the backend would accept it). It never prints the
token, the subject, an email, a name or any other claim, and it never sends the token anywhere:
signature checks use the public keys.
"""

import getpass
import json
import re
import sys
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import jwt

from app.core.auth import (
    JWKS_MAX_BYTES,
    AuthenticationFailed,
    FailureCategory,
    JwksFetcher,
    JwksKeyProvider,
    KeyUnavailable,
    SigningKeyProvider,
    TokenVerifier,
)
from app.core.config import Settings, get_settings

_PLAIN = re.compile(r"[A-Za-z0-9_.:/\-]{1,80}")


def _plain(value: object) -> str:
    """Show a value only if it is short and plain text, so a surprise cannot print data."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str) and _PLAIN.fullmatch(value):
        return value
    return "<present, unusual value not shown>" if value is not None else "<absent>"


def describe_keys(body: bytes) -> list[str]:
    try:
        entries = json.loads(body).get("keys", [])
    except (ValueError, AttributeError):
        return ["The key endpoint did not return a JSON key set."]
    if not isinstance(entries, list) or not entries:
        return [
            "No signing keys are published. The project probably still uses the legacy JWT "
            "secret. Enable asymmetric signing keys in the Supabase dashboard "
            "(JWT signing keys); the backend does not support the shared secret."
        ]
    lines = [f"{len(entries)} signing key(s) published:"]
    for entry in entries[:20]:
        if isinstance(entry, dict):
            lines.append(
                f"  kid={_plain(entry.get('kid'))} kty={_plain(entry.get('kty'))} "
                f"crv={_plain(entry.get('crv'))} alg={_plain(entry.get('alg'))}"
            )
    return lines


def describe_token(token: str, settings: Settings, key_ids: set[str]) -> list[str]:
    """Facts about the token, read WITHOUT verifying it. Never includes claim values that
    could identify a person."""
    try:
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return ["The input is not a readable JWT."]
    subject = claims.get("sub")
    try:
        canonical = isinstance(subject, str) and str(UUID(subject)) == subject.lower()
    except ValueError:
        canonical = False
    issued, expires = claims.get("iat"), claims.get("exp")
    lifetime = (
        f"{int(expires - issued)} seconds"
        if isinstance(issued, int | float) and isinstance(expires, int | float)
        else "unknown"
    )
    remaining = int(expires - time.time()) if isinstance(expires, int | float) else None
    issuer_ok = "yes" if claims.get("iss") == settings.supabase_issuer else "NO"
    return [
        f"alg: {_plain(header.get('alg'))}",
        f"kid present in the published keys: {'yes' if header.get('kid') in key_ids else 'NO'}",
        f"iss matches the configured project: {issuer_ok}"
        f" (token iss: {_plain(claims.get('iss'))}; expected: {settings.supabase_issuer})",
        f"aud: {_plain(claims.get('aud'))} (backend expects: {settings.supabase_jwt_audience})",
        f"role: {_plain(claims.get('role'))} (backend requires: authenticated)",
        f"is_anonymous: {_plain(claims.get('is_anonymous'))} (backend requires: false)",
        f"sub is a canonical UUID: {'yes' if canonical else 'NO'}",
        f"token lifetime: {lifetime}; seconds until expiry: {remaining}",
    ]


# One fixed sentence per failure category. Nothing from the network or from an exception is shown.
KEY_FAILURE_MESSAGES: dict[FailureCategory, str] = {
    FailureCategory.DNS: "Could not fetch the signing keys: the host name could not be resolved.",
    FailureCategory.CONNECT: "Could not fetch the signing keys: no connection could be made.",
    FailureCategory.TLS: "Could not fetch the signing keys: the TLS or certificate check failed.",
    FailureCategory.STATUS: "Could not fetch the signing keys: the server did not answer HTTP 200.",
    FailureCategory.DEADLINE: (
        "Could not fetch the signing keys: the time limit expired "
        "(raise SUPABASE_JWKS_TIMEOUT_SECONDS if your network is slow, up to 30)."
    ),
    FailureCategory.SIZE: "Could not fetch the signing keys: the response was too large.",
    FailureCategory.BODY: (
        "Could not fetch the signing keys: the response was incomplete or unusable."
    ),
}


def run(
    settings: Settings,
    read_token: Callable[[str], str] = getpass.getpass,
    keys: SigningKeyProvider | None = None,
    fetch: Callable[[str, float, int], bytes] | None = None,
    out: Callable[[str], Any] = print,
) -> int:
    if not settings.supabase_url or not settings.supabase_jwks_url:
        out("SUPABASE_URL is not set. Put your project URL in your local .env first.")
        return 2
    out(f"Project: {settings.supabase_url}")
    key_ids: set[str] = set()
    try:
        body = (fetch or JwksFetcher())(
            settings.supabase_jwks_url, settings.supabase_jwks_timeout_seconds, JWKS_MAX_BYTES
        )
    except KeyUnavailable as exc:
        out(KEY_FAILURE_MESSAGES[exc.category])
        return 2
    for line in describe_keys(body):
        out(line)
    try:
        key_ids = {
            entry["kid"] for entry in json.loads(body)["keys"] if isinstance(entry.get("kid"), str)
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        key_ids = set()

    token = read_token("Paste an access token (input is hidden; it is not stored or sent): ")
    token = token.strip()
    for line in describe_token(token, settings, key_ids):
        out(line)

    issuer = settings.supabase_issuer
    assert issuer is not None
    provider = keys or JwksKeyProvider(
        settings.supabase_jwks_url, timeout=settings.supabase_jwks_timeout_seconds
    )
    verifier = TokenVerifier(provider, issuer=issuer, audience=settings.supabase_jwt_audience)
    try:
        verifier.verify(token)
    except AuthenticationFailed as exc:
        out(f"The backend would REJECT this token: {exc.reason.value}")
        return 1
    except KeyUnavailable as exc:
        out(f"The backend could not obtain signing keys ({exc.category.value}): answer 503.")
        return 2
    out("The backend would ACCEPT this token.")
    return 0


def main() -> int:
    return run(get_settings())


if __name__ == "__main__":
    sys.exit(main())
