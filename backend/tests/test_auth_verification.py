"""Token verification: every way a token can be wrong, and the one way it can be right."""

import time
from typing import Any
from uuid import UUID

import jwt
import pytest

from app.core.auth import (
    ALLOWED_ALGORITHMS,
    MAX_TOKEN_LENGTH,
    AuthenticatedUser,
    AuthenticationFailed,
    KeyUnavailable,
    RejectionReason,
    TokenVerifier,
)
from tests import auth_support as a

R = RejectionReason


@pytest.fixture
def keys() -> a.FakeKeys:
    return a.FakeKeys({a.KID: a.EC_KEY.public_key()})


@pytest.fixture
def verifier(keys: a.FakeKeys) -> TokenVerifier:
    return TokenVerifier(keys, issuer=a.ISSUER, audience=a.AUDIENCE)


def reason_for(verifier: TokenVerifier, token: str) -> RejectionReason:
    with pytest.raises(AuthenticationFailed) as caught:
        verifier.verify(token)
    return caught.value.reason


def test_valid_token_gives_the_subject_as_identity(verifier: TokenVerifier) -> None:
    user = verifier.verify(a.make_token())

    assert user == AuthenticatedUser(user_id=UUID(a.USER_ID))


def test_valid_rs256_token_is_accepted() -> None:
    keys = a.FakeKeys({a.KID: a.RSA_KEY.public_key()})
    verifier = TokenVerifier(keys, issuer=a.ISSUER, audience=a.AUDIENCE)

    token = a.make_token(key=a.RSA_KEY, algorithm="RS256")

    assert verifier.verify(token).user_id == UUID(a.USER_ID)


def test_identity_is_minimal_and_immutable(verifier: TokenVerifier) -> None:
    user = verifier.verify(a.make_token())

    assert [name for name in AuthenticatedUser.__slots__] == ["user_id"]
    assert a.SECRET_EMAIL not in repr(user)
    with pytest.raises(AttributeError):
        user.user_id = UUID(int=1)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("", R.MALFORMED_TOKEN),
        ("not-a-jwt", R.MALFORMED_TOKEN),
        ("a.b.c", R.MALFORMED_TOKEN),
        ("a" * (MAX_TOKEN_LENGTH + 1), R.MALFORMED_TOKEN),
    ],
)
def test_malformed_tokens_are_rejected(verifier, token, expected) -> None:
    assert reason_for(verifier, token) is expected


def test_expired_token_is_rejected(verifier: TokenVerifier) -> None:
    past = int(time.time()) - 3600

    assert reason_for(verifier, a.make_token(a.valid_claims(exp=past))) is R.EXPIRED


def test_token_expired_within_leeway_is_still_accepted(verifier: TokenVerifier) -> None:
    just_expired = int(time.time()) - 5

    assert verifier.verify(a.make_token(a.valid_claims(exp=just_expired)))


def test_token_not_yet_valid_is_rejected(verifier: TokenVerifier) -> None:
    future = int(time.time()) + 3600

    assert reason_for(verifier, a.make_token(a.valid_claims(nbf=future))) is R.NOT_YET_VALID


def test_token_signed_by_another_key_is_rejected(verifier: TokenVerifier) -> None:
    token = a.make_token(key=a.OTHER_EC_KEY)

    assert reason_for(verifier, token) is R.BAD_SIGNATURE


def test_tampered_payload_is_rejected(verifier: TokenVerifier) -> None:
    header, _, signature = a.make_token().split(".")
    other_payload = a.make_token(a.valid_claims(sub="99999999-2222-4333-8444-555555555555"))
    forged = ".".join([header, other_payload.split(".")[1], signature])

    assert reason_for(verifier, forged) is R.BAD_SIGNATURE


def test_wrong_issuer_is_rejected(verifier: TokenVerifier) -> None:
    token = a.make_token(a.valid_claims(iss="https://other.example.invalid/auth/v1"))

    assert reason_for(verifier, token) is R.BAD_ISSUER


def test_wrong_audience_is_rejected(verifier: TokenVerifier) -> None:
    assert reason_for(verifier, a.make_token(a.valid_claims(aud="anon"))) is R.BAD_AUDIENCE


@pytest.mark.parametrize("claim", ["exp", "sub", "iss", "aud"])
def test_missing_required_claim_is_rejected(verifier, claim) -> None:
    token = a.make_token(a.valid_claims(**{claim: a.MISSING}))

    assert reason_for(verifier, token) in {R.MISSING_CLAIM}


@pytest.mark.parametrize(
    "subject",
    [
        "",
        "not-a-uuid",
        "1" * 36,
        12345,
        None,
        ["x"],
        a.USER_ID.replace("-", ""),
        a.USER_ID + " ",
        "{" + a.USER_ID + "}",
        "urn:uuid:" + a.USER_ID,
        "1111222-24333-8444-5555-55555555555",  # 36 characters, hyphens misplaced
    ],
)
def test_invalid_subject_is_rejected(verifier, subject) -> None:
    token = a.make_token(a.valid_claims(sub=subject))

    assert reason_for(verifier, token) in {R.BAD_SUBJECT, R.MISSING_CLAIM, R.INVALID_TOKEN}


def test_uppercase_subject_is_accepted_as_the_same_identity(verifier: TokenVerifier) -> None:
    token = a.make_token(a.valid_claims(sub=a.USER_ID.upper()))

    assert verifier.verify(token).user_id == UUID(a.USER_ID)


@pytest.mark.parametrize("role", ["anon", "service_role", "", None, ["authenticated"]])
def test_roles_other_than_authenticated_are_rejected(verifier, role) -> None:
    token = a.make_token(a.valid_claims(role=role))

    assert reason_for(verifier, token) is R.BAD_ROLE


def test_missing_role_is_rejected(verifier: TokenVerifier) -> None:
    assert reason_for(verifier, a.make_token(a.valid_claims(role=a.MISSING))) is R.BAD_ROLE


@pytest.mark.parametrize("value", [True, "false", 0, None, a.MISSING])
def test_anything_but_an_explicit_false_anonymous_flag_is_rejected(verifier, value) -> None:
    token = a.make_token(a.valid_claims(is_anonymous=value))

    assert reason_for(verifier, token) is R.ANONYMOUS_USER


# --- header attacks --------------------------------------------------------------------


def test_unsigned_alg_none_token_is_rejected(verifier: TokenVerifier) -> None:
    token = jwt.encode(a.valid_claims(), None, algorithm="none", headers={"kid": a.KID})

    assert reason_for(verifier, token) is R.BAD_ALGORITHM


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_hmac_tokens_are_rejected(verifier, algorithm) -> None:
    # No shared-secret verification exists, whatever key the token claims to use.
    forged = jwt.encode(a.valid_claims(), "x" * 64, algorithm=algorithm, headers={"kid": a.KID})

    assert reason_for(verifier, forged) is R.BAD_ALGORITHM


def test_only_asymmetric_algorithms_are_allowed() -> None:
    assert set(ALLOWED_ALGORITHMS) == {"ES256", "RS256"}


def test_algorithm_that_does_not_match_the_key_type_is_rejected(verifier: TokenVerifier) -> None:
    # RS256 token, but the key held for this kid is an EC key.
    token = a.make_token(key=a.RSA_KEY, algorithm="RS256")

    assert reason_for(verifier, token) in {R.INVALID_TOKEN, R.BAD_ALGORITHM, R.BAD_SIGNATURE}


@pytest.mark.parametrize("kid", [a.MISSING, "", 123, ["x"], "k" * 200])
def test_missing_or_invalid_key_id_is_rejected(verifier, kid) -> None:
    header = {"alg": "ES256", "typ": "JWT", "kid": kid}
    token = a.raw_token({k: v for k, v in header.items() if v is not a.MISSING})

    # PyJWT itself refuses a non-string kid while reading the header; either way it is a 401.
    assert reason_for(verifier, token) in {R.BAD_KEY_ID, R.MALFORMED_TOKEN}


def test_unknown_key_id_is_rejected(verifier: TokenVerifier) -> None:
    assert reason_for(verifier, a.make_token(headers={"kid": "other"})) is R.UNKNOWN_KEY


@pytest.mark.parametrize("header", ["jku", "x5u"])
def test_key_location_headers_are_ignored(keys: a.FakeKeys, verifier, header) -> None:
    token = a.make_token(headers={header: "http://127.0.0.1:9/keys.json"})

    # Verified with the key we already hold; the URL is never used.
    assert verifier.verify(token)
    assert keys.calls == [a.KID]


def test_embedded_jwk_header_is_not_trusted(verifier: TokenVerifier) -> None:
    attacker_key = a.OTHER_EC_KEY
    token = a.make_token(key=attacker_key, headers={"jwk": a.public_jwk(attacker_key)})

    assert reason_for(verifier, token) is R.BAD_SIGNATURE


def test_unavailable_keys_are_reported_as_unavailable_not_as_a_bad_token() -> None:
    verifier = TokenVerifier(a.FakeKeys(unavailable=True), issuer=a.ISSUER, audience=a.AUDIENCE)

    with pytest.raises(KeyUnavailable):
        verifier.verify(a.make_token())


def test_rejections_carry_only_a_reason(verifier: TokenVerifier) -> None:
    token = a.make_token(a.valid_claims(aud="other"))

    with pytest.raises(AuthenticationFailed) as caught:
        verifier.verify(token)

    exc: Any = caught.value
    assert exc.args == ()
    assert exc.__cause__ is None
    assert a.SECRET_EMAIL not in str(exc) + repr(exc)
