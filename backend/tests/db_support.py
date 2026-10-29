"""Helpers for building the test database engine safely."""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import create_db_engine

HELP = "See 'Database' in docs/development.md to start PostgreSQL and set TEST_DATABASE_URL."


def build_test_engine(url: str | None) -> Engine:
    """Return an engine for the dedicated test database, or fail the test run clearly.

    Database tests must never skip silently, and must never run against a database
    that is not dedicated to tests: they create and drop schema objects.
    """
    if not url:
        pytest.fail(f"TEST_DATABASE_URL is not set. {HELP}", pytrace=False)
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.fail(
            "TEST_DATABASE_URL must point at a dedicated database whose name ends in '_test'. "
            "Refusing to run tests against any other database.",
            pytrace=False,
        )
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except SQLAlchemyError as error:
        engine.dispose()
        # Only the error class is reported: the full message can include connection details.
        pytest.fail(
            f"Cannot connect to the test database ({type(error).__name__}). {HELP}",
            pytrace=False,
        )
    return engine
