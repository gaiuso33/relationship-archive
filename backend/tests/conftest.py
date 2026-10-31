import io
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from tests.db_support import build_test_engine


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    url = get_settings().test_database_url
    engine = build_test_engine(url.get_secret_value() if url else None)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """A session whose work is always rolled back after the test, even if it commits.

    The session joins an outer transaction through a SAVEPOINT, so `session.commit()`
    inside the test only releases the savepoint and nothing is persisted.
    """
    connection = db_engine.connect()
    outer = connection.begin()
    session = Session(connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


_LOGGER_NAMES = (
    "",
    "app",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "sqlalchemy",
    "sqlalchemy.engine",
    "httpx",
    "httpcore",
    "asyncio",
)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Give every test the logging state it found, whatever the test configured."""
    saved: dict[str, tuple[Any, ...]] = {}
    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        saved[name] = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


@pytest.fixture(params=["json", "text"])
def log_format(request: pytest.FixtureRequest) -> str:
    """Run a test once per output format: both must give identical privacy protection."""
    return str(request.param)


@pytest.fixture
def log_stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def test_settings(log_format: str) -> Settings:
    return Settings(_env_file=None, log_level="DEBUG", log_format=log_format)


@pytest.fixture
def configured_logging(test_settings: Settings, log_stream: io.StringIO) -> io.StringIO:
    configure_logging(test_settings, log_stream)
    return log_stream
