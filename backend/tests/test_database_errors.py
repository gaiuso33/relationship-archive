"""Database errors must be translated into safe responses and must never log values.

PostgreSQL's own error text contains the offending value (for example
`Key (email)=(x) already exists`), so these tests run against a real PostgreSQL.
"""

import io
import logging
from typing import Annotated

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, get_session
from app.main import API_V1_PREFIX, create_app
from tests import support
from tests.test_error_handling import assert_nothing_sensitive


class PersonIn(BaseModel):
    email: str


@pytest.fixture
def db_client(test_settings: Settings, log_stream: io.StringIO, db_session: Session) -> TestClient:
    application = create_app(test_settings)
    application.dependency_overrides[get_session] = lambda: db_session

    @application.post(f"{API_V1_PREFIX}/probe/people")
    def create_person(person: PersonIn, session: Annotated[Session, Depends(get_session)]) -> dict:
        session.execute(
            text("insert into privacy_probe_people (email) values (:email)"),
            {"email": person.email},
        )
        return {"created": True}

    @application.get(f"{API_V1_PREFIX}/probe/broken-sql")
    def broken_sql(session: Annotated[Session, Depends(get_session)]) -> None:
        session.execute(text("select * from table_that_does_not_exist_synthetic"))

    @application.get(f"{API_V1_PREFIX}/probe/db-down")
    def db_down() -> None:
        raise OperationalError(
            f"select {support.DB_VALUE}",
            {"email": support.DB_VALUE},
            psycopg.OperationalError(f"connection to {support.DB_VALUE} refused"),
        )

    @application.get(f"{API_V1_PREFIX}/probe/raw-driver-error")
    def raw_driver_error() -> None:
        # A driver error that escaped SQLAlchemy: PostgreSQL's text names the value.
        raise psycopg.errors.UniqueViolation(f"Key (email)=({support.DB_VALUE}) already exists")

    db_session.execute(text("create table privacy_probe_people (email text primary key)"))
    db_session.commit()
    configure_logging(test_settings, log_stream)
    return TestClient(application, raise_server_exceptions=False)


def test_control_the_database_really_puts_the_value_in_its_error_text(
    db_session: Session,
) -> None:
    """Proves the leak vector exists, so the tests below can fail if it were not handled."""
    db_session.execute(text("create table privacy_control (email text primary key)"))
    db_session.execute(text("insert into privacy_control values (:e)"), {"e": support.DB_VALUE})

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.execute(
                text("insert into privacy_control values (:e)"), {"e": support.DB_VALUE}
            )

    assert support.DB_VALUE in str(caught.value.orig)


def test_unique_violation_returns_a_safe_conflict_and_leaks_nothing(
    db_client: TestClient, log_stream: io.StringIO
) -> None:
    url = f"{API_V1_PREFIX}/probe/people"
    body = {"email": support.DB_VALUE}
    first = db_client.post(url, json=body, headers=support.AUTH_HEADERS)
    second = db_client.post(url, json=body, headers=support.AUTH_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {
        "error": {
            "code": "conflict",
            "message": "The request conflicts with existing data.",
            "request_id": second.headers["x-request-id"],
        }
    }
    assert_nothing_sensitive(log_stream, first, second)
    output = log_stream.getvalue()
    for internal in ("privacy_probe_people", "DETAIL", "duplicate key", "pkey", "insert into"):
        assert internal not in output
        assert internal not in second.text
    # Operations still get what they need: what kind of error, which SQLSTATE, which request.
    assert "database_error" in output
    assert "sqlalchemy.exc.IntegrityError" in output
    assert "23505" in output
    assert second.headers["x-request-id"] in output


def test_other_database_errors_become_a_generic_500(
    db_client: TestClient, log_stream: io.StringIO
) -> None:
    response = db_client.get(f"{API_V1_PREFIX}/probe/broken-sql")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "table_that_does_not_exist_synthetic" not in response.text
    assert "table_that_does_not_exist_synthetic" not in log_stream.getvalue()
    assert "42P01" in log_stream.getvalue()


def test_sql_statements_and_values_stay_out_of_logs_even_at_debug(
    db_engine: Engine, test_settings: Settings, log_stream: io.StringIO
) -> None:
    configure_logging(test_settings, log_stream)
    assert test_settings.log_level == "DEBUG"
    # Simulate someone turning the library loggers up, which must not matter.
    for name in ("sqlalchemy", "sqlalchemy.engine", "sqlalchemy.engine.Engine"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    engine = create_db_engine(db_engine.url.render_as_string(hide_password=False))

    with engine.connect() as connection:
        connection.execute(text("select :v as probe_column"), {"v": support.DB_VALUE})
    engine.dispose()

    output = log_stream.getvalue()
    assert support.DB_VALUE not in output
    assert "probe_column" not in output
    assert "select" not in output.lower()


def test_control_the_same_logging_would_leak_without_our_handler(
    db_engine: Engine,
) -> None:
    """Proves the test above is sensitive: plain SQLAlchemy DEBUG logging does show SQL."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("sqlalchemy.engine.Engine")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        engine = create_db_engine(db_engine.url.render_as_string(hide_password=False))
        with engine.connect() as connection:
            connection.execute(text("select 'probe_column_control'"))
        engine.dispose()
    finally:
        logger.removeHandler(handler)

    assert "probe_column_control" in stream.getvalue()


def test_unavailable_database_returns_503_and_leaks_nothing(
    db_client: TestClient, log_stream: io.StringIO
) -> None:
    response = db_client.get(f"{API_V1_PREFIX}/probe/db-down", headers=support.AUTH_HEADERS)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert_nothing_sensitive(log_stream, response)
    assert "database_error" in log_stream.getvalue()
    assert "OperationalError" in log_stream.getvalue()


def test_driver_errors_outside_sqlalchemy_are_translated_too(
    db_client: TestClient, log_stream: io.StringIO
) -> None:
    response = db_client.get(f"{API_V1_PREFIX}/probe/raw-driver-error")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert_nothing_sensitive(log_stream, response)
    assert "23505" in log_stream.getvalue()


def test_echo_enabled_engine_still_writes_nothing_to_our_handler(
    db_engine: Engine, test_settings: Settings, log_stream: io.StringIO
) -> None:
    """Someone passing echo=True turns the engine logger on directly: the handler blocks it."""
    configure_logging(test_settings, log_stream)
    url = db_engine.url.render_as_string(hide_password=False)
    engine = create_engine(url, echo="debug", hide_parameters=True)

    with engine.connect() as connection:
        connection.execute(text("select 'echo_probe_literal' as echo_probe_column"))
    engine.dispose()

    output = log_stream.getvalue()
    assert "echo_probe" not in output
    assert "select" not in output.lower()
