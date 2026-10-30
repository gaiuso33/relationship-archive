"""Privacy checks against a real uvicorn process.

FastAPI's TestClient does not run uvicorn, so it cannot show what uvicorn itself writes
(its access log with query strings and client addresses, and the traceback it prints for an
unhandled exception). This test starts the real server as a subprocess and reads
everything it prints.
"""

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

from tests import support

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROBE = "/api/v1/probe"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(port: int, method: str, path: str, **kwargs) -> tuple[int, dict[str, str], str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, **kwargs)
        response = connection.getresponse()
        body = response.read().decode()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    finally:
        connection.close()


@pytest.fixture
def server(log_format: str, tmp_path: Path):
    port = free_port()
    output_path = tmp_path / "server-output.txt"
    env = {
        **os.environ,
        "LOG_FORMAT": log_format,
        "LOG_LEVEL": "DEBUG",
        "PYTHONUNBUFFERED": "1",
    }
    with output_path.open("w") as output:
        process = subprocess.Popen(  # noqa: S603
            # Deliberately the plain command: no --no-access-log and no --log-config.
            [sys.executable, "-m", "uvicorn", "tests.support_app:app", "--port", str(port)],
            cwd=BACKEND_DIR,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
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
            yield port, output_path, log_format
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_real_uvicorn_never_logs_or_returns_synthetic_sensitive_values(server) -> None:
    port, output_path, log_format = server
    auth = {"Authorization": f"Bearer {support.BEARER_VALUE}", "X-Request-ID": "client-chosen-id"}
    json_headers = {**auth, "Content-Type": "application/json"}
    query = f"?token={support.QUERY_VALUE}&email={support.EMAIL}&name={quote(support.NAME)}"

    responses = [
        request(port, "GET", f"{PROBE}/people/{support.PATH_ID}{query}", headers=auth),
        request(port, "GET", f"{PROBE}/boom{query}", headers=auth),
        request(port, "GET", f"{PROBE}/http-400", headers=auth),
        request(port, "GET", f"{PROBE}/http-503", headers=auth),
        request(
            port,
            "POST",
            f"{PROBE}/notes",
            body=json.dumps({"text": support.NOTE, "rating": support.NAME}),
            headers=json_headers,
        ),
        request(
            port,
            "POST",
            f"{PROBE}/strict",
            body=json.dumps({"text": "ok", support.EXTRA_FIELD: support.NOTE}),
            headers=json_headers,
        ),
        request(
            port,
            "POST",
            f"{PROBE}/notes",
            body=f'{{"text": "{support.NOTE}", "rating": }}',
            headers=json_headers,
        ),
        request(
            port,
            "GET",
            f"/api/v1/{support.PATH_ID}/{quote(support.NAME)}{query}",
            headers=auth,
        ),
        request(port, "POST", f"/api/v1/health{query}", headers=auth),
    ]

    # Responses: the right statuses, one common error shape, a server-made request id.
    assert [status for status, _, _ in responses] == [200, 500, 400, 503, 422, 422, 422, 404, 405]
    for status, headers, body in responses:
        assert headers["x-request-id"] != "client-chosen-id"
        assert re.fullmatch(r"[0-9a-f]{32}", headers["x-request-id"])
        if status >= 400:
            assert json.loads(body)["error"]["request_id"] == headers["x-request-id"]

    # Stop the server so every line has been flushed before reading its output.
    request(port, "GET", "/api/v1/health")
    time.sleep(0.3)
    server_output = output_path.read_text()
    request_ids = {headers["x-request-id"] for _, headers, _ in responses}

    for value in support.ALL_SENSITIVE:
        assert value not in server_output, f"{value!r} found in server output"
        assert quote(value) not in server_output, f"{value!r} (URL-encoded) in server output"
        for _, headers, body in responses:
            assert value not in body
            assert value not in str(headers)
    assert "client-chosen-id" not in server_output
    assert "Traceback" not in server_output
    assert "Exception in ASGI application" not in server_output
    assert "uvicorn.access" not in server_output
    # Frame paths reveal nothing about the machine: no home directory, no virtualenv, no
    # checkout location.
    assert str(BACKEND_DIR) not in server_output
    assert str(BACKEND_DIR.parent) not in server_output
    assert "site-packages" not in server_output
    assert "/home/" not in server_output and "/tmp/" not in server_output
    assert "Authorization" not in server_output and "Bearer" not in server_output
    assert not re.search(r"127\.0\.0\.1:\d+ - ", server_output), "client address logged"
    assert "HTTP/1.1" not in server_output, "raw request line logged"
    assert "/api/v1/probe/people/SYNTHETIC" not in server_output

    # Operational information is still there: route templates, statuses, request ids.
    assert f"{PROBE}/people/{{person_id}}" in server_output
    assert "unmatched" in server_output
    assert "unhandled_error" in server_output
    assert "builtins.RuntimeError" in server_output
    for request_id in request_ids:
        assert request_id in server_output
    if log_format == "json":
        for line in server_output.splitlines():
            if line.strip():
                json.loads(line)
