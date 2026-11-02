"""The JWKS key provider: caching, refresh limits, failure handling and the real HTTP fetch.

The network tests use a loopback server run by the test itself. No Supabase project is
involved, and nothing leaves the machine.
"""

import datetime
import ipaddress
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

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


# A drip-feed server sends one byte every DRIP_GAP seconds. With a total deadline of 1 second,
# every single read finishes well inside it (0.2 s), yet the response would take DRIP_TOTAL
# (20 s) to arrive, so only a total deadline stops it.
DRIP_GAP = 0.2
DRIP_BYTES = 100
DRIP_TOTAL = DRIP_GAP * DRIP_BYTES
DEADLINE = 1.0
SLOW_OK_DELAY = 1.5  # a valid response that starts later than DEADLINE


class Server:
    def __init__(self) -> None:
        self.mode = "ok"
        self.delay = SLOW_OK_DELAY  # used by the slow_ok and split_ok modes
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
                elif outer.mode == "drip_body":
                    # Headers at once, then one byte every DRIP_GAP seconds: every single read
                    # finishes inside a per-read timeout, but the whole body takes DRIP_TOTAL.
                    self.send_response(200)
                    self.send_header("Content-Length", str(DRIP_BYTES))
                    self.end_headers()
                    self.drip(b"x" * DRIP_BYTES)
                elif outer.mode == "drip_headers":
                    self.drip(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                elif outer.mode == "error_with_body":
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    self.wfile.write(outer.body)
                elif outer.mode == "redirect_with_body":
                    self.send_response(302)
                    self.send_header("Location", "/trap")
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    self.wfile.write(outer.body)
                elif outer.mode == "stream_big":
                    # No Content-Length: the body ends when the connection closes.
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b" " * 5000 + outer.body)
                elif outer.mode == "short_body":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(outer.body) + 500))
                    self.end_headers()
                    self.wfile.write(outer.body)  # valid JSON, but fewer bytes than declared
                elif outer.mode == "valid_then_drip":
                    # A complete key set, then the response never ends (no Content-Length).
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(outer.body)
                    self.wfile.flush()
                    self.drip(b" " * DRIP_BYTES)
                elif outer.mode == "slow_ok":
                    # A complete, valid answer that simply takes a while to start.
                    time.sleep(outer.delay)
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    self.wfile.write(outer.body)
                elif outer.mode == "split_ok":
                    # A complete, valid answer that pauses twice: before the headers and in
                    # the middle of the body. No single wait is long, the whole is.
                    half = len(outer.body) // 2
                    time.sleep(outer.delay)
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    self.wfile.write(outer.body[:half])
                    self.wfile.flush()
                    time.sleep(outer.delay)
                    self.wfile.write(outer.body[half:])
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

            def drip(self, data: bytes) -> None:
                try:
                    for index in range(len(data)):
                        self.wfile.write(data[index : index + 1])
                        self.wfile.flush()
                        time.sleep(DRIP_GAP)
                except OSError:
                    pass  # the client gave up, which is what the tests expect

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


# --- total deadline against drip-feed responses ----------------------------------------


@pytest.mark.parametrize("mode", ["drip_body", "drip_headers"])
def test_the_timeout_is_a_total_deadline_not_a_per_read_timeout(server: Server, mode) -> None:
    assert DRIP_GAP < DEADLINE < DRIP_TOTAL  # each read is fast; the whole response is not
    server.mode = mode
    started = time.monotonic()

    with pytest.raises(KeyUnavailable):
        fetch_jwks_bytes(server.url, DEADLINE, 64 * 1024)

    elapsed = time.monotonic() - started
    assert DEADLINE * 0.8 <= elapsed < DEADLINE + 1.0, f"took {elapsed:.2f}s"


def test_a_drip_fed_refresh_fails_closed_and_never_serves_the_expired_keys(server: Server) -> None:
    clock = Clock()
    keys = JwksKeyProvider(server.url, timeout=DEADLINE, ttl=300, clock=clock)
    assert keys.get_key(a.KID) is not None  # a normal fetch fills the cache
    server.mode = "drip_body"
    clock.now += 301  # the cached keys have expired
    started = time.monotonic()

    with pytest.raises(KeyUnavailable):
        keys.get_key(a.KID)

    assert time.monotonic() - started < DEADLINE + 1.0


def test_a_body_shorter_than_declared_is_refused(server: Server) -> None:
    server.mode = "drip_body"  # declares 100 bytes, gives up after the deadline, never complete

    with pytest.raises(KeyUnavailable):
        fetch_jwks_bytes(server.url, 0.5, 64 * 1024)


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_the_total_deadline_also_holds_over_tls(tmp_path, monkeypatch, phase) -> None:
    """Production fetches use HTTPS, so check the watchdog on a TLS socket too.

    A throw-away certificate for 127.0.0.1 is generated here and trusted only for this
    process through SSL_CERT_FILE; certificate verification stays on.
    """
    key = a.new_ec_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    head = b"HTTP/1.0 200 OK\r\nContent-Length: %d\r\n\r\n" % DRIP_BYTES

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with context.wrap_socket(connection, server_side=True) as tls:
                tls.recv(4096)
                payload = head + b"x" * DRIP_BYTES
                if phase == "body":
                    tls.sendall(head)
                    payload = payload[len(head) :]
                for index in range(len(payload)):
                    tls.sendall(payload[index : index + 1])
                    time.sleep(DRIP_GAP)
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    started = time.monotonic()
    try:
        with pytest.raises(KeyUnavailable):
            fetch_jwks_bytes(f"https://127.0.0.1:{listener.getsockname()[1]}/jwks", DEADLINE, 65536)
    finally:
        listener.close()

    elapsed = time.monotonic() - started
    # Not an immediate certificate failure, and not the length of the drip.
    assert DEADLINE * 0.8 <= elapsed < DEADLINE + 1.0, f"took {elapsed:.2f}s"


# --- the fetch itself must refuse these, whatever the caller does with the body --------


@pytest.mark.parametrize(
    "mode",
    [
        "error_with_body",  # a valid key set sent with an error status
        "redirect_with_body",  # a valid key set sent with a redirect
        "short_body",  # fewer bytes than the response declared
    ],
)
def test_fetch_refuses_responses_that_are_not_a_complete_200(server: Server, mode) -> None:
    server.mode = mode

    with pytest.raises(KeyUnavailable):
        fetch_jwks_bytes(server.url, 3.0, 64 * 1024)
    assert "/trap" not in server.hits


def test_fetch_enforces_the_size_cap_when_no_length_is_declared(server: Server) -> None:
    server.mode = "stream_big"

    with pytest.raises(KeyUnavailable):
        fetch_jwks_bytes(server.url, 3.0, 1000)


def test_a_response_that_never_ends_is_refused_even_if_what_arrived_is_valid(
    server: Server,
) -> None:
    server.mode = "valid_then_drip"
    assert DEADLINE < DRIP_TOTAL
    started = time.monotonic()

    with pytest.raises(KeyUnavailable):
        fetch_jwks_bytes(server.url, DEADLINE, 64 * 1024)

    assert time.monotonic() - started < DEADLINE + 1.0
