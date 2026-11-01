"""The JWKS key provider: caching, refresh limits, failure handling and the real HTTP fetch.

The network tests use a loopback server run by the test itself. No Supabase project is
involved, and nothing leaves the machine.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.core.auth import (
    JwksKeyProvider,
    KeyUnavailable,
    TokenVerifier,
    UnknownKey,
    fetch_jwks_bytes,
)
from tests import auth_support as a


def jwks(*pairs: tuple[str, Any]) -> bytes:
    return json.dumps({"keys": [a.public_jwk(key, kid) for kid, key in pairs]}).encode()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ScriptedFetch:
    """A fake fetch function that returns what the test says and counts its calls."""

    def __init__(self, *responses: bytes | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, url: str, timeout: float, max_bytes: int) -> bytes:
        self.calls += 1
        response = self.responses[min(self.calls, len(self.responses)) - 1]
        if isinstance(response, Exception):
            raise response
        return response


def provider(fetch: Callable[..., bytes], clock: Clock | None = None, **kw: Any):
    return JwksKeyProvider(
        "https://example.invalid/jwks", fetch=fetch, clock=clock or Clock(), **kw
    )


def test_returns_the_key_for_a_known_kid_and_caches_the_set() -> None:
    fetch = ScriptedFetch(jwks((a.KID, a.EC_KEY)))
    keys = provider(fetch)

    for _ in range(5):
        assert keys.get_key(a.KID) is not None

    assert fetch.calls == 1


def test_cache_expires_after_the_ttl() -> None:
    clock = Clock()
    fetch = ScriptedFetch(jwks((a.KID, a.EC_KEY)))
    keys = provider(fetch, clock, ttl=300)

    keys.get_key(a.KID)
    clock.now += 301
    keys.get_key(a.KID)

    assert fetch.calls == 2


def test_unknown_kids_cannot_cause_more_than_one_refresh_per_cooldown() -> None:
    clock = Clock()
    fetch = ScriptedFetch(jwks((a.KID, a.EC_KEY)))
    keys = provider(fetch, clock, cooldown=30)
    keys.get_key(a.KID)

    for index in range(200):
        with pytest.raises(UnknownKey):
            keys.get_key(f"random-{index}")
    assert fetch.calls == 1  # all inside the cooldown after the first fetch

    clock.now += 31
    for index in range(200):
        with pytest.raises(UnknownKey):
            keys.get_key(f"random-again-{index}")
    assert fetch.calls == 2  # one forced refresh, then the cooldown applies again


def test_a_rotated_key_is_picked_up_after_the_cooldown() -> None:
    clock = Clock()
    fetch = ScriptedFetch(
        jwks((a.KID, a.EC_KEY)), jwks((a.KID, a.EC_KEY), ("rotated", a.OTHER_EC_KEY))
    )
    keys = provider(fetch, clock, cooldown=30)
    keys.get_key(a.KID)

    clock.now += 31

    assert keys.get_key("rotated") is not None


def test_first_fetch_failure_is_unavailable_and_retries_are_backed_off() -> None:
    clock = Clock()
    fetch = ScriptedFetch(KeyUnavailable())
    keys = provider(fetch, clock, failure_backoff=5)

    for _ in range(50):
        with pytest.raises(KeyUnavailable):
            keys.get_key(a.KID)
    assert fetch.calls == 1

    clock.now += 6
    with pytest.raises(KeyUnavailable):
        keys.get_key(a.KID)
    assert fetch.calls == 2


def test_recovers_when_the_endpoint_comes_back() -> None:
    clock = Clock()
    fetch = ScriptedFetch(KeyUnavailable(), jwks((a.KID, a.EC_KEY)))
    keys = provider(fetch, clock, failure_backoff=5)
    with pytest.raises(KeyUnavailable):
        keys.get_key(a.KID)

    clock.now += 6

    assert keys.get_key(a.KID) is not None


def test_expired_cache_is_not_served_when_the_refresh_fails() -> None:
    clock = Clock()
    fetch = ScriptedFetch(jwks((a.KID, a.EC_KEY)), KeyUnavailable())
    keys = provider(fetch, clock, ttl=300)
    keys.get_key(a.KID)

    clock.now += 301

    with pytest.raises(KeyUnavailable):
        keys.get_key(a.KID)


def test_fresh_cache_with_a_failed_refresh_reports_an_unknown_key_not_an_outage() -> None:
    clock = Clock()
    fetch = ScriptedFetch(jwks((a.KID, a.EC_KEY)), KeyUnavailable())
    keys = provider(fetch, clock, ttl=300, cooldown=30)
    keys.get_key(a.KID)
    clock.now += 31

    with pytest.raises(UnknownKey):
        keys.get_key("something-else")
    assert keys.get_key(a.KID) is not None  # known keys keep working


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'"keys"',
        b"{}",
        b'{"keys": {}}',
        b'{"keys": []}',  # what a project on the legacy shared secret publishes
        b'{"keys": [1, "x", null]}',
        b'{"keys": [{"kty": "oct", "kid": "k", "k": "c2VjcmV0"}]}',
        b'{"keys": [{"kty": "EC", "kid": "k", "crv": "P-256", "x": "AA", "y": "AA"}]}',
        b'{"keys": [{"kty": "EC", "crv": "P-256"}]}',
        b"[" * 100000,
    ],
)
def test_unusable_key_sets_are_unavailable(body: bytes) -> None:
    with pytest.raises(KeyUnavailable):
        provider(ScriptedFetch(body)).get_key(a.KID)


def test_malformed_and_symmetric_entries_are_skipped_not_trusted() -> None:
    good = a.public_jwk(a.EC_KEY)
    body = json.dumps(
        {
            "keys": [
                {"kty": "oct", "kid": "shared", "k": "c2VjcmV0"},
                {"kty": "EC", "kid": "broken"},
                "junk",
                good,
            ]
        }
    ).encode()
    keys = provider(ScriptedFetch(body))

    assert keys.get_key(a.KID) is not None
    with pytest.raises(UnknownKey):
        keys.get_key("shared")


def test_key_set_size_is_bounded() -> None:
    many = [(f"k{i}", a.EC_KEY) for i in range(50)]
    keys = provider(ScriptedFetch(jwks(*many)))

    assert keys.get_key("k0") is not None
    with pytest.raises(UnknownKey):
        keys.get_key("k49")


# --- real HTTP against a loopback server -----------------------------------------------


class Server:
    def __init__(self) -> None:
        self.mode = "ok"
        self.body = jwks((a.KID, a.EC_KEY))
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.hits.append(self.path)
                if self.path == "/trap":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"keys": []}')
                elif outer.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/trap")
                    self.end_headers()
                elif outer.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                elif outer.mode == "slow":
                    time.sleep(1.5)
                    self.send_response(200)
                    self.end_headers()
                else:
                    body = outer.body
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}/jwks"


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[Server]:
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    instance = Server()
    instance.thread.start()
    yield instance
    instance.httpd.shutdown()
    instance.httpd.server_close()


def test_real_fetch_end_to_end_verifies_a_token(server: Server) -> None:
    keys = JwksKeyProvider(server.url)
    verifier = TokenVerifier(keys, issuer=a.ISSUER, audience=a.AUDIENCE)

    assert verifier.verify(a.make_token()).user_id
    verifier.verify(a.make_token())
    assert server.hits == ["/jwks"]  # fetched once, then cached


def test_a_redirect_is_not_followed(server: Server) -> None:
    server.mode = "redirect"

    with pytest.raises(KeyUnavailable):
        JwksKeyProvider(server.url).get_key(a.KID)
    assert "/trap" not in server.hits


def test_an_error_status_is_unavailable(server: Server) -> None:
    server.mode = "error"

    with pytest.raises(KeyUnavailable):
        JwksKeyProvider(server.url).get_key(a.KID)


def test_a_slow_endpoint_times_out(server: Server) -> None:
    server.mode = "slow"
    started = time.monotonic()

    with pytest.raises(KeyUnavailable):
        JwksKeyProvider(server.url, timeout=0.3).get_key(a.KID)
    assert time.monotonic() - started < 1.2


def test_an_oversized_response_is_refused(server: Server) -> None:
    server.body = b'{"keys": [], "padding": "' + b"x" * 5000 + b'"}'

    with pytest.raises(KeyUnavailable):
        fetch_jwks_bytes(server.url, 1.0, 1000)


def test_an_unreachable_endpoint_is_unavailable() -> None:
    with pytest.raises(KeyUnavailable):
        fetch_jwks_bytes("http://127.0.0.1:9/jwks", 0.5, 1000)


def test_a_jku_header_never_makes_the_verifier_fetch_from_that_address(server: Server) -> None:
    trap = Server()
    trap.thread.start()
    try:
        keys = JwksKeyProvider(server.url)
        verifier = TokenVerifier(keys, issuer=a.ISSUER, audience=a.AUDIENCE)
        trap_url = trap.url.replace("/jwks", "/keys")

        token = a.make_token(
            key=a.OTHER_EC_KEY, headers={"jku": trap_url, "x5u": trap_url, "kid": "attacker"}
        )

        with pytest.raises(Exception):  # noqa: B017 - the exact reason is covered elsewhere
            verifier.verify(token)
        assert trap.hits == []
    finally:
        trap.httpd.shutdown()
        trap.httpd.server_close()
