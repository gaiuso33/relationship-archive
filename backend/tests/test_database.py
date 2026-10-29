from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import session as session_module


def test_session_runs_a_query_on_postgresql_17(db_session: Session) -> None:
    assert db_session.execute(text("select 1")).scalar_one() == 1
    assert db_session.execute(text("show server_version_num")).scalar_one().startswith("17")


def test_database_uses_utf8(db_session: Session) -> None:
    assert db_session.execute(text("show server_encoding")).scalar_one() == "UTF8"


def test_committed_work_is_visible_during_the_test(db_session: Session) -> None:
    db_session.execute(text("create table isolation_probe (id int)"))
    db_session.execute(text("insert into isolation_probe values (1)"))
    db_session.commit()

    assert db_session.execute(text("select count(*) from isolation_probe")).scalar_one() == 1


def test_committed_work_does_not_leak_into_the_next_test(db_session: Session) -> None:
    exists = db_session.execute(text("select to_regclass('public.isolation_probe')"))
    assert exists.scalar_one() is None


def test_get_session_closes_the_session_and_returns_the_connection(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "get_engine", lambda: db_engine)
    sessions: Iterator[Session] = session_module.get_session()

    session = next(sessions)
    session.execute(text("select 1"))
    assert db_engine.pool.checkedout() == 1

    with pytest.raises(StopIteration):
        next(sessions)
    assert db_engine.pool.checkedout() == 0


def test_get_session_rolls_back_work_that_was_not_committed(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "get_engine", lambda: db_engine)
    sessions = session_module.get_session()
    session = next(sessions)
    session.execute(text("create table rollback_probe (id int)"))
    with pytest.raises(StopIteration):
        next(sessions)

    with db_engine.connect() as connection:
        exists = connection.execute(text("select to_regclass('public.rollback_probe')"))
        assert exists.scalar_one() is None


def test_get_engine_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(session_module, "get_settings", lambda: Settings(_env_file=None))
    session_module.get_engine.cache_clear()

    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        session_module.get_engine()
