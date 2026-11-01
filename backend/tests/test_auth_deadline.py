"""The configurable total deadline for fetching the signing keys (issue #65).

Covers: the setting, the single-flight DNS helper, one monotonic deadline across DNS, connect,
TLS, headers and body, the fixed failure categories, and the unchanged cache and backoff rules.

Everything runs against synthetic keys, a fake resolver and loopback servers. Nothing leaves
the machine and no Supabase project is involved.
"""

import datetime
import io
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.core.auth as auth_module
from app.core.auth import (
    FailureCategory,
    JwksFetcher,
    JwksKeyProvider,
    KeyUnavailable,
    SingleFlightResolver,
)
from app.core.config import Settings
from app.core.logging import configure_logging
from app.main import API_V1_PREFIX, create_app
from app.tools import check_token
from tests import auth_support as a
from tests.test_auth_jwks import (  # noqa: F401
    DEADLINE,
    DRIP_TOTAL,
    SLOW_OK_DELAY,
    Clock,
    Server,
    jwks,
    server,
)

# Scheduling tolerance for "returns within the deadline": thread start-up, the watchdog timer
# and a loaded CI runner. Documented in docs/authentication.md.
TOLERANCE = 1.0
NAME = "jwks.test"  # a name that only our fake resolver knows
MAX = 64 * 1024


def loopback(port: int) -> list[Any]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


class FakeResolver:
    """A resolver that can be made slow, can fail, and records how it was used."""

    def __init__(self, addresses: Callable[[], list[Any]] | None = None) -> None:
        self.addresses = addresses or (lambda: [])
        self.release = threading.Event()
        self.block = False
        self.error: Exception | None = None
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()

    def __call__(self, host: str, port: int) -> list[Any]:
        with self.guard:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.block:
                self.release.wait(30)
            if self.error is not None:
                raise self.error
            return self.addresses()
        finally:
            with self.guard:
                self.active -= 1


def wait_until(predicate: Callable[[], bool], limit: float = 3.0) -> bool:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def named(url: str) -> str:
    """The loopback test server's URL, but addressed by a name only the fake resolver knows."""
    return url.replace("127.0.0.1", NAME)


def category_of(call: Callable[[], Any]) -> FailureCategory:
    with pytest.raises(KeyUnavailable) as caught:
        call()
    return caught.value.category


# --- the setting -------------------------------------------------------------------------


def make(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_the_default_deadline_is_ten_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_JWKS_TIMEOUT_SECONDS", raising=False)

    assert make().supabase_jwks_timeout_seconds == 10.0


@pytest.mark.parametrize("raw", ["", "  "])
def test_an_empty_value_uses_the_default(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SUPABASE_JWKS_TIMEOUT_SECONDS", raw)

    assert make().supabase_jwks_timeout_seconds == 10.0


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", 1.0), ("2.5", 2.5), ("30", 30.0), (" 7 ", 7.0)]
)
def test_values_inside_the_range_are_accepted(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    monkeypatch.setenv("SUPABASE_JWKS_TIMEOUT_SECONDS", raw)

    assert make().supabase_jwks_timeout_seconds == expected


@pytest.mark.parametrize(
    "raw",
    [
        "0",
        "-1",
        "0.99",
        "30.01",
        "31",
        "nan",
        "NaN",
        "inf",
        "-inf",
        "infinity",
        "abc",
        "1e999",
        "5s",
    ],
)
def test_invalid_values_are_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("SUPABASE_JWKS_TIMEOUT_SECONDS", raw)

    with pytest.raises(ValidationError) as caught:
        make()

    text = str(caught.value)
    assert f"'{raw}'" not in text
    assert "input_value" not in text
    assert "input_value" not in str(caught.value)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, 31, None.__class__])
def test_invalid_python_values_cannot_disable_the_deadline(value: object) -> None:
    with pytest.raises(ValidationError):
        make(supabase_jwks_timeout_seconds=value)


def test_the_configured_deadline_reaches_the_provider() -> None:
    application = create_app(a.auth_settings(supabase_jwks_timeout_seconds=4.0))

    provider = application.state.token_verifier._keys  # noqa: SLF001
    assert provider._timeout == 4.0  # noqa: SLF001


# --- DNS: single flight, bounded caller wait --------------------------------------------------


def test_a_slow_lookup_returns_at_the_deadline_not_when_the_resolver_finishes() -> None:
    resolver = FakeResolver()
    resolver.block = True
    single = SingleFlightResolver(resolver)
    started = time.monotonic()

    try:
        assert category_of(lambda: single.lookup(NAME, 443, started + 0.5)) is (
            FailureCategory.DEADLINE
        )
        elapsed = time.monotonic() - started
        assert 0.4 <= elapsed < 0.5 + TOLERANCE, f"took {elapsed:.2f}s"
        assert resolver.active == 1  # the OS call is still running; we did not wait for it
    finally:
        resolver.release.set()


def test_repeated_timeouts_never_leave_more_than_one_lookup_in_flight() -> None:
    resolver = FakeResolver()
    resolver.block = True
    single = SingleFlightResolver(resolver)

    try:
        for _ in range(8):
            assert category_of(lambda: single.lookup(NAME, 443, time.monotonic() + 0.05)) is (
                FailureCategory.DEADLINE
            )
        assert resolver.calls == 1
        assert resolver.max_active == 1
        # Another name must not start a second lookup either; it waits for the first.
        assert category_of(lambda: single.lookup("other.test", 443, time.monotonic() + 0.05)) is (
            FailureCategory.DEADLINE
        )
        assert resolver.calls == 1
        assert resolver.max_active == 1
    finally:
        resolver.release.set()
    assert wait_until(lambda: resolver.active == 0)


def test_a_new_lookup_starts_after_the_abandoned_one_finishes() -> None:
    resolver = FakeResolver(lambda: loopback(1))
    resolver.block = True
    single = SingleFlightResolver(resolver)
    assert category_of(lambda: single.lookup(NAME, 443, time.monotonic() + 0.05)) is (
        FailureCategory.DEADLINE
    )

    resolver.release.set()
    assert wait_until(lambda: resolver.active == 0)
    resolver.block = False

    assert single.lookup(NAME, 443, time.monotonic() + 2) == loopback(1)
    assert resolver.calls == 2
    assert resolver.max_active == 1


def test_a_caller_joins_the_outstanding_lookup_for_the_same_name() -> None:
    resolver = FakeResolver(lambda: loopback(1))
    resolver.block = True
    single = SingleFlightResolver(resolver)
    assert category_of(lambda: single.lookup(NAME, 443, time.monotonic() + 0.05)) is (
        FailureCategory.DEADLINE
    )
    threading.Timer(0.2, resolver.release.set).start()

    assert single.lookup(NAME, 443, time.monotonic() + 3) == loopback(1)
    assert resolver.calls == 1  # the later caller reused the first lookup


def test_a_resolver_error_and_an_empty_answer_are_dns_failures() -> None:
    failing = FakeResolver()
    failing.error = OSError("secret-host.internal could not be resolved")
    empty = FakeResolver(lambda: [])

    for resolver in (failing, empty):
        single = SingleFlightResolver(resolver)
        with pytest.raises(KeyUnavailable) as caught:
            single.lookup(NAME, 443, time.monotonic() + 2)
        assert caught.value.category is FailureCategory.DNS
        assert str(caught.value) == ""
        assert caught.value.__cause__ is None


def test_a_literal_ip_address_needs_no_lookup(server: Server) -> None:  # noqa: F811
    resolver = FakeResolver()
    resolver.block = True  # would hang if it were used

    body = JwksFetcher(SingleFlightResolver(resolver))(server.url, 3.0, MAX)

    assert body == server.body
    assert resolver.calls == 0


def test_successful_dns_inside_the_deadline_proceeds_normally(server: Server) -> None:  # noqa: F811
    resolver = FakeResolver(lambda: loopback(server.httpd.server_port))
    started = time.monotonic()

    body = JwksFetcher(SingleFlightResolver(resolver))(named(server.url), 3.0, MAX)

    assert body == server.body
    assert resolver.calls == 1
    assert time.monotonic() - started < 1.0


def test_the_host_header_keeps_the_original_name(server: Server) -> None:  # noqa: F811
    seen: list[str] = []
    resolver = FakeResolver(lambda: loopback(server.httpd.server_port))
    fetcher = JwksFetcher(SingleFlightResolver(resolver))
    original = server.httpd.RequestHandlerClass.do_GET

    def spy(handler: Any) -> None:
        seen.append(handler.headers["Host"])
        original(handler)

    server.httpd.RequestHandlerClass.do_GET = spy
    try:
        fetcher(named(server.url), 3.0, MAX)
    finally:
        server.httpd.RequestHandlerClass.do_GET = original

    assert seen == [f"{NAME}:{server.httpd.server_port}"]


# --- lock release -------------------------------------------------------------------------------


def blocked_provider(timeout: float = 0.5, **kw: Any) -> tuple[JwksKeyProvider, FakeResolver]:
    resolver = FakeResolver()
    resolver.block = True
    keys = JwksKeyProvider(
        f"https://{NAME}/jwks",
        timeout=timeout,
        failure_backoff=kw.pop("failure_backoff", 0.0),
        fetch=JwksFetcher(SingleFlightResolver(resolver)),
        **kw,
    )
    return keys, resolver


def test_a_dns_timeout_releases_the_provider_lock_without_waiting_for_the_resolver() -> None:
    keys, resolver = blocked_provider()
    started = time.monotonic()
    try:
        assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.DEADLINE
        elapsed = time.monotonic() - started

        assert elapsed < 0.5 + TOLERANCE, f"took {elapsed:.2f}s"
        assert keys._lock.acquire(blocking=False)  # noqa: SLF001
        keys._lock.release()  # noqa: SLF001
        assert resolver.active == 1  # the OS lookup is still outstanding
    finally:
        resolver.release.set()


def test_a_concurrent_caller_is_not_held_back_by_the_abandoned_lookup() -> None:
    keys, resolver = blocked_provider(timeout=0.5)
    outcomes: list[tuple[FailureCategory, float]] = []

    def call() -> None:
        started = time.monotonic()
        outcomes.append((category_of(lambda: keys.get_key(a.KID)), time.monotonic() - started))

    try:
        threads = [threading.Thread(target=call) for _ in range(3)]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        total = time.monotonic() - started
    finally:
        resolver.release.set()

    assert [c for c, _ in outcomes] == [FailureCategory.DEADLINE] * 3
    # They queue on the lock one after another, each bounded by its own deadline, and none of
    # them waits for the resolver itself.
    assert total < 3 * (0.5 + TOLERANCE)
    assert resolver.calls == 1
    assert resolver.max_active == 1


def test_repeated_provider_timeouts_leave_one_resolver_thread() -> None:
    keys, resolver = blocked_provider(timeout=0.2)
    try:
        for _ in range(6):
            assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.DEADLINE
        assert resolver.calls == 1
        assert resolver.max_active == 1
        names = [t for t in threading.enumerate() if t.name == "jwks-dns" and t.is_alive()]
        assert len(names) <= 1
    finally:
        resolver.release.set()


# --- the total deadline across every phase ------------------------------------------------------


def test_a_slow_but_successful_response_inside_the_deadline_is_accepted(
    server: Server,  # noqa: F811
) -> None:
    server.mode = "slow_ok"  # starts answering after 1.5 s: past DEADLINE, inside this limit
    assert SLOW_OK_DELAY > DEADLINE

    body = JwksFetcher()(server.url, 4.0, MAX)

    assert body == server.body


def test_a_slow_resolver_and_a_slow_response_share_one_deadline(server: Server) -> None:  # noqa: F811
    """Neither phase is slow enough to fail alone; together they exceed the one deadline."""
    server.mode = "slow_ok"  # 1.5 s to answer
    resolver = FakeResolver(lambda: (time.sleep(0.8), loopback(server.httpd.server_port))[1])
    started = time.monotonic()

    category = category_of(
        lambda: JwksFetcher(SingleFlightResolver(resolver))(named(server.url), 2.0, MAX)
    )

    assert category is FailureCategory.DEADLINE
    assert time.monotonic() - started < 2.0 + TOLERANCE


@pytest.mark.parametrize("mode", ["slow", "drip_headers", "drip_body"])
def test_an_overrun_is_cut_off_at_about_the_deadline(server: Server, mode: str) -> None:  # noqa: F811
    server.mode = mode
    started = time.monotonic()

    category = category_of(lambda: JwksFetcher()(server.url, 1.0, MAX))

    elapsed = time.monotonic() - started
    assert category is FailureCategory.DEADLINE
    assert 0.8 <= elapsed < 1.0 + TOLERANCE, f"took {elapsed:.2f}s"


def test_a_deadline_that_expires_during_dns_gives_the_deadline_category(
    server: Server,  # noqa: F811
) -> None:
    resolver = FakeResolver()
    resolver.block = True
    try:
        category = category_of(
            lambda: JwksFetcher(SingleFlightResolver(resolver))(named(server.url), 0.5, MAX)
        )
    finally:
        resolver.release.set()

    assert category is FailureCategory.DEADLINE
    assert server.hits == []  # the request was never sent


def test_connect_and_read_timeouts_are_three_seconds_capped_by_the_time_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert auth_module.JWKS_CONNECT_TIMEOUT_SECONDS == 3.0
    assert auth_module.JWKS_READ_TIMEOUT_SECONDS == 3.0
    applied: list[float | None] = []

    class RecordingSocket:
        def __init__(self, *args: Any) -> None:
            pass

        def settimeout(self, value: float | None) -> None:
            applied.append(value)

        def connect(self, address: Any) -> None:
            raise OSError("refused")

        def close(self) -> None:
            pass

    monkeypatch.setattr(auth_module.socket, "socket", RecordingSocket)
    addresses = loopback(9)

    for budget in (30.0, 1.5):
        applied.clear()
        with pytest.raises(KeyUnavailable):
            auth_module._connect(addresses, time.monotonic() + budget)  # noqa: SLF001
        assert applied and applied[0] is not None
        assert applied[0] <= min(3.0, budget)
        assert applied[0] > min(3.0, budget) - 0.5

    class Plain:
        def __init__(self) -> None:
            self.timeouts: list[float | None] = []

        def settimeout(self, value: float | None) -> None:
            self.timeouts.append(value)

    sock = Plain()
    auth_module._arm(sock, time.monotonic() + 30.0)  # noqa: SLF001
    auth_module._arm(sock, time.monotonic() + 0.4)  # noqa: SLF001
    assert sock.timeouts[0] == 3.0
    assert sock.timeouts[1] <= 0.4
    with pytest.raises(KeyUnavailable) as caught:
        auth_module._arm(sock, time.monotonic() - 1)  # noqa: SLF001
    assert caught.value.category is FailureCategory.DEADLINE


def test_each_connect_attempt_gets_its_own_limit_and_the_next_address_is_tried(
    server: Server,  # noqa: F811
) -> None:
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()  # nothing listens here any more
    resolver = FakeResolver(lambda: loopback(dead_port) + loopback(server.httpd.server_port))

    body = JwksFetcher(SingleFlightResolver(resolver))(named(server.url), 3.0, MAX)

    assert body == server.body


# --- TLS: verification stays on and uses the original host name ---------------------------------


@contextmanager
def tls_server(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, san: str) -> Iterator[int]:
    key = a.new_ec_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)])
    now = datetime.datetime.now(datetime.UTC)
    san_entry: x509.GeneralName
    try:
        san_entry = x509.IPAddress(ipaddress.ip_address(san))
    except ValueError:
        san_entry = x509.DNSName(san)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(7)
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([san_entry]), False)
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
    listener.listen(4)
    body = jwks((a.KID, a.EC_KEY))
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(connection, server_side=True) as tls:
                    tls.recv(4096)
                    tls.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%b"
                        % (len(body), body)
                    )
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        listener.close()


def test_tls_is_verified_against_the_original_host_name(tmp_path, monkeypatch) -> None:
    with tls_server(tmp_path, monkeypatch, NAME) as port:
        resolver = FakeResolver(lambda: loopback(port))

        body = JwksFetcher(SingleFlightResolver(resolver))(f"https://{NAME}:{port}/jwks", 5.0, MAX)

    assert body == jwks((a.KID, a.EC_KEY))


def test_a_certificate_for_another_name_is_a_tls_failure(tmp_path, monkeypatch) -> None:
    """The name resolves to the loopback server, but its certificate is for a different name."""
    with tls_server(tmp_path, monkeypatch, "someone-else.test") as port:
        resolver = FakeResolver(lambda: loopback(port))

        category = category_of(
            lambda: JwksFetcher(SingleFlightResolver(resolver))(
                f"https://{NAME}:{port}/jwks", 5.0, MAX
            )
        )

    assert category is FailureCategory.TLS


def test_an_untrusted_certificate_is_a_tls_failure(tmp_path, monkeypatch) -> None:
    with tls_server(tmp_path, monkeypatch, NAME) as port:
        monkeypatch.delenv("SSL_CERT_FILE")  # the throw-away certificate is no longer trusted
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "key.pem"))  # no certificates in it
        resolver = FakeResolver(lambda: loopback(port))

        category = category_of(
            lambda: JwksFetcher(SingleFlightResolver(resolver))(
                f"https://{NAME}:{port}/jwks", 5.0, MAX
            )
        )

    assert category in {FailureCategory.TLS}


# --- failure categories: each is produced by a test, and none exposes network detail -------------


def closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def trigger(case: str, server: Server, tmp_path: Any, monkeypatch: Any) -> KeyUnavailable:  # noqa: F811
    """Run one failing fetch and return the exception it raised."""
    server_port = server.httpd.server_port
    call: Callable[[], bytes]
    if case == "dns":
        resolver = FakeResolver()
        resolver.error = OSError("lookup of internal-secret.example failed")
        call = lambda: JwksFetcher(SingleFlightResolver(resolver))(  # noqa: E731
            f"https://{NAME}/jwks", 2.0, MAX
        )
    elif case == "connect":
        resolver = FakeResolver(lambda: loopback(closed_port()))
        call = lambda: JwksFetcher(SingleFlightResolver(resolver))(  # noqa: E731
            f"http://{NAME}:9/jwks", 2.0, MAX
        )
    elif case == "tls":
        # A plain-HTTP server answering a TLS client: the handshake cannot succeed.
        call = lambda: JwksFetcher()(f"https://127.0.0.1:{server_port}/jwks", 2.0, MAX)  # noqa: E731
    elif case == "status":
        server.mode = "redirect"
        call = lambda: JwksFetcher()(server.url, 2.0, MAX)  # noqa: E731
    elif case == "deadline":
        server.mode = "drip_body"
        call = lambda: JwksFetcher()(server.url, 1.0, MAX)  # noqa: E731
    elif case == "size":
        server.mode = "stream_big"
        call = lambda: JwksFetcher()(server.url, 2.0, 1000)  # noqa: E731
    else:
        server.mode = "short_body"
        call = lambda: JwksFetcher()(server.url, 2.0, MAX)  # noqa: E731
    with pytest.raises(KeyUnavailable) as caught:
        call()
    return caught.value


@pytest.mark.parametrize("case", [c.value for c in FailureCategory])
def test_each_failure_category_is_produced_and_leaks_nothing(
    case: str,
    server: Server,  # noqa: F811
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = trigger(case, server, tmp_path, monkeypatch)

    assert error.category is FailureCategory(case)
    assert str(error) == ""
    assert error.args == ()
    assert error.__cause__ is None
    assert error.__suppress_context__ or error.__context__ is None or case == "dns"
    rendered = repr(error) + str(error.category)
    for sensitive in ("127.0.0.1", NAME, "internal-secret", str(server.httpd.server_port), "jwks"):
        assert sensitive not in rendered


def test_the_categories_are_fixed_words() -> None:
    assert {c.value for c in FailureCategory} == {
        "dns",
        "connect",
        "tls",
        "status",
        "deadline",
        "size",
        "body",
    }


def test_a_bare_key_unavailable_still_works() -> None:
    assert KeyUnavailable().category is FailureCategory.BODY


# --- the provider: cache, cooldown, backoff and no stale keys are unchanged ---------------------


class Boom:
    """A fetch that raises a prepared failure, and counts calls."""

    def __init__(self, *outcomes: bytes | KeyUnavailable) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url: str, timeout: float, max_bytes: int) -> bytes:
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, KeyUnavailable):
            raise outcome
        return outcome


def test_a_timed_out_refresh_fails_closed_backs_off_and_never_serves_expired_keys() -> None:
    clock = Clock()
    good = jwks((a.KID, a.EC_KEY))
    fetch = Boom(good, KeyUnavailable(FailureCategory.DEADLINE), good)
    keys = JwksKeyProvider(
        "https://example.invalid/jwks",
        ttl=300,
        failure_backoff=5,
        fetch=fetch,
        clock=clock,
    )
    assert keys.get_key(a.KID) is not None

    clock.now += 301  # expired
    assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.DEADLINE
    assert fetch.calls == 2

    clock.now += 4  # inside the backoff: no new fetch, and the failure category is remembered
    assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.DEADLINE
    assert fetch.calls == 2

    clock.now += 2  # backoff over: tries again and recovers
    assert keys.get_key(a.KID) is not None
    assert fetch.calls == 3


def test_a_timed_out_refresh_on_a_fresh_cache_is_just_an_unknown_key() -> None:
    clock = Clock()
    good = jwks((a.KID, a.EC_KEY))
    fetch = Boom(good, KeyUnavailable(FailureCategory.DEADLINE))
    keys = JwksKeyProvider(
        "https://example.invalid/jwks",
        ttl=300,
        cooldown=30,
        failure_backoff=5,
        fetch=fetch,
        clock=clock,
    )
    keys.get_key(a.KID)
    clock.now += 31

    with pytest.raises(auth_module.UnknownKey):
        keys.get_key("some-other-kid")

    assert keys.get_key(a.KID) is not None  # the cached keys are still fresh and still served
    assert fetch.calls == 2


def test_the_backoff_does_not_change_with_the_deadline() -> None:
    clock = Clock()
    fetch = Boom(KeyUnavailable(FailureCategory.DNS))
    keys = JwksKeyProvider(
        "https://example.invalid/jwks", timeout=30.0, failure_backoff=5, fetch=fetch, clock=clock
    )
    for _ in range(3):
        assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.DNS
    assert fetch.calls == 1
    clock.now += 5
    category_of(lambda: keys.get_key(a.KID))
    assert fetch.calls == 2


# --- API response, logs and the diagnostic tool -------------------------------------------------


@pytest.mark.parametrize("category", list(FailureCategory))
def test_the_api_stays_a_generic_503_and_logs_only_the_category(
    category: FailureCategory, log_format: str
) -> None:
    settings = a.auth_settings(log_level="DEBUG", log_format=log_format)
    stream = io.StringIO()
    keys = JwksKeyProvider(
        "https://secret-project.example.invalid/jwks",
        fetch=Boom(KeyUnavailable(category)),
        clock=Clock(),
    )
    application = create_app(settings, keys)
    configure_logging(settings, stream)
    client = TestClient(application, raise_server_exceptions=False)
    token = a.make_token()

    response = client.get(f"{API_V1_PREFIX}/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "service_unavailable"
    assert set(error) == {"code", "message", "request_id"}
    assert category.value not in response.text
    output = stream.getvalue()
    assert f"auth_keys_unavailable_{category.value}" in output
    for secret in (token, "secret-project", "example.invalid", "Bearer"):
        assert secret not in output
        assert secret not in response.text


def test_a_timeout_path_response_and_logs_carry_no_network_detail(
    server: Server,  # noqa: F811
    log_format: str,
) -> None:
    """The real fetch times out against a loopback server; nothing about it may leak."""
    server.mode = "drip_body"
    settings = a.auth_settings(log_level="DEBUG", log_format=log_format)
    stream = io.StringIO()
    keys = JwksKeyProvider(server.url, timeout=DEADLINE)
    application = create_app(settings, keys)
    configure_logging(settings, stream)
    client = TestClient(application, raise_server_exceptions=False)
    token = a.make_token()
    started = time.monotonic()

    response = client.get(f"{API_V1_PREFIX}/me", headers={"Authorization": f"Bearer {token}"})

    assert time.monotonic() - started < DEADLINE + TOLERANCE < DRIP_TOTAL
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    output = stream.getvalue()
    assert "auth_keys_unavailable_deadline" in output
    for secret in (token, "127.0.0.1", str(server.httpd.server_port), "/jwks", "Bearer", "xxxx"):
        assert secret not in output
        assert secret not in response.text


@pytest.mark.parametrize("category", list(FailureCategory))
def test_the_diagnostic_tool_prints_one_fixed_message_per_category(
    category: FailureCategory,
) -> None:
    lines: list[str] = []
    secret_url = "https://secret-project.example.invalid"
    settings = make(supabase_url=secret_url, supabase_jwks_timeout_seconds=12)
    seen: list[float] = []

    def failing(url: str, timeout: float, max_bytes: int) -> bytes:
        seen.append(timeout)
        raise KeyUnavailable(category)

    code = check_token.run(settings, fetch=failing, out=lines.append)

    assert code == 2
    assert seen == [12.0]  # the diagnostic uses the configured deadline
    message = lines[-1]
    assert message == check_token.KEY_FAILURE_MESSAGES[category]
    assert message.startswith("Could not fetch the signing keys")
    assert "secret-project" not in message
    assert len({check_token.KEY_FAILURE_MESSAGES[c] for c in FailureCategory}) == len(
        list(FailureCategory)
    )
