from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    application = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    application.state.settings = settings
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(health.router, prefix=API_V1_PREFIX)
    return application


app = create_app()
