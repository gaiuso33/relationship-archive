"""Request context: server-generated request ids, a privacy-safe access log, and the last
line of defence for unhandled errors.

Pure ASGI middleware, so it sees every request and no request or response body is read.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode, approved_response, log_error
from app.core.logging import request_id_var

access_logger = logging.getLogger("app.access")
UNMATCHED_ROUTE = "unmatched"
# Static path segments only (for example `/api/v1`): nothing that looks like data.
_STATIC_PREFIX = re.compile(r"(?:/[A-Za-z][A-Za-z0-9_.\-]{0,30}){0,4}")


def route_template(scope: Scope) -> str:
    """Return the matched route as a template such as `/api/v1/people/{person_id}`.

    The template is built from the route definition, never from what the client sent. In
    current FastAPI the matched route does not carry the prefix it was included under
    (`/api/v1`), so that prefix is recovered from the raw path, but only when the rest of
    the raw path is exactly this route with its parameters filled in. A request that
    matched no route (404) has no template at all.
    """
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    if not isinstance(template, str) or not template:
        return UNMATCHED_ROUTE

    expanded = template
    for name, value in (scope.get("path_params") or {}).items():
        expanded = expanded.replace("{" + name + "}", str(value))
    raw_path = str(scope.get("path", ""))
    if expanded and raw_path.endswith(expanded):
        prefix = raw_path[: len(raw_path) - len(expanded)]
        if _STATIC_PREFIX.fullmatch(prefix):
            return prefix + template
    return template


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Always generated here. A client-supplied X-Request-ID is ignored, so a client
        # cannot choose what appears in our logs.
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception as exc:
                if response_started:
                    # Too late to change the response. uvicorn closes the connection, and
                    # its own log record is reduced to type and frames by our formatter.
                    raise
                log_error("unhandled_error", exc)
                status_code = 500
                await approved_response(ErrorCode.INTERNAL_ERROR)(
                    scope, receive, send_with_request_id
                )
        finally:
            access_logger.info(
                "request completed",
                extra={
                    "event": "request_completed",
                    "method": scope.get("method", ""),
                    # The route template ("/people/{person_id}"), never the raw path.
                    "route": route_template(scope),
                    "status": status_code,
                    "duration_ms": (time.perf_counter() - started) * 1000,
                },
            )
            request_id_var.reset(token)
