"""Shared FastAPI dependencies. `require_user` is the one way a route learns who is calling."""

import logging
from typing import Annotated

from fastapi import Depends, Request

from app.core.auth import (
    MAX_TOKEN_LENGTH,
    AuthenticatedUser,
    AuthenticationFailed,
    KeyUnavailable,
    RejectionReason,
    TokenVerifier,
)
from app.core.errors import AppError, ErrorCode, log_error

logger = logging.getLogger("app.auth")


def _bearer_token(request: Request) -> str:
    """Read the token from the one `Authorization: Bearer <token>` header.

    Nothing else in the request (body, query string, other headers, cookies) is consulted.
    """
    values = request.headers.getlist("authorization")
    if not values:
        raise AuthenticationFailed(RejectionReason.MISSING_CREDENTIALS)
    if len(values) != 1:
        raise AuthenticationFailed(RejectionReason.MALFORMED_AUTHORIZATION)
    scheme, separator, token = values[0].partition(" ")
    if (
        scheme.lower() != "bearer"
        or not separator
        or not token
        or token != token.strip()
        or " " in token
        or len(token) > MAX_TOKEN_LENGTH
    ):
        raise AuthenticationFailed(RejectionReason.MALFORMED_AUTHORIZATION)
    return token


def require_user(request: Request) -> AuthenticatedUser:
    """Authenticate the request from its Bearer token and return the verified identity.

    A synchronous dependency on purpose: fetching signing keys blocks, so FastAPI runs it in
    its thread pool. Failures become fixed, privacy-safe errors: 401 for a bad or missing
    token, 503 when verification itself is unavailable (never a pass).
    """
    verifier: TokenVerifier | None = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        logger.error("auth_not_configured", extra={"event": "auth_not_configured"})
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    try:
        return verifier.verify(_bearer_token(request))
    except AuthenticationFailed as exc:
        # The event name comes from a fixed enum, never from request data.
        event = f"auth_rejected_{exc.reason.value}"
        logger.warning(event, extra={"event": event})
        raise AppError(ErrorCode.UNAUTHORIZED) from None
    except KeyUnavailable as exc:
        # The category is a fixed word (dns, connect, tls, status, deadline, size, body).
        log_error(f"auth_keys_unavailable_{exc.category.value}", exc, logging.ERROR)
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE) from None


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
