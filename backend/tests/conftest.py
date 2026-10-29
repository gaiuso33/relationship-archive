from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
