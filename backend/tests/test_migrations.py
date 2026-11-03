import uuid
from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db_support import BACKEND_DIR, alembic_config, ensure_head, reset_schema


def test_there_is_a_single_linear_history_ending_at_the_notes_migration() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0003"]
    assert script.get_revision("0003").down_revision == "0002"
    assert script.get_revision("0002").down_revision == "0001"
    assert script.get_revision("0001").down_revision is None


def test_upgrade_from_an_empty_database_and_round_trip(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        # Start from truly empty: no application tables or Alembic bookkeeping left over.
        reset_schema(connection)
        config = alembic_config(connection)

        def version() -> str:
            return connection.execute(text("select version_num from alembic_version")).scalar_one()

        def tables() -> set[str]:
            return set(inspect(connection).get_table_names())

        # The baseline creates no application tables.
        command.upgrade(config, "0001")
        assert version() == "0001"
        assert tables() == {"alembic_version"}

        command.upgrade(config, "0002")
        assert version() == "0002"
        assert tables() == {"alembic_version", "people"}

        command.upgrade(config, "head")
        assert version() == "0003"
        assert tables() == {"alembic_version", "people", "notes"}

        command.downgrade(config, "0002")
        assert version() == "0002"
        assert tables() == {"alembic_version", "people"}

        command.downgrade(config, "base")
        assert connection.execute(text("select count(*) from alembic_version")).scalar_one() == 0
        assert tables() == {"alembic_version"}

        command.upgrade(config, "head")
        assert version() == "0003"
        assert tables() == {"alembic_version", "people", "notes"}


def test_downgrade_with_data_removes_notes_but_keeps_people(db_engine: Engine) -> None:
    """The downgrade drops notes before the unique constraint their key depends on."""
    with db_engine.begin() as connection:
        reset_schema(connection)
        config = alembic_config(connection)
        command.upgrade(config, "head")
        owner, person = uuid.uuid4(), uuid.uuid4()
        connection.execute(
            text("insert into people (id, owner_id, display_name) values (:p, :o, 'Synthetic')"),
            {"p": person, "o": owner},
        )
        connection.execute(
            text("insert into notes (id, owner_id, person_id, content) values (:n, :o, :p, 'x')"),
            {"n": uuid.uuid4(), "o": owner, "p": person},
        )

        command.downgrade(config, "0002")

        assert "notes" not in inspect(connection).get_table_names()
        assert connection.execute(text("select count(*) from people")).scalar_one() == 1
        constraints = inspect(connection).get_unique_constraints("people")
        assert "uq_people_id_owner_id" not in {c["name"] for c in constraints}

        command.upgrade(config, "head")
        assert connection.execute(text("select count(*) from notes")).scalar_one() == 0


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


# --- the notes table and its ownership guarantee ---------------------------------------------


@pytest.fixture
def rolled_back(migrated_engine: Engine):
    """A connection in a transaction that is always rolled back."""
    connection = migrated_engine.connect()
    outer = connection.begin()
    try:
        yield connection
    finally:
        outer.rollback()
        connection.close()


def add_person(connection: Connection, owner: uuid.UUID) -> uuid.UUID:
    person = uuid.uuid4()
    connection.execute(
        text("insert into people (id, owner_id, display_name) values (:p, :o, 'Synthetic')"),
        {"p": person, "o": owner},
    )
    return person


def add_note(
    connection: Connection, owner: uuid.UUID, person: uuid.UUID, content: str = "synthetic"
) -> None:
    connection.execute(
        text("insert into notes (id, owner_id, person_id, content) values (:n, :o, :p, :c)"),
        {"n": uuid.uuid4(), "o": owner, "p": person, "c": content},
    )


def sqlstate_of(connection: Connection, statement: Callable[[], None]) -> str:
    """Run a statement that must fail, inside a savepoint, and return its SQLSTATE."""
    with pytest.raises(IntegrityError) as caught:
        with connection.begin_nested():
            statement()
    return str(getattr(caught.value.orig, "sqlstate", ""))


def test_notes_table_shape_and_explicit_constraint_names(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        inspector = inspect(connection)
        columns = {c["name"]: c for c in inspector.get_columns("notes")}
        assert set(columns) == {
            "id",
            "owner_id",
            "person_id",
            "content",
            "created_at",
            "updated_at",
        }
        assert all(not c["nullable"] for c in columns.values())
        assert inspector.get_pk_constraint("notes")["constrained_columns"] == ["id"]
        assert {c["name"] for c in inspector.get_check_constraints("notes")} == {
            "ck_notes_content_not_blank",
            "ck_notes_content_max_length",
        }
        (foreign_key,) = inspector.get_foreign_keys("notes")
        assert foreign_key["name"] == "fk_notes_person_id_owner_id_people"
        assert foreign_key["constrained_columns"] == ["person_id", "owner_id"]
        assert foreign_key["referred_table"] == "people"
        assert foreign_key["referred_columns"] == ["id", "owner_id"]
        assert foreign_key["options"].get("ondelete") == "CASCADE"
        indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes("notes")}
        assert indexes["ix_notes_owner_id_person_id_created_at"] == [
            "owner_id",
            "person_id",
            "created_at",
        ]
        unique = {c["name"]: c["column_names"] for c in inspector.get_unique_constraints("people")}
        assert unique["uq_people_id_owner_id"] == ["id", "owner_id"]


def test_the_database_refuses_a_note_owned_by_someone_other_than_its_person(
    rolled_back: Connection,
) -> None:
    """The composite foreign key: ownership holds even if application code has a defect."""
    owner, intruder = uuid.uuid4(), uuid.uuid4()
    person = add_person(rolled_back, owner)

    state = sqlstate_of(rolled_back, lambda: add_note(rolled_back, intruder, person))

    assert state == "23503"
    add_note(rolled_back, owner, person)  # the real owner can
    assert rolled_back.execute(text("select count(*) from notes")).scalar_one() == 1


def test_the_database_refuses_a_note_for_a_person_that_does_not_exist(
    rolled_back: Connection,
) -> None:
    owner = uuid.uuid4()

    assert sqlstate_of(rolled_back, lambda: add_note(rolled_back, owner, uuid.uuid4())) == "23503"


def test_deleting_a_person_deletes_only_that_persons_notes(rolled_back: Connection) -> None:
    owner, other_owner = uuid.uuid4(), uuid.uuid4()
    doomed, kept, strangers = (
        add_person(rolled_back, owner),
        add_person(rolled_back, owner),
        add_person(rolled_back, other_owner),
    )
    for person, who in ((doomed, owner), (doomed, owner), (kept, owner), (strangers, other_owner)):
        add_note(rolled_back, who, person)

    rolled_back.execute(text("delete from people where id = :p"), {"p": doomed})

    remaining = rolled_back.execute(text("select person_id from notes")).scalars().all()
    assert sorted(map(str, remaining)) == sorted([str(kept), str(strangers)])


@pytest.mark.parametrize("content", ["", " ", "\t", "\n", " \t\r\n  "])
def test_the_database_refuses_blank_note_content(rolled_back: Connection, content: str) -> None:
    owner = uuid.uuid4()
    person = add_person(rolled_back, owner)

    assert (
        sqlstate_of(rolled_back, lambda: add_note(rolled_back, owner, person, content)) == "23514"
    )


def test_the_database_enforces_the_content_length_limit(rolled_back: Connection) -> None:
    owner = uuid.uuid4()
    person = add_person(rolled_back, owner)

    add_note(rolled_back, owner, person, "n" * 10_000)

    state = sqlstate_of(rolled_back, lambda: add_note(rolled_back, owner, person, "n" * 10_001))
    assert state == "23514"


def test_the_database_accepts_internal_whitespace_and_surrounding_whitespace(
    rolled_back: Connection,
) -> None:
    owner = uuid.uuid4()
    person = add_person(rolled_back, owner)

    add_note(rolled_back, owner, person, "  line one\n\tline two  ")

    stored = rolled_back.execute(text("select content from notes")).scalar_one()
    assert stored == "  line one\n\tline two  "
