"""Helpers for building the test database engine safely."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text
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


BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(connection: Connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    return config


def head_revision() -> str:
    heads = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_heads()
    assert len(heads) == 1
    return heads[0]


def reset_schema(connection: Connection) -> None:
    """Remove every application table and Alembic's bookkeeping from the test database.

    Only ever called on the dedicated `*_test` database (see `build_test_engine`).
    """
    connection.execute(text("drop table if exists notes, people, alembic_version cascade"))


def ensure_head(engine: Engine) -> None:
    """Leave the test database migrated to head, committed, doing nothing if it already is."""
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        if "alembic_version" in tables:
            current = connection.execute(text("select version_num from alembic_version"))
            if current.scalar_one_or_none() == head_revision():
                return
        reset_schema(connection)
        command.upgrade(alembic_config(connection), "head")
