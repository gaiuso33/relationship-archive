"""Database engine and request-scoped sessions.

There is one engine per process, created lazily from `DATABASE_URL`, and one
`Session` per request, provided by `get_session`.

Sessions never commit on their own. Code that changes data calls
`session.commit()` explicitly; anything left uncommitted is rolled back when the
session closes. This keeps the point at which data is saved visible in the code,
and behaves the same on every FastAPI version.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings

NOT_CONFIGURED_MESSAGE = (
    "DATABASE_URL is not set. Copy .env.example to .env (see docs/development.md)."
)


def create_db_engine(url: str) -> Engine:
    return create_engine(
        url,
        # Replace connections that the server or network dropped while idle.
        pool_pre_ping=True,
        # Never copy bound parameters (names, note text) into error messages or logs.
        hide_parameters=True,
    )


@lru_cache
def get_engine() -> Engine:
    database_url = get_settings().database_url
    if database_url is None:
        raise RuntimeError(NOT_CONFIGURED_MESSAGE)
    return create_db_engine(database_url.get_secret_value())


def dispose_engine() -> None:
    """Close pooled connections. Called when the application shuts down."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_engine.cache_clear()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
