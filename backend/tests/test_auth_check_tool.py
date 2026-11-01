"""The owner's local check tool must never print the token or any personal claim."""

import json

import pytest

from app.tools import check_token
from tests import auth_support as a


def run_tool(token: str, keys: a.FakeKeys, body: bytes | None = None) -> tuple[int, str]:
    lines: list[str] = []
    jwks = body or json.dumps({"keys": [a.public_jwk(a.EC_KEY)]}).encode()
    code = check_token.run(
        a.auth_settings(),
        read_token=lambda prompt: token,
        keys=keys,
        fetch=lambda url, timeout, limit: jwks,
        out=lines.append,
    )
    return code, "\n".join(lines)


def test_a_good_token_is_accepted_and_nothing_personal_is_printed() -> None:
    token = a.make_token()
    code, output = run_tool(token, a.FakeKeys({a.KID: a.EC_KEY.public_key()}))

    assert code == 0
    assert "would ACCEPT" in output
    assert "alg: ES256" in output
    for secret in (token, token.split(".")[1], a.SECRET_EMAIL, a.SECRET_NAME, a.USER_ID):
        assert secret not in output


def test_a_rejected_token_reports_only_the_reason() -> None:
    token = a.make_token(a.valid_claims(aud="anon"))
    code, output = run_tool(token, a.FakeKeys({a.KID: a.EC_KEY.public_key()}))

    assert code == 1
    assert "REJECT this token: bad_audience" in output
    assert a.SECRET_EMAIL not in output
    assert a.USER_ID not in output


def test_a_project_without_signing_keys_is_explained() -> None:
    token = a.make_token()
    code, output = run_tool(token, a.FakeKeys(unavailable=True), body=b'{"keys": []}')

    assert code == 2
    assert "legacy JWT secret" in output


def test_unreadable_input_is_reported_without_echoing_it() -> None:
    code, output = run_tool("SYNTHETIC-NOT-A-TOKEN-4242", a.FakeKeys({}))

    assert code == 1
    assert "SYNTHETIC-NOT-A-TOKEN-4242" not in output


def test_unusual_values_are_not_shown() -> None:
    token = a.make_token(a.valid_claims(role=f"role {a.SECRET_EMAIL}"))
    _, output = run_tool(token, a.FakeKeys({a.KID: a.EC_KEY.public_key()}))

    assert a.SECRET_EMAIL not in output
    assert "unusual value not shown" in output


@pytest.mark.parametrize("value", [None, True, 5, "plain-value", "has space"])
def test_plain_helper(value) -> None:
    shown = check_token._plain(value)

    assert " " not in shown or shown.startswith("<")
