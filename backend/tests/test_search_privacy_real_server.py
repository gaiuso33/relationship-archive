"""People search through a real uvicorn process: what it returns and what it prints.

`test_people_search_api.py` drives the real app in process, and `test_privacy_real_server.py`
runs a real server with probe routes only. This module joins them (#74): the unmodified
`app.main:app` in a real uvicorn process, its real middleware and logging, real authentication
and a real database, reaching the actual `POST /api/v1/people/search` handler.

Authentication uses locally generated keys. The test process signs synthetic tokens with a
private key that never leaves it, and serves only the public key from a loopback JWKS endpoint,
which the backend accepts as its Supabase URL in test mode (plain http on loopback only). No
Supabase project, network or real credential is involved.

Everything printed by the server is read after it stops, and must contain none of the private
text. The same is checked in every error body. Both log formats are exercised.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from tests import auth_support as a
from tests.test_privacy_real_server import BACKEND_DIR, free_port, request

SEARCH = "/api/v1/people/search"
PEOPLE = "/api/v1/people"

# Synthetic and unmistakable. The search text is part of both users' names on purpose.
USER_A = "7a74a74a-0000-4000-8000-00000000000a"
USER_B = "7a74a74a-0000-4000-8000-00000000000b"
MARK = "SyntheticSearchMarkerQ74"
NAME_A = f"Corvin {MARK} Alderwick"
NAME_B = f"Marisol {MARK} Birchfield"
INTEREST = "SyntheticInterestMarker74"
NOTE = "SyntheticNoteMarker74"
BEARER_MARK = "SyntheticBearerMarker74"
CLIENT_REQUEST_ID = "client-chosen-id-74"
MISSING_SCHEMA = "synthetic_schema_that_does_not_exist_74"

# Private text that must never reach the server's output or an error body.
PRIVATE_TEXT = (MARK, NAME_A, NAME_B, "Alderwick", "Birchfield", INTEREST, NOTE, BEARER_MARK)
# What a leaked statement, parameter list or traceback would look like.
FORBIDDEN_OUTPUT = (
    "Traceback",
    "Exception in ASGI application",
    "uvicorn.access",
    "HTTP/1.1",  # the raw request line that uvicorn's own access log would print
    "ILIKE",
    "SELECT",
    "[SQL",
    "[parameters",
    "display_name",
    MISSING_SCHEMA,
    CLIENT_REQUEST_ID,
)


class _Keys(BaseHTTPRequestHandler):
    """Serves only the public half of the test key, where Supabase would publish its keys."""

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path == "/auth/v1/.well-known/jwks.json":
            body = json.dumps({"keys": [a.public_jwk(a.EC_KEY)]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def project_url() -> Iterator[str]:
    keys = ThreadingHTTPServer(("127.0.0.1", 0), _Keys)
    thread = threading.Thread(target=keys.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{keys.server_address[1]}"
    finally:
        keys.shutdown()
        keys.server_close()
        thread.join(timeout=5)


def token(project_url: str, user_id: str) -> dict[str, str]:
    claims = a.valid_claims(iss=f"{project_url}/auth/v1", sub=user_id)
    return {"Authorization": f"Bearer {a.make_token(claims)}"}


def database_url_for_tests() -> str:
    url = get_settings().test_database_url
    assert url is not None, "TEST_DATABASE_URL is required"
    return url.get_secret_value()


@contextmanager
def real_server(
    project_url: str, log_format: str, tmp_path: Path, *, database_url: str
) -> Iterator[tuple[int, Callable[[], str]]]:
    """Run the real application. Yields the port and a function that stops it and returns
    everything it printed."""
    port = free_port()
    output_path = tmp_path / f"server-output-{port}.txt"
    env = {
        **os.environ,
        "APP_ENV": "test",
        "LOG_FORMAT": log_format,
        "LOG_LEVEL": "DEBUG",
        "SUPABASE_URL": project_url,
        "DATABASE_URL": database_url,
        "PYTHONUNBUFFERED": "1",
    }
    with output_path.open("w") as output:
        process = subprocess.Popen(  # noqa: S603
            # The real entry point and the plain command: no probe router, no log options.
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            cwd=BACKEND_DIR,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        stopped = False

        def stop_and_read() -> str:
            nonlocal stopped
            if not stopped:
                stopped = True
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            return output_path.read_text()

        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    if request(port, "GET", "/api/v1/health")[0] == 200:
                        break
                except OSError:
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("test server did not start: " + output_path.read_text()[-500:])
                time.sleep(0.1)
            yield port, stop_and_read
        finally:
            stop_and_read()


def call(
    port: int, method: str, path: str, headers: dict[str, str], payload: object = None, raw=None
) -> tuple[int, dict[str, str], str]:
    all_headers = {**headers, "X-Request-ID": CLIENT_REQUEST_ID}
    if payload is not None or raw is not None:
        all_headers["Content-Type"] = "application/json"
        body = raw if raw is not None else json.dumps(payload)
        return request(port, method, path, body=body, headers=all_headers)
    return request(port, method, path, headers=all_headers)


def assert_safe_error(status: int, headers: dict[str, str], body: str, expected: int) -> dict:
    """An error answer: the right status, the one common shape, a server-made request id."""
    assert status == expected, body
    error = json.loads(body)["error"]
    assert set(error) <= {"code", "message", "request_id", "fields"}
    assert error["request_id"] == headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", error["request_id"])
    assert headers["x-request-id"] != CLIENT_REQUEST_ID
    for value in PRIVATE_TEXT:
        assert value not in body, "private text in an error body"
        assert quote(value) not in body
    return error


def assert_clean_output(server_output: str, path_logged: bool = True) -> None:
    assert server_output.strip(), "the server printed nothing, so there is nothing to check"
    for value in PRIVATE_TEXT:
        assert value not in server_output, f"{value!r} found in server output"
        assert quote(value) not in server_output
    for value in FORBIDDEN_OUTPUT:
        assert value not in server_output, f"{value!r} found in server output"
    if path_logged:
        # The request was logged by its route template, which shows the real route ran.
        assert SEARCH in server_output


def forget_users(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("delete from people where owner_id in (:a, :b)"), {"a": USER_A, "b": USER_B}
        )


@pytest.fixture
def clean_database(migrated_engine: Engine) -> Iterator[Engine]:
    forget_users(migrated_engine)
    yield migrated_engine
    forget_users(migrated_engine)


def test_search_through_the_real_server_never_exposes_private_text(
    project_url: str, log_format: str, tmp_path: Path, clean_database: Engine
) -> None:
    auth_a, auth_b = token(project_url, USER_A), token(project_url, USER_B)
    with real_server(project_url, log_format, tmp_path, database_url=database_url_for_tests()) as (
        port,
        stop_and_read,
    ):
        # Two users whose names both contain the search text.
        status, _, body = call(
            port,
            "POST",
            PEOPLE,
            auth_a,
            {"display_name": NAME_A, "interests": [INTEREST], "preferences": [INTEREST]},
        )
        assert status == 201, body
        person_a = json.loads(body)
        status, _, body = call(
            port, "POST", f"{PEOPLE}/{person_a['id']}/notes", auth_a, {"content": NOTE}
        )
        assert status == 201, body
        status, _, body = call(port, "POST", PEOPLE, auth_b, {"display_name": NAME_B})
        assert status == 201, body

        # A match: only the caller's own person, and nothing is reflected beyond it.
        status, headers, body = call(port, "POST", SEARCH, auth_a, {"query": MARK})
        assert status == 200
        assert [item["display_name"] for item in json.loads(body)["items"]] == [NAME_A]
        assert NAME_B not in body and "Birchfield" not in body
        assert NOTE not in body
        assert headers["x-request-id"] != CLIENT_REQUEST_ID
        status, _, body = call(port, "POST", SEARCH, auth_b, {"query": MARK.lower()})
        assert [item["display_name"] for item in json.loads(body)["items"]] == [NAME_B]

        # Names only: the interest and note markers are not searchable.
        for hidden in (INTEREST, NOTE):
            status, _, body = call(port, "POST", SEARCH, auth_a, {"query": hidden})
            assert (status, json.loads(body)) == (200, {"items": []})
        # No match.
        status, _, body = call(port, "POST", SEARCH, auth_a, {"query": MARK + "-no-match"})
        assert (status, json.loads(body)) == (200, {"items": []})

        failures = [
            # Validation errors: a control character, an extra field, wrong type, too long.
            (call(port, "POST", SEARCH, auth_a, {"query": MARK + "\u0000"}), 422),
            (call(port, "POST", SEARCH, auth_a, {"query": MARK, "extra": MARK}), 422),
            (call(port, "POST", SEARCH, auth_a, {"query": [MARK]}), 422),
            (call(port, "POST", SEARCH, auth_a, {"query": MARK * 20}), 422),
            (call(port, "POST", SEARCH, auth_a, {"query": "   "}), 422),
            # An unreadable body that still contains the search text.
            (call(port, "POST", SEARCH, auth_a, raw=f'{{"query": "{MARK}", }}'), 422),
            (call(port, "POST", SEARCH, auth_a, raw=MARK), 422),
            # Authentication failures: no token, an invalid token that holds a marker, a
            # token signed by another key, an expired token.
            (call(port, "POST", SEARCH, {}, {"query": MARK}), 401),
            (
                call(
                    port,
                    "POST",
                    SEARCH,
                    {"Authorization": f"Bearer {BEARER_MARK}"},
                    {"query": MARK},
                ),
                401,
            ),
            (
                call(
                    port,
                    "POST",
                    SEARCH,
                    {
                        "Authorization": "Bearer "
                        + a.make_token(a.valid_claims(), key=a.OTHER_EC_KEY)
                    },
                    {"query": MARK},
                ),
                401,
            ),
            (
                call(
                    port,
                    "POST",
                    SEARCH,
                    {
                        "Authorization": "Bearer "
                        + a.make_token(
                            a.valid_claims(iss=f"{project_url}/auth/v1", exp=int(time.time()) - 60)
                        )
                    },
                    {"query": MARK},
                ),
                401,
            ),
            # The wrong method on the search path, with the text in the URL. GET reaches the
            # person route ("search" is not an identifier), so this is a validation error.
            (call(port, "GET", f"{SEARCH}?query={MARK}", auth_a), 422),
        ]
        for (status, headers, body), expected in failures:
            assert_safe_error(status, headers, body, expected)

        # Clean up through the real API.
        for headers in (auth_a, auth_b):
            for item in json.loads(call(port, "GET", PEOPLE, headers)[2])["items"]:
                assert call(port, "DELETE", f"{PEOPLE}/{item['id']}", headers)[0] in (200, 204)
        server_output = stop_and_read()

    assert_clean_output(server_output)


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        # An unexpected database error raised by the search statement itself.
        ("table-missing", 500, "internal_error"),
        # The database cannot be reached at all.
        ("database-unreachable", 503, "service_unavailable"),
    ],
)
def test_a_database_failure_in_the_search_statement_is_safe(
    project_url: str, log_format: str, tmp_path: Path, failure: str, status: int, code: str
) -> None:
    """The statement carries the search text as a bound parameter. A database error raised by
    it (here: the table cannot be found, or the connection fails) must not expose the text,
    the SQL or the parameters to the client or to the server's output."""
    url = make_url(database_url_for_tests())
    if failure == "table-missing":
        url = url.update_query_dict({"options": f"-csearch_path={MISSING_SCHEMA}"})
    else:
        url = url.set(host="127.0.0.1", port=1)  # nothing listens here
    database_url = url.render_as_string(hide_password=False)

    with real_server(project_url, log_format, tmp_path, database_url=database_url) as (
        port,
        stop_and_read,
    ):
        answer = call(port, "POST", SEARCH, token(project_url, USER_A), {"query": MARK})
        error = assert_safe_error(*answer, status)
        assert error["code"] == code
        server_output = stop_and_read()

    assert_clean_output(server_output)
