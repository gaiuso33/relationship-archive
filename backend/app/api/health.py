from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


@router.get("/health", summary="Service health check")
def read_health() -> HealthResponse:
    """Report that the service is running.

    Intentionally minimal: it reveals no configuration, versions or data, and it
    does not touch the database (none exists yet).
    """
    return HealthResponse(status="ok")
