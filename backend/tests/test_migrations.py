import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db_support import BACKEND_DIR, alembic_config, ensure_head, reset_schema


def test_there_is_a_single_head_and_it_is_the_people_migration() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0002"]
    assert script.get_revision("0002").down_revision == "0001"


def test_upgrade_from_an_empty_database_and_round_trip(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        # Start from truly empty: no application tables or Alembic bookkeeping left over.
        reset_schema(connection)
        config = alembic_config(connection)

        # The baseline creates no application tables.
        command.upgrade(config, "0001")
        version = connection.execute(text("select version_num from alembic_version"))
        assert version.scalar_one() == "0001"
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}

        command.upgrade(config, "head")
        version = connection.execute(text("select version_num from alembic_version"))
        assert version.scalar_one() == "0002"
        assert set(inspect(connection).get_table_names()) == {"alembic_version", "people"}

        command.downgrade(config, "base")
        assert connection.execute(text("select count(*) from alembic_version")).scalar_one() == 0
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}

        command.upgrade(config, "head")


def test_models_and_migrations_agree(db_engine: Engine) -> None:
    """`alembic check` raises if the models would need a migration that does not exist."""
    with db_engine.begin() as connection:
        reset_schema(connection)
        config = alembic_config(connection)
        command.upgrade(config, "head")

        command.check(config)


def test_people_table_shape(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        inspector = inspect(connection)
        columns = {c["name"]: c for c in inspector.get_columns("people")}
        assert set(columns) == {
            "id",
            "owner_id",
            "display_name",
            "birthday",
            "timezone",
            "interests",
            "preferences",
            "created_at",
            "updated_at",
        }
        required = {"id", "owner_id", "display_name", "interests", "preferences"}
        required |= {"created_at", "updated_at"}
        assert {name for name, c in columns.items() if not c["nullable"]} == required
        # Ownership lookups are indexed.
        indexed = [index["column_names"] for index in inspector.get_indexes("people")]
        assert ["owner_id"] in indexed
        assert inspector.get_pk_constraint("people")["constrained_columns"] == ["id"]
        # The owner is a Supabase user, not a row in this database: no foreign key.
        assert inspector.get_foreign_keys("people") == []


def test_database_refuses_a_blank_display_name(migrated_engine: Engine) -> None:
    """The schema backs up the API validation: a blank name cannot be stored by any path."""
    connection = migrated_engine.connect()
    outer = connection.begin()
    try:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "insert into people (id, owner_id, display_name) "
                    "values (gen_random_uuid(), gen_random_uuid(), '   ')"
                )
            )
    finally:
        outer.rollback()
        connection.close()
        ensure_head(migrated_engine)
