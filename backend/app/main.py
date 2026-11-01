from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, me
from app.core.auth import JwksKeyProvider, SigningKeyProvider, TokenVerifier
from app.core.config import Settings, get_settings
from app.core.errors import ErrorResponse, register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import dispose_engine

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    # uvicorn sets up its own logging before the app starts. Apply ours again afterwards.
    configure_logging(application.state.settings)
    yield
    dispose_engine()


def build_token_verifier(
    settings: Settings, key_provider: SigningKeyProvider | None = None
) -> TokenVerifier | None:
    """Create the verifier, or None when Supabase is not configured (protected routes then
    answer 503). Creating it makes no network request."""
    issuer, jwks_url = settings.supabase_issuer, settings.supabase_jwks_url
    if issuer is None or jwks_url is None:
        return None
    return TokenVerifier(
        key_provider or JwksKeyProvider(jwks_url, timeout=settings.supabase_jwks_timeout_seconds),
        issuer=issuer,
        audience=settings.supabase_jwt_audience,
    )


def create_app(
    settings: Settings | None = None, key_provider: SigningKeyProvider | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    application = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    application.state.settings = settings
    application.state.token_verifier = build_token_verifier(settings, key_provider)
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(health.router, prefix=API_V1_PREFIX)
    application.include_router(me.router, prefix=API_V1_PREFIX)
    return application


app = create_app()
