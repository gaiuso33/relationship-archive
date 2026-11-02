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


@pytest.fixture(autouse=True)
def no_resolver_threads_left() -> Iterator[None]:
    """Let abandoned lookups from one test finish before the next test counts threads."""
    yield
    wait_until(
        lambda: not [t for t in threading.enumerate() if t.name == "jwks-dns" and t.is_alive()], 5
    )


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


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, 31, "abc"])
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
    assert error.__suppress_context__ or error.__context__ is None
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


# === review follow-up: unexpected failures, address fallback, resolver recovery, stalls ==========


class SocketShim:
    """The `socket` module as the fetcher sees it, with the socket class replaced.

    Only `app.core.auth` is given the shim, so the loopback test servers are unaffected.
    """

    def __init__(self, factory: Callable[..., Any]) -> None:
        self.socket = factory

    def __getattr__(self, name: str) -> Any:
        return getattr(socket, name)


def failing_for(families: set[int], error: Exception) -> Callable[..., socket.socket]:
    real = socket.socket

    def factory(family: int = -1, *args: Any, **kw: Any) -> socket.socket:
        if family in families:
            raise error
        return real(family, *args, **kw)

    return factory


@contextmanager
def silent_listener() -> Iterator[int]:
    """Accepts TCP connections and never says anything (a peer that stalls)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                held.append(listener.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()
        for connection in held:
            connection.close()


# --- finding 2: a failed thread start must not leave the resolver stuck ---------------------------


def test_a_failed_thread_start_is_a_dns_failure_and_the_resolver_recovers(monkeypatch) -> None:
    resolver = FakeResolver(lambda: loopback(1))
    single = SingleFlightResolver(resolver)
    real_start = threading.Thread.start
    failed: list[int] = []

    def start(self: threading.Thread) -> None:
        if self.name == "jwks-dns" and not failed:
            failed.append(1)
            raise RuntimeError("can't start new thread: secret-detail")
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)

    with pytest.raises(KeyUnavailable) as caught:
        single.lookup(NAME, 443, time.monotonic() + 2)

    assert caught.value.category is FailureCategory.DNS
    assert str(caught.value) == ""
    assert caught.value.__cause__ is None
    assert "secret" not in repr(caught.value)
    assert single._current is None  # noqa: SLF001 - nothing was published for a dead thread
    assert resolver.calls == 0

    assert single.lookup(NAME, 443, time.monotonic() + 2) == loopback(1)  # threads work again
    assert resolver.calls == 1


def test_a_thread_start_failure_after_an_abandoned_lookup_does_not_wedge_the_resolver(
    monkeypatch,
) -> None:
    resolver = FakeResolver(lambda: loopback(1))
    resolver.block = True
    single = SingleFlightResolver(resolver)
    assert category_of(lambda: single.lookup(NAME, 443, time.monotonic() + 0.05)) is (
        FailureCategory.DEADLINE
    )
    resolver.release.set()
    assert wait_until(lambda: resolver.active == 0)
    resolver.block = False
    real_start = threading.Thread.start
    failed: list[int] = []

    def start(self: threading.Thread) -> None:
        if self.name == "jwks-dns" and not failed:
            failed.append(1)
            raise RuntimeError("no threads")
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)

    assert category_of(lambda: single.lookup(NAME, 443, time.monotonic() + 2)) is (
        FailureCategory.DNS
    )
    assert single.lookup(NAME, 443, time.monotonic() + 2) == loopback(1)


def test_a_fetch_recovers_after_a_failed_thread_start(server: Server, monkeypatch) -> None:  # noqa: F811
    resolver = FakeResolver(lambda: loopback(server.httpd.server_port))
    fetcher = JwksFetcher(SingleFlightResolver(resolver))
    real_start = threading.Thread.start
    failed: list[int] = []

    def start(self: threading.Thread) -> None:
        if self.name == "jwks-dns" and not failed:
            failed.append(1)
            raise RuntimeError("no threads")
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)

    assert category_of(lambda: fetcher(named(server.url), 3.0, MAX)) is FailureCategory.DNS
    assert fetcher(named(server.url), 3.0, MAX) == server.body


# --- the resolver state machine under contention ---------------------------------------


def test_many_threads_never_run_two_lookups_at_once() -> None:
    resolver = FakeResolver(lambda: (time.sleep(0.005), loopback(1))[1])
    single = SingleFlightResolver(resolver)
    results: list[list[Any]] = []
    failures: list[FailureCategory] = []

    def work(index: int) -> None:
        host = f"h{index % 2}.test"  # two names, so callers also meet a lookup for the other one
        for _ in range(20):
            try:
                results.append(single.lookup(host, 443, time.monotonic() + 10))
            except KeyUnavailable as error:
                failures.append(error.category)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert failures == []
    assert len(results) == 12 * 20
    assert all(result == loopback(1) for result in results)
    assert resolver.max_active == 1
    assert resolver.active == 0


def test_callers_that_time_out_never_wedge_or_multiply_the_lookup() -> None:
    """Many callers whose deadline is shorter than the lookup, repeatedly, and then recovery."""
    resolver = FakeResolver(lambda: (time.sleep(0.05), loopback(1))[1])
    single = SingleFlightResolver(resolver)
    outcomes: list[str] = []

    def work(index: int) -> None:
        host = f"h{index % 2}.test"
        for _ in range(30):
            try:
                single.lookup(host, 443, time.monotonic() + 0.01)
                outcomes.append("ok")
            except KeyUnavailable as error:
                outcomes.append(error.category.value)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert set(outcomes) <= {"ok", "deadline"}
    assert "deadline" in outcomes
    assert resolver.max_active == 1
    # Whatever happened above, the next caller with time to spare gets an answer.
    assert single.lookup("h0.test", 443, time.monotonic() + 5) == loopback(1)
    assert wait_until(lambda: resolver.active == 0)
    assert resolver.max_active == 1


# --- finding 1: fallback to the next address ----------------------------------------------------


def test_a_socket_that_cannot_be_created_moves_on_to_the_next_address(
    server: Server,  # noqa: F811
    monkeypatch,
) -> None:
    port = server.httpd.server_port
    ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))
    resolver = FakeResolver(lambda: [ipv6, *loopback(port)])
    unsupported = OSError(97, "Address family not supported by protocol")
    monkeypatch.setattr(
        auth_module, "socket", SocketShim(failing_for({socket.AF_INET6}, unsupported))
    )

    body = JwksFetcher(SingleFlightResolver(resolver))(named(server.url), 3.0, MAX)

    assert body == server.body


def test_any_error_creating_a_socket_moves_on_and_never_escapes(
    server: Server,  # noqa: F811
    monkeypatch,
) -> None:
    port = server.httpd.server_port
    ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))
    resolver = FakeResolver(lambda: [ipv6, *loopback(port)])
    monkeypatch.setattr(
        auth_module, "socket", SocketShim(failing_for({socket.AF_INET6}, ValueError("secret")))
    )

    assert JwksFetcher(SingleFlightResolver(resolver))(named(server.url), 3.0, MAX) == server.body


def test_when_no_socket_can_be_created_the_failure_is_connect(monkeypatch) -> None:
    resolver = FakeResolver(lambda: loopback(9))
    monkeypatch.setattr(
        auth_module,
        "socket",
        SocketShim(failing_for({socket.AF_INET}, OSError(24, "Too many open files: secret"))),
    )

    with pytest.raises(KeyUnavailable) as caught:
        JwksFetcher(SingleFlightResolver(resolver))(f"http://{NAME}:9/jwks", 3.0, MAX)

    assert caught.value.category is FailureCategory.CONNECT
    assert str(caught.value) == ""
    assert "secret" not in repr(caught.value)


def test_a_malformed_resolver_answer_is_a_connect_failure_not_a_crash() -> None:
    resolver = FakeResolver(lambda: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", "not-an-address")])

    assert category_of(
        lambda: JwksFetcher(SingleFlightResolver(resolver))(f"http://{NAME}:9/jwks", 3.0, MAX)
    ) is (FailureCategory.CONNECT)


# --- finding 1: unexpected exceptions -----------------------------------------------------


class Raising:
    """A fetch that raises the same prepared exception every time, and counts calls."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, url: str, timeout: float, max_bytes: int) -> bytes:
        self.calls += 1
        raise self.error


UNEXPECTED = [
    OSError("secret-host.internal: connection refused"),
    RuntimeError("secret"),
    AttributeError("'NoneType' object has no attribute 'secret'"),
    ValueError("secret"),
    UnicodeError("secret"),
]


@pytest.mark.parametrize("error", UNEXPECTED, ids=lambda e: type(e).__name__)
def test_an_unexpected_fetch_exception_is_categorised_and_triggers_the_backoff(
    error: Exception,
) -> None:
    clock = Clock()
    fetch = Raising(error)
    keys = JwksKeyProvider(
        "https://example.invalid/jwks", failure_backoff=5, fetch=fetch, clock=clock
    )

    with pytest.raises(KeyUnavailable) as first:
        keys.get_key(a.KID)

    assert first.value.category is FailureCategory.BODY
    assert str(first.value) == ""
    assert first.value.__cause__ is None
    assert "secret" not in repr(first.value) + str(first.value.category)
    assert fetch.calls == 1

    clock.now += 4  # inside the backoff: no new fetch, same fixed category
    assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.BODY
    assert fetch.calls == 1

    clock.now += 2  # backoff over: one more attempt
    assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.BODY
    assert fetch.calls == 2


def test_an_unexpected_exception_on_an_expired_cache_never_serves_stale_keys() -> None:
    clock = Clock()
    good = jwks((a.KID, a.EC_KEY))

    class Flaky:
        calls = 0

        def __call__(self, url: str, timeout: float, max_bytes: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return good
            raise RuntimeError("secret")

    keys = JwksKeyProvider("https://example.invalid/jwks", ttl=300, fetch=Flaky(), clock=clock)
    assert keys.get_key(a.KID) is not None

    clock.now += 301

    assert category_of(lambda: keys.get_key(a.KID)) is FailureCategory.BODY


def test_an_unexpected_exception_in_the_real_fetch_is_categorised(monkeypatch) -> None:
    with silent_listener() as port:
        url = f"http://127.0.0.1:{port}/jwks"

        def boom(*args: Any, **kw: Any) -> None:
            raise RuntimeError("secret-internal-detail")

        # The watchdog thread cannot be started: without it the deadline could not be enforced.
        monkeypatch.setattr(threading.Timer, "start", boom)

        with pytest.raises(KeyUnavailable) as caught:
            JwksFetcher()(url, 3.0, MAX)

    assert caught.value.category is FailureCategory.BODY
    assert str(caught.value) == ""
    assert "secret" not in repr(caught.value)


def test_a_failure_while_building_the_tls_context_is_a_tls_failure(monkeypatch) -> None:
    def boom(*args: Any, **kw: Any) -> None:
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(auth_module.ssl, "create_default_context", boom)
    with silent_listener() as port:
        with pytest.raises(KeyUnavailable) as caught:
            JwksFetcher()(f"https://127.0.0.1:{port}/jwks", 3.0, MAX)

    assert caught.value.category is FailureCategory.TLS
    assert "secret" not in repr(caught.value)


def test_the_watchdog_race_in_the_tls_handshake_cannot_escape_as_an_attribute_error(
    monkeypatch,
) -> None:
    """At the deadline the watchdog clears the TLS object (ssl.SSLSocket.shutdown), so a
    handshake that has not started yet can see `None` and raise AttributeError."""

    def raced(self: ssl.SSLSocket, *args: Any, **kw: Any) -> None:
        raise AttributeError("'NoneType' object has no attribute 'do_handshake'")

    monkeypatch.setattr(ssl.SSLSocket, "do_handshake", raced)
    with silent_listener() as port:
        category = category_of(lambda: JwksFetcher()(f"https://127.0.0.1:{port}/jwks", 3.0, MAX))

    assert category is FailureCategory.TLS


def test_the_api_answers_a_generic_503_for_an_unexpected_fetch_exception(log_format: str) -> None:
    settings = a.auth_settings(log_level="DEBUG", log_format=log_format)
    stream = io.StringIO()
    keys = JwksKeyProvider(
        "https://secret-project.example.invalid/jwks",
        fetch=Raising(OSError("secret-host.internal refused")),
        clock=Clock(),
    )
    application = create_app(settings, keys)
    configure_logging(settings, stream)
    client = TestClient(application, raise_server_exceptions=False)
    token = a.make_token()

    response = client.get(f"{API_V1_PREFIX}/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    output = stream.getvalue()
    assert "auth_keys_unavailable_body" in output
    for secret in (token, "secret-host", "secret-project", "refused"):
        assert secret not in output
        assert secret not in response.text


def test_the_diagnostic_tool_prints_a_fixed_message_for_an_unexpected_failure() -> None:
    lines: list[str] = []
    settings = make(supabase_url="https://secret-project.example.invalid")

    def failing(url: str, timeout: float, max_bytes: int) -> bytes:
        raise RuntimeError("secret-host.internal exploded")

    code = check_token.run(settings, fetch=failing, out=lines.append)

    assert code == 2
    assert lines[-1] == check_token.KEY_FAILURE_MESSAGES[FailureCategory.BODY]
    joined = "\n".join(lines)
    assert "secret-host" not in joined
    assert "exploded" not in joined


# --- finding 3: a stalled operation versus the total deadline -------------------------------------


def test_a_stall_longer_than_three_seconds_inside_a_larger_deadline_is_body_not_deadline(
    server: Server,  # noqa: F811
) -> None:
    assert auth_module.JWKS_READ_TIMEOUT_SECONDS == 3.0
    server.mode = "slow_ok"
    server.delay = 3.6  # no byte for longer than the 3 s read limit; the 10 s deadline is far off
    started = time.monotonic()

    category = category_of(lambda: JwksFetcher()(server.url, 10.0, MAX))

    elapsed = time.monotonic() - started
    assert category is FailureCategory.BODY
    assert 2.5 <= elapsed < 3.0 + TOLERANCE, f"took {elapsed:.2f}s"  # the read limit, not 10 s


def test_a_read_stall_is_reported_under_its_phase_when_time_remains(
    server: Server,  # noqa: F811
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_module, "JWKS_READ_TIMEOUT_SECONDS", 0.4)
    server.mode = "slow_ok"
    server.delay = 1.5
    started = time.monotonic()

    assert category_of(lambda: JwksFetcher()(server.url, 5.0, MAX)) is FailureCategory.BODY

    assert time.monotonic() - started < 0.4 + TOLERANCE


def test_a_tls_handshake_stall_is_tls_when_time_remains(monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "JWKS_READ_TIMEOUT_SECONDS", 0.4)
    with silent_listener() as port:
        started = time.monotonic()

        category = category_of(lambda: JwksFetcher()(f"https://127.0.0.1:{port}/jwks", 5.0, MAX))

    assert category is FailureCategory.TLS
    assert time.monotonic() - started < 0.4 + TOLERANCE


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_stall_that_hits_the_total_deadline_is_always_deadline(
    scheme: str,
    server: Server,  # noqa: F811
) -> None:
    """The read limit (3 s) is longer than the deadline, so the deadline is what expires. Run it
    several times: the socket timeout and the watchdog fire at nearly the same moment, and the
    answer must not depend on which one wins."""
    server.mode = "slow_ok"
    server.delay = 2.0
    with silent_listener() as silent_port:
        port = silent_port if scheme == "https" else server.httpd.server_port
        for _ in range(5):
            started = time.monotonic()

            category = category_of(
                lambda: JwksFetcher()(f"{scheme}://127.0.0.1:{port}/jwks", 0.4, MAX)
            )

            assert category is FailureCategory.DEADLINE
            assert time.monotonic() - started < 0.4 + TOLERANCE


def test_a_slow_response_whose_total_time_exceeds_the_read_limit_is_accepted(
    server: Server,  # noqa: F811
    monkeypatch,
) -> None:
    """Each wait is shorter than the read limit, the whole exchange is longer than it, and it is
    still inside the total deadline: it must be accepted."""
    monkeypatch.setattr(auth_module, "JWKS_READ_TIMEOUT_SECONDS", 1.0)
    server.mode = "split_ok"
    server.delay = 0.7  # two waits of 0.7 s: 1.4 s in total, over the 1.0 s read limit
    started = time.monotonic()

    body = JwksFetcher()(server.url, 5.0, MAX)

    assert body == server.body
    assert time.monotonic() - started >= 1.4


def test_the_connect_limit_is_the_deadline_only_when_the_deadline_is_the_binding_limit(
    monkeypatch,
) -> None:
    class TimingOut:
        def __init__(self, *args: Any) -> None:
            pass

        def settimeout(self, value: float | None) -> None:
            pass

        def connect(self, address: Any) -> None:
            raise TimeoutError("timed out")

        def close(self) -> None:
            pass

    monkeypatch.setattr(auth_module, "socket", SocketShim(TimingOut))

    # 30 s left, 3 s limit: the attempt timed out on its own limit.
    with pytest.raises(KeyUnavailable) as own:
        auth_module._connect(loopback(9), time.monotonic() + 30)  # noqa: SLF001
    assert own.value.category is FailureCategory.CONNECT

    # 1 s left: the deadline was the limit, so it is the deadline that expired.
    with pytest.raises(KeyUnavailable) as total:
        auth_module._connect(loopback(9), time.monotonic() + 1)  # noqa: SLF001
    assert total.value.category is FailureCategory.DEADLINE


# --- the classification rules, without depending on timing ---------------------------------------


class StalledSocket:
    """A connected socket whose first read times out at once (as if the peer had stalled)."""

    def __init__(self, *args: Any) -> None:
        pass

    def settimeout(self, value: float | None) -> None:
        pass

    def connect(self, address: Any) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        pass

    def makefile(self, *args: Any, **kw: Any) -> Any:
        class Stalled(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, buffer: Any) -> int:
                raise TimeoutError("timed out")

        return io.BufferedReader(Stalled())

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (2.0, FailureCategory.DEADLINE),  # less than a read limit left: the deadline was binding
        (30.0, FailureCategory.BODY),  # the read limit was binding: a stalled peer
    ],
)
def test_a_read_timeout_is_the_deadline_only_when_the_deadline_was_the_binding_limit(
    monkeypatch, budget: float, expected: FailureCategory
) -> None:
    """The socket timeout can fire a hair before the watchdog and the clock reach the deadline.
    If the deadline set that timeout, the answer must still be `deadline`."""
    monkeypatch.setattr(auth_module, "socket", SocketShim(StalledSocket))
    resolver = FakeResolver(lambda: loopback(9))

    category = category_of(
        lambda: JwksFetcher(SingleFlightResolver(resolver))(f"http://{NAME}:9/jwks", budget, MAX)
    )

    assert category is expected


def test_the_outer_guard_turns_anything_unexpected_into_a_categorised_failure(
    monkeypatch,
) -> None:
    def explode(self: Any, url: str, deadline: float, max_bytes: int) -> bytes:
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(JwksFetcher, "_fetch", explode)

    with pytest.raises(KeyUnavailable) as caught:
        JwksFetcher()("https://example.invalid/jwks", 3.0, MAX)

    assert caught.value.category is FailureCategory.BODY
    assert str(caught.value) == ""
    assert caught.value.__cause__ is None
    assert "secret" not in repr(caught.value)
    # A deadline that has already passed still takes precedence.
    with pytest.raises(KeyUnavailable) as late:
        JwksFetcher()("https://example.invalid/jwks", -1.0, MAX)
    assert late.value.category is FailureCategory.DEADLINE


@pytest.mark.parametrize("url", ["http://[::1/jwks", "ftp://example.invalid/jwks", "https:///jwks"])
def test_a_malformed_url_is_a_connect_failure_not_a_crash(url: str) -> None:
    assert category_of(lambda: JwksFetcher()(url, 3.0, MAX)) is FailureCategory.CONNECT
