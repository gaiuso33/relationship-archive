from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(connection: Connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    return config


def test_there_is_a_single_head_and_it_is_the_baseline() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == ["0001"]


def test_upgrade_from_an_empty_database_and_round_trip(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        # Start from truly empty: no Alembic bookkeeping left over from an earlier run.
        connection.execute(text("drop table if exists alembic_version"))
        config = alembic_config(connection)

        command.upgrade(config, "head")
        version = connection.execute(text("select version_num from alembic_version"))
        assert version.scalar_one() == "0001"
        # The baseline creates no application tables.
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}

        command.downgrade(config, "base")
        assert connection.execute(text("select count(*) from alembic_version")).scalar_one() == 0

        command.upgrade(config, "head")


def test_models_and_migrations_agree(db_engine: Engine) -> None:
    """`alembic check` raises if the models would need a migration that does not exist."""
    with db_engine.begin() as connection:
        connection.execute(text("drop table if exists alembic_version"))
        config = alembic_config(connection)
        command.upgrade(config, "head")

        command.check(config)
