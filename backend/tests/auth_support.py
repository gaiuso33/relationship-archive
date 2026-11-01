"""Synthetic keys, tokens and a fake key provider for the authentication tests.

Everything is generated at run time: no key or token-shaped literal is stored in the
repository, and nothing here is a real Supabase value.
"""

import base64
import json
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.core.auth import KeyUnavailable, UnknownKey
from app.core.config import Settings

PROJECT_URL = "https://synthetic-project.example.invalid"
ISSUER = f"{PROJECT_URL}/auth/v1"
AUDIENCE = "authenticated"
KID = "synthetic-key-1"
USER_ID = "11111111-2222-4333-8444-555555555555"
# Distinctive values that must never appear in a response or a log.
SECRET_EMAIL = "synthetic.token.email@example.invalid"
SECRET_NAME = "SyntheticTokenHolderName"


class FakeKeys:
    """Stands in for the JWKS provider: no network, no Supabase."""

    def __init__(self, keys: dict[str, Any] | None = None, *, unavailable: bool = False) -> None:
        self.keys = keys or {}
        self.unavailable = unavailable
        self.calls: list[str] = []

    def get_key(self, kid: str) -> Any:
        self.calls.append(kid)
        if self.unavailable:
            raise KeyUnavailable
        try:
            return self.keys[kid]
        except KeyError:
            raise UnknownKey from None


def new_ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def new_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


EC_KEY = new_ec_key()
OTHER_EC_KEY = new_ec_key()
RSA_KEY = new_rsa_key()


def valid_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": USER_ID,
        "role": "authenticated",
        "is_anonymous": False,
        "iat": now,
        "exp": now + 3600,
        "email": SECRET_EMAIL,
        "user_metadata": {"full_name": SECRET_NAME},
        "session_id": str(uuid.uuid4()),
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not _MISSING}


_MISSING = object()
MISSING = _MISSING


def make_token(
    claims: dict[str, Any] | None = None,
    *,
    key: Any = EC_KEY,
    algorithm: str = "ES256",
    headers: dict[str, Any] | None = None,
) -> str:
    header = {"kid": KID, **(headers or {})}
    header = {k: v for k, v in header.items() if v is not _MISSING}
    return jwt.encode(
        valid_claims() if claims is None else claims, key, algorithm=algorithm, headers=header
    )


def public_jwk(private_key: Any, kid: str = KID) -> dict[str, Any]:
    algorithm = ECAlgorithm if isinstance(private_key, ec.EllipticCurvePrivateKey) else RSAAlgorithm
    jwk = json.loads(algorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "use": "sig"})
    return jwk


def auth_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "supabase_url": PROJECT_URL,
    }
    values.update(overrides)
    return Settings(**values)


def raw_token(header: dict[str, Any], claims: dict[str, Any] | None = None) -> str:
    """A token with a hand-written header (and a junk signature) for cases the library
    refuses to encode, such as a non-string `kid`."""

    def segment(value: dict[str, Any]) -> str:
        data = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    return ".".join([segment(header), segment(claims or valid_claims()), "c2lnbmF0dXJl"])
