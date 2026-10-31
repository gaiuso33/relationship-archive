"""Request-level privacy tests, in both log formats.

Each test sends synthetic tokens, emails, names, note text and query values, then checks
that none of them appears in the captured logs or in any response (body or headers).
"""

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import configure_logging
from app.main import API_V1_PREFIX, create_app
from tests import support

PROBE = f"{API_V1_PREFIX}/probe"


@pytest.fixture
def client(test_settings: Settings, log_stream: io.StringIO) -> TestClient:
    application = create_app(test_settings)
    application.include_router(support.build_probe_router(), prefix=API_V1_PREFIX)
    # create_app installs the stderr handler; point logging at the capture stream instead.
    configure_logging(test_settings, log_stream)
    return TestClient(application, raise_server_exceptions=False)


def assert_nothing_sensitive(log_stream: io.StringIO, *responses) -> None:
    haystacks = [("logs", log_stream.getvalue())]
    for index, response in enumerate(responses):
        haystacks.append((f"response {index} body", response.text))
        haystacks.append((f"response {index} headers", str(dict(response.headers))))
    for where, text in haystacks:
        for value in support.ALL_SENSITIVE:
            assert value not in text, f"{value!r} leaked into {where}"


def error_of(response) -> dict:
    return response.json()["error"]


def test_request_with_token_query_and_body_leaks_nothing(client, log_stream) -> None:
    ok = client.post(
        f"{PROBE}/notes?token={support.QUERY_VALUE}&email={support.EMAIL}",
        json={"text": support.NOTE, "rating": 5},
        headers=support.AUTH_HEADERS,
    )
    person = client.get(f"{PROBE}/people/{support.PATH_ID}", headers=support.AUTH_HEADERS)

    assert ok.status_code == 200
    assert person.status_code == 200
    assert_nothing_sensitive(log_stream, ok, person)


def test_access_log_has_route_template_status_and_duration_only(client, log_stream) -> None:
    client.get(f"{PROBE}/people/{support.PATH_ID}?q={support.QUERY_VALUE}")

    output = log_stream.getvalue()
    assert f"{PROBE}/people/{{person_id}}" in output
    assert "request_completed" in output
    assert "duration_ms" in output
    assert "status" in output
    assert support.PATH_ID not in output
    assert support.QUERY_VALUE not in output
    for forbidden in ("testclient", "127.0.0.1", "user-agent", "python-httpx"):
        assert forbidden not in output.lower()


def test_unmatched_path_is_logged_without_its_raw_path(client, log_stream) -> None:
    response = client.get(f"{API_V1_PREFIX}/{support.PATH_ID}/{support.NAME}")

    assert response.status_code == 404
    assert "unmatched" in log_stream.getvalue()
    assert_nothing_sensitive(log_stream, response)


def test_one_access_entry_per_request(client, log_stream) -> None:
    client.get(f"{PROBE}/people/a")
    client.get(f"{PROBE}/people/b")

    assert log_stream.getvalue().count("request_completed") == 2


# --- request ids -------------------------------------------------------------------------


def test_request_id_is_generated_by_the_server_and_client_value_is_ignored(
    client, log_stream
) -> None:
    chosen = "client-chosen-id-SYNTHETIC\ninjected"
    response = client.get(f"{API_V1_PREFIX}/health", headers={"X-Request-ID": chosen})

    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert request_id != chosen
    assert "SYNTHETIC" not in log_stream.getvalue()
    assert request_id in log_stream.getvalue()


def test_request_ids_are_unique_per_request(client) -> None:
    ids = {client.get(f"{API_V1_PREFIX}/health").headers["x-request-id"] for _ in range(5)}

    assert len(ids) == 5


def test_request_id_matches_between_header_error_body_and_logs(client, log_stream) -> None:
    response = client.get(f"{PROBE}/boom", headers=support.AUTH_HEADERS)

    request_id = response.headers["x-request-id"]
    assert error_of(response)["request_id"] == request_id
    assert log_stream.getvalue().count(request_id) >= 2  # error entry and access entry


# --- unhandled errors (500) ----------------------------------------------------------------


def test_unhandled_error_returns_generic_body_and_leaks_nothing(client, log_stream) -> None:
    response = client.get(f"{PROBE}/boom?note={support.NOTE}", headers=support.AUTH_HEADERS)

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "request_id": response.headers["x-request-id"],
        }
    }
    assert_nothing_sensitive(log_stream, response)
    output = log_stream.getvalue()
    assert "unhandled_error" in output
    assert "builtins.RuntimeError" in output
    assert "error_id" in output
    assert "Traceback" not in output


# --- validation errors (422) ---------------------------------------------------------------


def test_validation_error_returns_only_location_and_type(client, log_stream) -> None:
    response = client.post(
        f"{PROBE}/notes",
        json={"text": support.NOTE, "rating": f"not-a-number {support.NAME}"},
        headers=support.AUTH_HEADERS,
    )

    assert response.status_code == 422
    error = error_of(response)
    assert error["code"] == "validation_error"
    assert error["fields"] == [{"loc": ["body", "rating"], "type": "int_parsing"}]
    for field in error["fields"]:
        assert set(field) == {"loc", "type"}
    assert "input" not in response.text
    assert_nothing_sensitive(log_stream, response)


def test_validation_error_for_missing_field_and_wrong_type(client, log_stream) -> None:
    response = client.post(f"{PROBE}/notes", json={"text": 123})

    assert response.status_code == 422
    assert {tuple(f["loc"]) for f in error_of(response)["fields"]} == {
        ("body", "text"),
        ("body", "rating"),
    }


def test_unexpected_field_name_chosen_by_the_client_is_not_echoed(client, log_stream) -> None:
    response = client.post(
        f"{PROBE}/strict", json={"text": "ok", support.EXTRA_FIELD: support.NOTE}
    )

    assert response.status_code == 422
    assert error_of(response)["fields"] == [
        {"loc": ["body", "<unknown field>"], "type": "extra_forbidden"}
    ]
    assert_nothing_sensitive(log_stream, response)


def test_malformed_json_body_is_not_echoed(client, log_stream) -> None:
    response = client.post(
        f"{PROBE}/notes",
        content=f'{{"text": "{support.NOTE}", "rating": }}',
        headers={"Content-Type": "application/json", **support.AUTH_HEADERS},
    )

    assert response.status_code == 422
    assert_nothing_sensitive(log_stream, response)


# --- HTTPException and routing errors ------------------------------------------------------


def test_http_exception_detail_is_never_sent_or_logged(client, log_stream) -> None:
    response = client.get(f"{PROBE}/http-400", headers=support.AUTH_HEADERS)

    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"
    assert error_of(response)["message"] == "The request could not be understood."
    assert_nothing_sensitive(log_stream, response)


def test_server_side_http_exception_keeps_its_status_but_not_its_detail(client, log_stream) -> None:
    response = client.get(f"{PROBE}/http-503")

    assert response.status_code == 503
    assert error_of(response)["code"] == "internal_error"
    assert_nothing_sensitive(log_stream, response)


def test_not_found_and_method_not_allowed_use_the_common_format(client, log_stream) -> None:
    missing = client.get(f"{API_V1_PREFIX}/does-not-exist")
    wrong_method = client.post(f"{API_V1_PREFIX}/health")

    assert missing.status_code == 404
    assert error_of(missing)["code"] == "not_found"
    assert wrong_method.status_code == 405
    assert error_of(wrong_method)["code"] == "method_not_allowed"
    assert error_of(wrong_method)["request_id"] == wrong_method.headers["x-request-id"]


# --- AppError ------------------------------------------------------------------------------


def test_app_error_returns_only_its_approved_message(client, log_stream) -> None:
    response = client.get(f"{PROBE}/app-error", headers=support.AUTH_HEADERS)

    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"
    assert error_of(response)["message"] == "The requested resource was not found."
    assert_nothing_sensitive(log_stream, response)


def test_app_error_cannot_carry_a_free_text_message() -> None:
    with pytest.raises(TypeError):
        AppError(ErrorCode.NOT_FOUND, support.NOTE)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        AppError(support.NOTE)  # type: ignore[arg-type]
    assert AppError(ErrorCode.CONFLICT).args == ()


def test_every_approved_error_has_a_fixed_message() -> None:
    from app.core.errors import APPROVED

    assert set(APPROVED) <= set(ErrorCode)
    for status, message in APPROVED.values():
        assert 400 <= status <= 599
        assert message.endswith(".")


# --- misc ----------------------------------------------------------------------------------


def test_health_endpoint_is_unchanged(client) -> None:
    response = client.get(f"{API_V1_PREFIX}/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_describes_the_error_format(client) -> None:
    schema = client.get("/openapi.json").json()

    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "422" in schema["paths"][f"{PROBE}/notes"]["post"]["responses"]


def test_lifespan_applies_logging_again(test_settings: Settings, log_stream) -> None:
    import logging

    application = create_app(test_settings)
    with TestClient(application):
        handlers = logging.getLogger().handlers
        marked = [h for h in handlers if getattr(h, "_relationship_archive_handler", False)]
        assert len(marked) == 1


def test_json_log_lines_stay_valid_json_for_every_response(log_stream) -> None:
    settings = Settings(_env_file=None, log_level="DEBUG", log_format="json")
    application = create_app(settings)
    application.include_router(support.build_probe_router(), prefix=API_V1_PREFIX)
    configure_logging(settings, log_stream)
    client = TestClient(application, raise_server_exceptions=False)

    client.get(f"{PROBE}/boom")
    client.get(f"{PROBE}/http-400")
    client.post(f"{PROBE}/notes", json={})

    lines = log_stream.getvalue().splitlines()
    assert lines
    for line in lines:
        json.loads(line)


def test_only_http_semantic_headers_pass_through_from_http_exception(client, log_stream) -> None:
    response = client.get(f"{PROBE}/http-401")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "x-debug" not in response.headers
    assert support.NOTE not in str(response.headers)
    assert_nothing_sensitive(log_stream, response)


def test_app_error_rejects_codes_without_an_approved_message() -> None:
    with pytest.raises(ValueError):
        AppError(ErrorCode.HTTP_ERROR)
    with pytest.raises(ValueError):
        AppError("a free-text message")  # type: ignore[arg-type]


class FakeRoute:
    def __init__(self, path_format: str) -> None:
        self.path_format = path_format


@pytest.mark.parametrize(
    ("raw_path", "route_format", "params", "expected"),
    [
        ("/api/v1/health", "/health", {}, "/api/v1/health"),
        ("/api/v1/health", "/api/v1/health", {}, "/api/v1/health"),
        (
            "/api/v1/people/abc",
            "/people/{person_id}",
            {"person_id": "abc"},
            "/api/v1/people/{person_id}",
        ),
        (
            "/api/v1/people/p1/notes/n2",
            "/people/{person_id}/notes/{note_id}",
            {"person_id": "p1", "note_id": "n2"},
            "/api/v1/people/{person_id}/notes/{note_id}",
        ),
        # A value equal to a static segment must not corrupt the template.
        (
            "/api/v1/people/v1",
            "/people/{person_id}",
            {"person_id": "v1"},
            "/api/v1/people/{person_id}",
        ),
        ("/files/a/b/c.txt", "/files/{rest}", {"rest": "a/b/c.txt"}, "/files/{rest}"),
        # If the raw path does not fit the route, only the route's own template is used.
        (
            "/somewhere/else/entirely",
            "/people/{person_id}",
            {"person_id": "x"},
            "/people/{person_id}",
        ),
        # A prefix that does not look static is never copied from the raw path.
        (
            "/Alexandra-Syntheticperson-1234567890123456789012345/people/x",
            "/people/{person_id}",
            {"person_id": "x"},
            "/people/{person_id}",
        ),
    ],
)
def test_route_template_comes_from_the_route_not_the_raw_path(
    raw_path, route_format, params, expected
) -> None:
    from app.core.middleware import route_template

    scope = {"path": raw_path, "path_params": params, "route": FakeRoute(route_format)}

    assert route_template(scope) == expected


def test_route_template_for_unmatched_request_has_no_path() -> None:
    from app.core.middleware import UNMATCHED_ROUTE, route_template

    assert route_template({"path": f"/{support.PATH_ID}", "path_params": {}}) == UNMATCHED_ROUTE
