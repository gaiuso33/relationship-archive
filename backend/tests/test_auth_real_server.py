"""The protected endpoint on a real uvicorn process, with a key endpoint that cannot be reached.

Shows what the whole stack prints when signing keys are unavailable: the library's own
error text (which contains the key URL) must not reach the logs or the response.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import auth_support as a
from tests.test_privacy_real_server import BACKEND_DIR, free_port, request

UNREACHABLE_HOST = "synthetic-keys-unreachable.example.invalid"


@pytest.fixture
def server(log_format: str, tmp_path: Path):
    port = free_port()
    output_path = tmp_path / "server-output.txt"
    env = {
        **os.environ,
        "APP_ENV": "test",
        "LOG_FORMAT": log_format,
        "LOG_LEVEL": "DEBUG",
        "SUPABASE_URL": f"https://{UNREACHABLE_HOST}",
        "PYTHONUNBUFFERED": "1",
    }
    with output_path.open("w") as output:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
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
            yield port, output_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_real_server_401_and_503_leak_nothing(server) -> None:
    port, output_path = server
    token = a.make_token()

    missing = request(port, "GET", "/api/v1/me")
    garbage = request(port, "GET", "/api/v1/me", headers={"Authorization": "Bearer not.a.jwt"})
    keys_down = request(
        port,
        "GET",
        f"/api/v1/me?access_token={token}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert missing[0] == 401 and garbage[0] == 401
    assert missing[1]["www-authenticate"] == "Bearer"
    assert keys_down[0] == 503
    assert "www-authenticate" not in keys_down[1]
    time.sleep(0.3)
    output = output_path.read_text()
    assert "auth_rejected_missing_credentials" in output
    assert "auth_keys_unavailable" in output
    everything = output + "".join(
        f"{body}{headers}" for _, headers, body in (missing, garbage, keys_down)
    )
    for secret in (token, token.split(".")[1], a.SECRET_EMAIL, a.USER_ID, UNREACHABLE_HOST):
        assert secret not in everything, "a sensitive value leaked"
