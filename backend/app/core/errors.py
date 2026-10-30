"""One error format, and exception handlers that never reveal internals.

Every error response has the same shape:

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

Client-visible messages come only from the fixed table below. There is deliberately no way
to pass an arbitrary string, an exception message or a `detail` through to the client.
"""

import logging
import re
import uuid
from collections.abc import Mapping
from enum import StrEnum
from http import HTTPStatus
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var, summarize_exception

logger = logging.getLogger("app.errors")


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    VALIDATION_ERROR = "validation_error"
    INTERNAL_ERROR = "internal_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    HTTP_ERROR = "http_error"


# The approved, user-safe messages. Adding an entry here is a reviewed change.
APPROVED: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.INVALID_REQUEST: (400, "The request could not be understood."),
    ErrorCode.UNAUTHORIZED: (401, "Authentication is required."),
    ErrorCode.FORBIDDEN: (403, "You do not have access to this resource."),
    ErrorCode.NOT_FOUND: (404, "The requested resource was not found."),
    ErrorCode.METHOD_NOT_ALLOWED: (405, "This method is not allowed for this resource."),
    ErrorCode.CONFLICT: (409, "The request conflicts with existing data."),
    ErrorCode.VALIDATION_ERROR: (422, "Some fields are missing or invalid."),
    ErrorCode.INTERNAL_ERROR: (500, "An unexpected error occurred."),
    ErrorCode.SERVICE_UNAVAILABLE: (
        503,
        "The service is temporarily unavailable. Try again later.",
    ),
}

_CODE_FOR_STATUS = {
    400: ErrorCode.INVALID_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
}


class AppError(Exception):
    """Raise to return one of the approved errors to the client.

    It takes an `ErrorCode`, never a message: the client text comes from `APPROVED`. The
    exception is created without arguments on purpose, so nothing the caller knows (names,
    ids, note text) can travel with it into a response or a log.
    """

    def __init__(self, code: ErrorCode) -> None:
        super().__init__()
        # Raises for anything that is not an ErrorCode, so a free-text message cannot be used.
        self.code = ErrorCode(code)
        if self.code not in APPROVED:
            raise ValueError(f"{self.code.value} has no approved client message")


class ErrorField(BaseModel):
    loc: list[str | int]
    type: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    fields: list[ErrorField] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


def current_request_id() -> str:
    return request_id_var.get() or uuid.uuid4().hex


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    fields: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    detail: dict[str, Any] = {"code": code, "message": message, "request_id": current_request_id()}
    if fields is not None:
        detail["fields"] = fields
    return JSONResponse({"error": detail}, status_code=status_code, headers=headers)


def approved_response(code: ErrorCode, headers: dict[str, str] | None = None) -> JSONResponse:
    status_code, message = APPROVED[code]
    return error_response(status_code, code.value, message, headers=headers)


def new_error_id() -> str:
    return uuid.uuid4().hex[:12]


def log_error(event: str, exc: BaseException, level: int = logging.ERROR) -> str:
    """Log an exception as type and frames only, and return the id that links it."""
    error_id = new_error_id()
    extra = {"event": event, "error_id": error_id, **summarize_exception(exc)}
    logger.log(level, event, extra=extra)
    return error_id


# --- handlers --------------------------------------------------------------------------

_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_UNKNOWN_FIELD = "<unknown field>"


def _safe_loc(loc: tuple[Any, ...], error_type: str) -> list[str | int]:
    """Keep the location of an error, but never a name that the client chose.

    Locations normally name declared fields. For an unexpected field the last element is
    the client's own key, so it is replaced. Keys of free-form dictionaries cannot be told
    apart from declared names, which is a documented limitation.
    """
    safe: list[str | int] = []
    for index, part in enumerate(loc):
        is_last = index == len(loc) - 1
        if isinstance(part, int) and not isinstance(part, bool):
            safe.append(part)
        elif error_type.startswith("extra") and is_last:
            safe.append(_UNKNOWN_FIELD)
        elif isinstance(part, str) and _FIELD_NAME.match(part):
            safe.append(part)
        else:
            safe.append(_UNKNOWN_FIELD)
    return safe


async def handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields: list[dict[str, Any]] = []
    for error in exc.errors():
        error_type = str(error.get("type", "invalid"))[:64]
        loc = _safe_loc(tuple(error.get("loc", ())), error_type)
        fields.append({"loc": loc, "type": error_type})
    status_code, message = APPROVED[ErrorCode.VALIDATION_ERROR]
    return error_response(status_code, ErrorCode.VALIDATION_ERROR.value, message, fields=fields)


_PASSTHROUGH_HEADERS = frozenset({"www-authenticate", "allow", "retry-after"})


def _safe_headers(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    if not isinstance(headers, Mapping):
        return None
    kept = {k: v for k, v in headers.items() if k.lower() in _PASSTHROUGH_HEADERS}
    return kept or None


async def handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    # Only the status is used. `exc.detail` is never sent to the client or logged. Of the
    # headers a route may attach, only those that are part of HTTP semantics are kept.
    headers = _safe_headers(exc.headers)
    code = _CODE_FOR_STATUS.get(exc.status_code)
    if code is not None:
        return approved_response(code, headers)
    if exc.status_code >= 500:
        message = APPROVED[ErrorCode.INTERNAL_ERROR][1]
        code = ErrorCode.INTERNAL_ERROR.value
        return error_response(exc.status_code, code, message, headers=headers)
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "Error"
    return error_response(exc.status_code, ErrorCode.HTTP_ERROR.value, phrase, headers=headers)


async def handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return approved_response(exc.code)


# SQLSTATE codes (PostgreSQL appendix A) that describe a conflict with existing data:
# unique_violation and foreign_key_violation.
_CONFLICT_SQLSTATES = frozenset({"23505", "23503"})
# Server shutdown, crash recovery, too many connections, and the whole connection-exception
# class 08: the database is unavailable, not the request wrong.
_UNAVAILABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03", "53300"})


def database_error_code(exc: BaseException, sqlstate: str | None) -> ErrorCode:
    """Choose the client-facing error for a database exception, using only safe facts."""
    if sqlstate in _CONFLICT_SQLSTATES:
        return ErrorCode.CONFLICT
    if (
        isinstance(exc, OperationalError | InterfaceError | psycopg.OperationalError)
        or (sqlstate is not None and sqlstate.startswith("08"))
        or sqlstate in _UNAVAILABLE_SQLSTATES
    ):
        return ErrorCode.SERVICE_UNAVAILABLE
    return ErrorCode.INTERNAL_ERROR


async def handle_database_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SQLAlchemyError | psycopg.Error)
    # The database error text can contain submitted values (for example the value that
    # violated a unique constraint) and the statement. Only the exception type and the
    # SQLSTATE code are logged, and only an approved message is returned.
    sqlstate = summarize_exception(exc).get("sqlstate")
    code = database_error_code(exc, sqlstate)
    level = logging.WARNING if code is ErrorCode.CONFLICT else logging.ERROR
    log_error("database_error", exc, level)
    return approved_response(code)


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(RequestValidationError, handle_validation_error)
    application.add_exception_handler(StarletteHTTPException, handle_http_exception)
    application.add_exception_handler(AppError, handle_app_error)
    application.add_exception_handler(SQLAlchemyError, handle_database_error)
    application.add_exception_handler(psycopg.Error, handle_database_error)
