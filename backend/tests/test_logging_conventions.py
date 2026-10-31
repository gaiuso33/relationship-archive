"""Static guard for the logging convention (see docs/logging-and-errors.md).

Application code must log a constant message and pass data only through the allowlisted
`extra` fields. A message built with an f-string, `%` or `.format()`, or log arguments, can
carry personal data into the logs, so this test fails the build when it finds one.
"""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}


def _logger_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in LOG_METHODS:
            continue
        target = node.func.value
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
        if name in {"logger", "access_logger"} or name.endswith("_logger"):
            yield node


def violations(source: str) -> list[str]:
    found: list[str] = []
    for call in _logger_calls(ast.parse(source)):
        method = call.func.attr  # type: ignore[union-attr]
        message_index = 1 if method == "log" else 0
        if len(call.args) > message_index + 1:
            found.append(f"line {call.lineno}: log arguments are not allowed")
            continue
        if len(call.args) <= message_index:
            found.append(f"line {call.lineno}: log call without a message")
            continue
        message = call.args[message_index]
        is_constant = isinstance(message, ast.Constant) and isinstance(message.value, str)
        is_event_name = isinstance(message, ast.Name) and message.id == "event"
        if not is_constant and not is_event_name:
            found.append(f"line {call.lineno}: the message must be a constant string")
    return found


def test_application_code_logs_constant_messages_only() -> None:
    problems = {
        str(path.relative_to(APP_DIR.parent)): found
        for path in sorted(APP_DIR.rglob("*.py"))
        if (found := violations(path.read_text(encoding="utf-8")))
    }

    assert problems == {}


@pytest.mark.parametrize(
    "source",
    [
        'logger.info(f"saved note {note_id}")',
        'logger.info("saved note %s", note_id)',
        'logger.error("failed: " + str(exc))',
        'logger.warning("saved {}".format(note))',
        "logger.exception(exc)",
        'logger.log(40, "x %s", value)',
        "logger.info()",
    ],
)
def test_the_checker_catches_unsafe_log_calls(source: str) -> None:
    assert violations(source)


@pytest.mark.parametrize(
    "source",
    [
        'logger.info("request completed", extra={"event": "request_completed"})',
        'logger.log(level, "database_error", extra={"event": "x"})',
        "logger.error(event, extra=extra)",
        "other.info(f'not a logger {x}')",
    ],
)
def test_the_checker_accepts_safe_log_calls(source: str) -> None:
    assert violations(source) == []
