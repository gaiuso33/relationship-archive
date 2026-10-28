from fastapi import FastAPI

from app.api import health
from app.core.config import Settings, get_settings

API_V1_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(title=settings.app_name)
    application.include_router(health.router, prefix=API_V1_PREFIX)
    return application


app = create_app()
