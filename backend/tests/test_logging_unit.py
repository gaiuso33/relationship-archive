"""Unit tests for structured logging and redaction, in both output formats."""

import io
import json
import logging
import re

import pytest

from app.core.config import Settings
from app.core.logging import (
    PrivacyFilter,
    configure_logging,
    redact,
    summarize_exception,
)
from tests import support

app_logger = logging.getLogger("app.test")


def lines(stream: io.StringIO) -> list[str]:
    return [line for line in stream.getvalue().splitlines() if line]


# --- redaction (supplementary layer) -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"Authorization: Bearer {support.BEARER_VALUE}", support.BEARER_VALUE),
        (f"sent header authorization={support.BEARER_VALUE}", support.BEARER_VALUE),
        (f"token is Bearer {support.BEARER_VALUE} ok", support.BEARER_VALUE),
        (f"Basic {support.BEARER_VALUE}", support.BEARER_VALUE),
        (f"jwt {support.JWT_VALUE} seen", support.JWT_VALUE),
        (f"Cookie: session={support.JWT_VALUE}; theme=dark", support.JWT_VALUE),
        (f"Set-Cookie: sid={support.BEARER_VALUE}", support.BEARER_VALUE),
        (f"password={support.NOTE}", support.NOTE),
        (f'{{"api_key": "{support.NOTE}"}}', support.NOTE),
        (f"access_token: {support.NOTE}", support.NOTE),
        (f"DATABASE_URL=postgresql://u:{support.NOTE}@h/d", support.NOTE),
        (f"postgresql+psycopg://user:{support.NOTE}@127.0.0.1/db", support.NOTE),
        (f"GET /api/v1/search?q={support.QUERY_VALUE}&x=1", support.QUERY_VALUE),
        (f"https://example.invalid/path?email={support.EMAIL}", support.EMAIL),
        (f"contact {support.EMAIL} today", support.EMAIL),
    ],
)
def test_redact_removes_sensitive_patterns(text: str, secret: str) -> None:
    redacted = redact(text)

    assert secret not in redacted
    assert "[REDACTED" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "request completed",
        "GET /api/v1/health 200",
        "started server process 1234",
        "route /api/v1/people/{person_id} took 3.2 ms",
        "",
    ],
)
def test_redact_leaves_harmless_text_unchanged(text: str) -> None:
    assert redact(text) == text


def test_redact_is_idempotent() -> None:
    once = redact(f"Authorization: Bearer {support.BEARER_VALUE} and {support.EMAIL}")
    assert redact(once) == once


# --- the allowlisted structure (primary boundary) ----------------------------------------


def test_unknown_extra_fields_are_dropped(configured_logging: io.StringIO) -> None:
    app_logger.info(
        "something happened",
        extra={
            "event": "something_happened",
            "note_text": support.NOTE,
            "person_name": support.NAME,
            "authorization": support.BEARER_VALUE,
            "email": support.EMAIL,
            "status": 200,
        },
    )

    output = configured_logging.getvalue()
    for value in (support.NOTE, support.NAME, support.BEARER_VALUE, support.EMAIL):
        assert value not in output
    assert "something_happened" in output
    assert "200" in output


def test_invalid_values_in_allowed_fields_are_dropped(configured_logging: io.StringIO) -> None:
    app_logger.info(
        "x",
        extra={
            "route": f"/people/{support.NAME}?email={support.EMAIL}",
            "error_type": f"Boom: {support.NOTE}",
            "error_id": support.NOTE,
            "frames": [f"{support.NOTE}", "app/x.py:1 in fn"],
            "sqlstate": support.NOTE,
            "method": support.NOTE,
            "status": "200; " + support.NOTE,
        },
    )

    output = configured_logging.getvalue()
    assert support.NOTE not in output
    assert support.NAME not in output
    assert support.EMAIL not in output
    assert "app/x.py:1 in fn" in output
    assert "OTHER" in output


def test_application_log_arguments_are_never_interpolated(
    configured_logging: io.StringIO,
) -> None:
    app_logger.info("saved note %s for %s", support.NOTE, support.NAME)
    app_logger.warning("lookup %(who)s", {"who": support.NAME})

    output = configured_logging.getvalue()
    assert support.NOTE not in output
    assert support.NAME not in output
    assert "saved note %s for %s" in output


def test_message_text_is_redacted_even_when_a_developer_slips(
    configured_logging: io.StringIO,
) -> None:
    app_logger.info(
        f"calling with Authorization: Bearer {support.BEARER_VALUE} for {support.EMAIL}"
    )

    output = configured_logging.getvalue()
    assert support.BEARER_VALUE not in output
    assert support.EMAIL not in output


def test_one_entry_is_one_line(configured_logging: io.StringIO) -> None:
    app_logger.info("first line\nsecond line\r\nthird\tline\x1b[31m")

    assert len(lines(configured_logging)) == 1
    assert "\x1b" not in configured_logging.getvalue()


def test_json_entries_are_valid_json(log_stream: io.StringIO) -> None:
    configure_logging(Settings(_env_file=None, log_level="DEBUG", log_format="json"), log_stream)

    app_logger.info("hello", extra={"event": "hello", "status": 204})

    entry = json.loads(lines(log_stream)[0])
    assert entry["message"] == "hello"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.test"


def _raise_with_sensitive_data() -> None:
    secret_local = support.NOTE  # noqa: F841  (a local that must never be logged)
    raise ValueError(f"cannot store {support.NOTE} for {support.NAME} {support.EMAIL}")


def test_exceptions_are_logged_as_type_and_frames_only(
    configured_logging: io.StringIO,
) -> None:
    try:
        _raise_with_sensitive_data()
    except ValueError:
        app_logger.exception("failed")  # attaches exc_info, as a careless developer would

    output = configured_logging.getvalue()
    for value in (support.NOTE, support.NAME, support.EMAIL):
        assert value not in output
    assert "builtins.ValueError" in output
    assert "tests/test_logging_unit.py:" in output
    assert "_raise_with_sensitive_data" in output
    assert "Traceback" not in output
    assert "raise ValueError" not in output  # no source text
    assert "secret_local" not in output  # no locals


def test_stack_frames_expose_no_absolute_paths() -> None:
    try:
        _raise_with_sensitive_data()
    except ValueError as error:
        summary = summarize_exception(error)

    assert summary["frames"]
    for frame in summary["frames"]:
        path = frame.split(":", 1)[0]
        assert not path.startswith("/"), frame
        assert "home" not in path.split("/")[:1], frame
        assert "site-packages" not in path, frame


def test_stack_info_is_not_logged(configured_logging: io.StringIO) -> None:
    app_logger.info("with stack", stack_info=True)

    assert "test_stack_info_is_not_logged" not in configured_logging.getvalue()


def test_sqlstate_is_kept_for_operations() -> None:
    class FakeDriverError(Exception):
        sqlstate = "23505"

    try:
        raise FakeDriverError(support.DB_VALUE)
    except FakeDriverError as error:
        summary = summarize_exception(error)

    assert summary["sqlstate"] == "23505"
    assert support.DB_VALUE not in json.dumps(summary)


# --- library loggers ---------------------------------------------------------------------


def _record(name: str, level: int, message: str = "select * from notes") -> logging.LogRecord:
    return logging.LogRecord(name, level, "f", 1, message, None, None)


def test_sql_engine_and_access_log_records_are_dropped_at_every_level() -> None:
    filt = PrivacyFilter()

    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        assert not filt.filter(_record("sqlalchemy.engine", level))
        assert not filt.filter(_record("sqlalchemy.engine.Engine", level))
        assert not filt.filter(_record("uvicorn.access", level))


def test_other_library_records_need_warning_and_never_keep_their_message(
    configured_logging: io.StringIO,
) -> None:
    filt = PrivacyFilter()
    assert not filt.filter(_record("httpx", logging.INFO))
    assert filt.filter(_record("httpx", logging.WARNING))
    assert filt.filter(_record("app.errors", logging.DEBUG))

    logging.getLogger("httpx").warning(f"request to {support.EMAIL} failed: {support.NOTE}")
    logging.getLogger("httpx").info(f"info {support.NOTE}")

    output = configured_logging.getvalue()
    assert support.EMAIL not in output
    assert support.NOTE not in output
    assert "third-party log message suppressed" in output
    assert "httpx" in output
    assert len(lines(configured_logging)) == 1


def test_non_text_application_message_is_dropped(configured_logging: io.StringIO) -> None:
    # `logger.error(exc)` is a common slip: the string form of an exception is its message.
    app_logger.error(ValueError(f"cannot store {support.NOTE}"))

    output = configured_logging.getvalue()
    assert support.NOTE not in output
    assert "non-text log message dropped" in output


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("/home/alice/work/relationship-archive/backend/app/api/health.py", None),
        ("/home/alice/.venv/lib/python3.12/site-packages/fastapi/routing.py", "fastapi/routing.py"),
        ("/opt/venv/lib/python3.12/dist-packages/pkg/mod.py", "pkg/mod.py"),
        ("/home/alice/Documents/private-folder/script.py", "<other>/script.py"),
        ("/tmp/pytest-of-alice/pytest-3/test_x0/helper.py", "<other>/helper.py"),
        ("<string>", "<other>/<string>"),
    ],
)
def test_frame_paths_reveal_no_directory_layout(filename: str, expected: str | None) -> None:
    from app.core.logging import _safe_path

    result = _safe_path(filename)

    if expected is not None:
        assert result == expected
    for leaked in ("alice", "home", "private-folder", "pytest-of", "work"):
        assert leaked not in result


def test_project_and_standard_library_paths_are_relative() -> None:
    import sysconfig

    from app.core.logging import BACKEND_DIR, _safe_path

    assert _safe_path(str(BACKEND_DIR / "app" / "api" / "health.py")) == "app/api/health.py"
    stdlib = sysconfig.get_paths()["stdlib"]
    assert _safe_path(f"{stdlib}/asyncio/base_events.py") == "<stdlib>/asyncio/base_events.py"


def test_debug_level_applies_to_application_loggers_only(
    test_settings: Settings, log_stream: io.StringIO
) -> None:
    assert test_settings.log_level == "DEBUG"
    configure_logging(test_settings, log_stream)

    assert logging.getLogger("app").getEffectiveLevel() == logging.DEBUG
    # The SQL engine loggers are silenced completely, whatever LOG_LEVEL says.
    assert logging.getLogger("sqlalchemy.engine").getEffectiveLevel() > logging.CRITICAL
    assert logging.getLogger("sqlalchemy.engine.Engine").getEffectiveLevel() > logging.CRITICAL
    assert logging.getLogger("sqlalchemy").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_is_idempotent(test_settings: Settings, log_stream: io.StringIO) -> None:
    root = logging.getLogger()
    foreign = [h for h in root.handlers if not getattr(h, "_relationship_archive_handler", False)]

    configure_logging(test_settings, log_stream)
    configure_logging(test_settings, log_stream)

    ours = [h for h in root.handlers if getattr(h, "_relationship_archive_handler", False)]
    assert len(ours) == 1
    # Handlers installed by someone else (for example pytest) are left alone.
    assert [h for h in root.handlers if h not in ours] == foreign
    app_logger.info("once")
    assert len(lines(log_stream)) == 1


def test_uvicorn_handlers_are_replaced(test_settings: Settings, log_stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.access").addHandler(logging.StreamHandler())
    logging.getLogger("uvicorn.error").addHandler(logging.StreamHandler())

    configure_logging(test_settings, log_stream)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).handlers == []
    assert logging.getLogger("uvicorn.access").disabled is True
    logging.getLogger("uvicorn.access").disabled = False
    logging.getLogger("uvicorn.access").info(f'1.2.3.4:5 - "GET /x?t={support.BEARER_VALUE}"')
    assert support.BEARER_VALUE not in log_stream.getvalue()


def test_uvicorn_messages_are_redacted(configured_logging: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").info("Uvicorn running on http://%s:%d", "127.0.0.1", 8000)
    logging.getLogger("uvicorn.error").warning(
        f"bad request Authorization: Bearer {support.BEARER_VALUE}"
    )

    output = configured_logging.getvalue()
    assert "Uvicorn running on http://127.0.0.1:8000" in output
    assert support.BEARER_VALUE not in output
    assert re.search(r"REDACTED", output)
