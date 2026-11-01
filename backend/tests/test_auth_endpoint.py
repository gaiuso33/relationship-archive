"""The protected endpoint end to end, in both log formats.

Uses synthetic keys and tokens only. The key provider is a fake, so nothing here touches the
network or a Supabase project.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import configure_logging
from app.main import API_V1_PREFIX, create_app
from tests import auth_support as a

ME = f"{API_V1_PREFIX}/me"


@pytest.fixture
def keys() -> a.FakeKeys:
    return a.FakeKeys({a.KID: a.EC_KEY.public_key()})


def build_client(settings: Settings, log_stream: io.StringIO, keys=None) -> TestClient:
    application = create_app(settings, keys)
    configure_logging(settings, log_stream)
    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture
def settings(log_format: str) -> Settings:
    return a.auth_settings(log_level="DEBUG", log_format=log_format)


@pytest.fixture
def client(settings, log_stream, keys) -> TestClient:
    return build_client(settings, log_stream, keys)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_uniform_401(response) -> dict:
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    error = response.json()["error"]
    assert response.headers["X-Request-ID"] == error["request_id"]
    assert error["code"] == "unauthorized"
    assert error["message"] == "Authentication is required."
    assert set(error) == {"code", "message", "request_id"}
    return error


def test_valid_token_returns_only_the_user_id(client: TestClient) -> None:
    response = client.get(ME, headers=bearer(a.make_token()))

    assert response.status_code == 200
    assert response.json() == {"user_id": a.USER_ID}
    assert "X-Request-ID" in response.headers
    assert response.headers["Cache-Control"] == "no-store"


def test_scheme_is_case_insensitive(client: TestClient) -> None:
    response = client.get(ME, headers={"Authorization": f"bearer {a.make_token()}"})

    assert response.status_code == 200


def test_missing_authorization_header_is_rejected(client: TestClient) -> None:
    assert_uniform_401(client.get(ME))


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "Token abc.def.ghi",
        "abc.def.ghi",
        "Bearer  two.spaces.here",
        "Bearer one two",
        "Bearer a.b.c ",
    ],
)
def test_malformed_authorization_headers_are_rejected(client, header) -> None:
    assert_uniform_401(client.get(ME, headers={"Authorization": header}))


def test_duplicate_authorization_headers_are_rejected(client: TestClient) -> None:
    token = a.make_token()
    headers = [("Authorization", f"Bearer {token}"), ("Authorization", f"Bearer {token}")]

    assert_uniform_401(client.get(ME, headers=headers))


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: "not.a.jwt",
        lambda: a.make_token(a.valid_claims(exp=1)),
        lambda: a.make_token(key=a.OTHER_EC_KEY),
        lambda: a.make_token(a.valid_claims(iss="https://other.example.invalid/auth/v1")),
        lambda: a.make_token(a.valid_claims(aud="anon")),
        lambda: a.make_token(a.valid_claims(sub="not-a-uuid")),
        lambda: a.make_token(a.valid_claims(sub=a.MISSING)),
        lambda: a.make_token(a.valid_claims(role="service_role")),
        lambda: a.make_token(a.valid_claims(is_anonymous=True)),
        lambda: a.make_token(headers={"kid": "unknown"}),
    ],
)
def test_every_kind_of_bad_token_gets_the_same_401(client, token_factory) -> None:
    first = assert_uniform_401(client.get(ME, headers=bearer(token_factory())))
    second = assert_uniform_401(client.get(ME))

    assert {k: v for k, v in first.items() if k != "request_id"} == {
        k: v for k, v in second.items() if k != "request_id"
    }


def test_identity_comes_only_from_the_verified_token(client: TestClient) -> None:
    other = "99999999-2222-4333-8444-555555555555"
    response = client.get(
        f"{ME}?user_id={other}&sub={other}",
        headers={**bearer(a.make_token()), "X-User-Id": other, "X-Forwarded-User": other},
    )

    assert response.json() == {"user_id": a.USER_ID}


def test_user_id_in_query_body_or_headers_is_not_authentication(client: TestClient) -> None:
    other = "99999999-2222-4333-8444-555555555555"

    for response in (
        client.get(f"{ME}?user_id={other}"),
        client.get(ME, headers={"X-User-Id": other, "X-Forwarded-User": other}),
        client.request("GET", ME, json={"user_id": other}),
    ):
        assert_uniform_401(response)


def test_token_in_query_string_or_cookie_is_ignored(client: TestClient) -> None:
    token = a.make_token()

    assert_uniform_401(client.get(f"{ME}?access_token={token}"))
    assert_uniform_401(client.get(ME, headers={"Cookie": f"sb-access-token={token}"}))


# --- privacy ---------------------------------------------------------------------------


def secrets_in(*texts: str, token: str) -> list[str]:
    values = [token, token.split(".")[1], a.SECRET_EMAIL, a.SECRET_NAME, a.USER_ID]
    return [v for v in values for t in texts if v in t]


def test_failures_leak_nothing_to_responses_or_logs(client, log_stream) -> None:
    claims = a.valid_claims(aud="anon")
    token = a.make_token(claims)

    response = client.get(f"{ME}?access_token={token}", headers=bearer(token))

    assert_uniform_401(response)
    output = log_stream.getvalue()
    assert "auth_rejected_bad_audience" in output
    assert secrets_in(output, response.text, str(dict(response.headers)), token=token) == []
    assert "Bearer" not in output
    assert "authorization" not in output.lower()


def test_success_leaks_no_token_or_claims_to_logs(client, log_stream) -> None:
    token = a.make_token()

    response = client.get(ME, headers=bearer(token))

    assert response.status_code == 200
    output = log_stream.getvalue()
    leaked = secrets_in(output, token=token)
    assert leaked == []
    assert "request_completed" in output


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer a b"])
def test_malformed_header_value_is_not_logged(client, log_stream, header) -> None:
    client.get(ME, headers={"Authorization": header or "x"})

    output = log_stream.getvalue()
    assert "auth_rejected_malformed_authorization" in output
    assert "Basic abc" not in output


def test_expired_token_is_logged_by_reason_only(client, log_stream) -> None:
    token = a.make_token(a.valid_claims(exp=1))

    client.get(ME, headers=bearer(token))

    output = log_stream.getvalue()
    assert "auth_rejected_expired" in output
    assert secrets_in(output, token=token) == []


# --- verification infrastructure -------------------------------------------------------


def test_unavailable_keys_return_a_privacy_safe_503(settings, log_stream) -> None:
    client = build_client(settings, log_stream, a.FakeKeys(unavailable=True))
    token = a.make_token()

    response = client.get(ME, headers=bearer(token))

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "service_unavailable"
    assert "WWW-Authenticate" not in response.headers
    assert response.headers["X-Request-ID"] == error["request_id"]
    output = log_stream.getvalue()
    assert "auth_keys_unavailable" in output
    assert secrets_in(output, response.text, token=token) == []


def test_unavailable_keys_never_let_a_request_through(settings, log_stream) -> None:
    client = build_client(settings, log_stream, a.FakeKeys(unavailable=True))

    for headers in ({}, bearer("garbage"), bearer(a.make_token())):
        assert client.get(ME, headers=headers).status_code in {401, 503}
    assert client.get(ME, headers=bearer(a.make_token())).status_code == 503


def test_missing_configuration_does_not_break_startup_and_fails_safely(
    log_format, log_stream
) -> None:
    settings = a.auth_settings(supabase_url=None, log_format=log_format)
    client = build_client(settings, log_stream)

    assert client.get(f"{API_V1_PREFIX}/health").status_code == 200
    response = client.get(ME, headers=bearer(a.make_token()))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "auth_not_configured" in log_stream.getvalue()


def test_the_endpoint_is_documented_with_its_error_responses(client: TestClient) -> None:
    operation = client.app.openapi()["paths"][ME]["get"]

    assert {"200", "401", "503"} <= set(operation["responses"])
