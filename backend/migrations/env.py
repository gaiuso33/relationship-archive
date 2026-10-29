"""Alembic environment.

The database URL comes from backend settings (`DATABASE_URL`), never from
`alembic.ini`. Tests can pass an already-open connection through
`config.attributes["connection"]`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from app import models  # noqa: F401  (registers models on Base.metadata)
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import NOT_CONFIGURED_MESSAGE, create_db_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
        return
    database_url = get_settings().database_url
    if database_url is None:
        raise RuntimeError(NOT_CONFIGURED_MESSAGE)
    engine = create_db_engine(database_url.get_secret_value())
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline (--sql) migrations are not supported; run against a database.")
run_migrations_online()
