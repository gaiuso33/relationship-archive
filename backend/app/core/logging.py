"""Privacy-safe logging.

The design has three layers, in order of importance:

1. **Allowlisted structured fields (the primary boundary).** A log entry contains only
   fixed fields (time, level, logger, message, request id) plus a short allowlist of
   validated extras. Any other `extra` key is dropped, so a developer cannot put a name,
   note text or token into a log entry by accident. Both output formats are built from
   the same entry, so they have identical protections.
2. **No exception messages, arguments, tracebacks or source lines.** An exception is
   logged as its type and a list of `file:line in function` frames with project-relative
   paths. Log calls from application code (`app.*` loggers) never interpolate arguments,
   and the message of any third-party logger is replaced by a fixed notice, except for the
   two uvicorn loggers that print start-up lines.
3. **Pattern redaction (supplementary).** The message text is scrubbed for bearer
   tokens, JWTs, credential-like key/value pairs, URL credentials and queries, and email
   addresses. Patterns cannot recognise names or note text, so this layer only catches
   what layers 1 and 2 missed.

See docs/logging-and-errors.md for the conventions contributors follow.
"""

import json
import logging
import re
import sys
import sysconfig
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
REDACTED = "[REDACTED]"
MAX_FRAMES = 15
MAX_MESSAGE_LENGTH = 2000
SUPPRESSED_MESSAGE = "third-party log message suppressed"
NON_TEXT_MESSAGE = "non-text log message dropped"
# Third-party loggers whose message text is kept (after redaction): uvicorn's start-up and
# shutdown lines. Every other third-party message is replaced by SUPPRESSED_MESSAGE.
MESSAGE_PASSTHROUGH_LOGGERS = frozenset({"uvicorn", "uvicorn.error"})
# Loggers whose records are always dropped, at any level. Matched by name and by prefix.
ALWAYS_BLOCKED_LOGGERS = ("uvicorn.access", "sqlalchemy.engine")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_TYPE_RE = re.compile(r"^[A-Za-z0-9_.]{1,120}$")
_EVENT_RE = re.compile(r"^[a-z0-9_.]{1,64}$")
_ROUTE_RE = re.compile(r"^[A-Za-z0-9/_{}.:\-]{1,200}$")
_HEX_RE = re.compile(r"^[0-9a-f]{12,32}$")
_SQLSTATE_RE = re.compile(r"^[0-9A-Z]{5}$")
_FRAME_RE = re.compile(r"^[A-Za-z0-9_./<>\-]{1,160}:\d{1,6} in [A-Za-z0-9_<>.]{1,80}$")

# --- redaction (supplementary layer) ---------------------------------------------------

_SENSITIVE_NAME = (
    r"[A-Za-z0-9_.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|credential|"
    r"database[_\-]?url|dsn|session|cookie)[A-Za-z0-9_.\-]*"
)
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key)"
            r"\s*[:=]\s*[^\r\n]+"
        ),
        rf"\1: {REDACTED}",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{4,}"), rf"\1 {REDACTED}"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{3,}\.[A-Za-z0-9_\-]{3,}(?:\.[A-Za-z0-9_\-]*)?"),
        REDACTED,
    ),
    (
        re.compile(
            rf"(?i)([\"']?)\b({_SENSITIVE_NAME})\1(\s*[:=]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;&}\])]+)"
        ),
        rf"\1\2\1\3{REDACTED}",
    ),
    (re.compile(r"(?i)(://)[^\s/@:]+(?::[^\s/@]*)?@"), rf"\1{REDACTED}@"),
    (re.compile(r"((?:https?://[^\s?#\"']*|/[^\s?#\"']*))\?[^\s#\"']+"), rf"\1?{REDACTED}"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
)


def redact(text: str) -> str:
    """Scrub credential-like and personal-looking patterns from free text (best effort)."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _single_line(text: str) -> str:
    """Neutralise line breaks and control characters so one entry is always one line."""
    return "".join(ch if ch.isprintable() else " " for ch in text.replace("\t", " "))


# --- exception summaries ---------------------------------------------------------------


_STDLIB_DIR = Path(sysconfig.get_paths()["stdlib"])


def _safe_path(filename: str) -> str:
    """Return a path that reveals nothing about the machine.

    Project files are shown relative to `backend/` (`app/api/health.py`), installed
    packages relative to site-packages (`fastapi/routing.py`), the standard library as
    `<stdlib>/...`, and anything else as `<other>/<file name>`. A home directory, a virtual
    environment location or a temporary directory never appears.
    """
    path = Path(filename)
    parts = path.parts
    for marker in ("site-packages", "dist-packages"):
        if marker in parts:
            return "/".join(parts[parts.index(marker) + 1 :])
    for base, prefix in ((BACKEND_DIR, ""), (_STDLIB_DIR, "<stdlib>/")):
        try:
            return prefix + path.relative_to(base).as_posix()
        except ValueError:
            continue
    return f"<other>/{path.name}"


def summarize_exception(exc: BaseException) -> dict[str, Any]:
    """Describe an exception without its message, arguments, locals or source text."""
    frames: list[str] = []
    tb: TracebackType | None = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        frames.append(f"{_safe_path(code.co_filename)}:{tb.tb_lineno} in {code.co_name}")
        tb = tb.tb_next
    summary: dict[str, Any] = {
        "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "frames": frames[-MAX_FRAMES:],
    }
    # SQLSTATE is a fixed 5-character code (for example 23505), safe and useful to log.
    sqlstate = getattr(getattr(exc, "orig", exc), "sqlstate", None)
    if isinstance(sqlstate, str):
        summary["sqlstate"] = sqlstate
    return summary


# --- structured entry ------------------------------------------------------------------


def _clean_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Pick the allowlisted extras from a record, dropping anything invalid."""
    extras: dict[str, Any] = {}
    get = record.__dict__.get

    if isinstance(event := get("event"), str) and _EVENT_RE.match(event):
        extras["event"] = event
    if isinstance(method := get("method"), str):
        extras["method"] = method if method in _HTTP_METHODS else "OTHER"
    if isinstance(route := get("route"), str) and _ROUTE_RE.match(route):
        extras["route"] = route
    if isinstance(status := get("status"), int) and not isinstance(status, bool):
        if 100 <= status <= 599:
            extras["status"] = status
    duration = get("duration_ms")
    if isinstance(duration, int | float) and not isinstance(duration, bool):
        extras["duration_ms"] = round(float(duration), 2)
    if isinstance(error_type := get("error_type"), str) and _TYPE_RE.match(error_type):
        extras["error_type"] = error_type
    if isinstance(error_id := get("error_id"), str) and _HEX_RE.match(error_id):
        extras["error_id"] = error_id
    if isinstance(sqlstate := get("sqlstate"), str) and _SQLSTATE_RE.match(sqlstate):
        extras["sqlstate"] = sqlstate
    if isinstance(frames := get("frames"), list):
        extras["frames"] = [f for f in frames if isinstance(f, str) and _FRAME_RE.match(f)][
            :MAX_FRAMES
        ]
    return extras


def is_application_logger(name: str) -> bool:
    return name == "app" or name.startswith("app.")


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def build_entry(record: logging.LogRecord) -> dict[str, Any]:
    """Build the one safe representation of a record that every formatter uses."""
    if is_application_logger(record.name):
        # Application code writes static event text. Arguments are never interpolated, and
        # a non-text message (for example an exception object) is dropped, because its
        # string form would carry the exception message.
        message = record.msg if isinstance(record.msg, str) else NON_TEXT_MESSAGE
    elif record.name in MESSAGE_PASSTHROUGH_LOGGERS:
        message = record.getMessage()
    else:
        message = SUPPRESSED_MESSAGE
    entry: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "logger": record.name,
        "message": redact(_single_line(message))[:MAX_MESSAGE_LENGTH],
    }
    request_id = request_id_var.get()
    if request_id is not None and _HEX_RE.match(request_id):
        entry["request_id"] = request_id

    extras = _clean_extras(record)
    # An exception attached by a library is reduced to type and frames, never its message.
    if record.exc_info and record.exc_info[1] is not None and "error_type" not in extras:
        extras.update(_clean_extras_from_summary(summarize_exception(record.exc_info[1])))
    entry.update(extras)
    return entry


def _clean_extras_from_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    probe = logging.LogRecord("probe", logging.ERROR, "", 0, "", None, None)
    probe.__dict__.update(summary)
    return _clean_extras(probe)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(build_entry(record), ensure_ascii=True, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = build_entry(record)
        head = f"{entry.pop('timestamp')} {entry.pop('level'):<8} {entry.pop('logger')}"
        message = entry.pop("message")
        fields = " ".join(
            f"{key}={json.dumps(value) if key == 'frames' else value}"
            for key, value in entry.items()
        )
        return f"{head} {message}" + (f" {fields}" if fields else "")


class PrivacyFilter(logging.Filter):
    """Decide which records may be written at all.

    * Application records (`app.*`) and uvicorn's own start-up lines pass at any level.
    * SQLAlchemy engine records and uvicorn's access log are always dropped. They can carry
      SQL text, bound values, result rows, request lines and client addresses. The check
      is made at the handler, so it still holds if someone raises these loggers' levels.
    * Every other third-party record is written only at WARNING and above, and its message
      is replaced by a fixed notice (see `build_entry`).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        if _matches(name, ALWAYS_BLOCKED_LOGGERS):
            return False
        if is_application_logger(name) or name in MESSAGE_PASSTHROUGH_LOGGERS:
            return True
        return record.levelno >= logging.WARNING


_HANDLER_MARK = "_relationship_archive_handler"
_THIRD_PARTY_PINNED = ("sqlalchemy", "httpx", "httpcore", "asyncio")
# One level above CRITICAL: nothing is ever emitted by these loggers.
_SILENCED = logging.CRITICAL + 10
_SILENCED_LOGGERS = ("sqlalchemy.engine", "sqlalchemy.engine.Engine")
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(settings: Settings, stream: IO[str] | None = None) -> logging.Handler:
    """Install the privacy-safe handler. Safe to call more than once."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    handler.addFilter(PrivacyFilter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)

    # LOG_LEVEL governs application loggers only. Everything else stays quiet, so raising
    # the level to DEBUG never turns on library output.
    root.setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(settings.log_level)
    for name in _THIRD_PARTY_PINNED:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in _SILENCED_LOGGERS:
        logging.getLogger(name).setLevel(_SILENCED)

    # uvicorn installs its own handlers (including a stdout access log with request lines
    # and client addresses). Remove them: our handler on the root logger replaces them.
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True
    return handler
