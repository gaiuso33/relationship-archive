"""The test database setup fails loudly instead of skipping or hitting the wrong database."""

import pytest

from tests.db_support import build_test_engine

PASSWORD = "synthetic-password-for-tests"


def test_missing_test_database_url_fails_instead_of_skipping() -> None:
    with pytest.raises(pytest.fail.Exception, match="TEST_DATABASE_URL is not set"):
        build_test_engine(None)


def test_refuses_a_database_not_named_for_tests() -> None:
    url = f"postgresql+psycopg://user:{PASSWORD}@127.0.0.1:5432/relationship_archive"

    with pytest.raises(pytest.fail.Exception, match="ends in '_test'") as error:
        build_test_engine(url)

    assert PASSWORD not in str(error.value)


def test_unreachable_database_fails_without_leaking_credentials() -> None:
    url = f"postgresql+psycopg://user:{PASSWORD}@127.0.0.1:1/relationship_archive_test"

    with pytest.raises(pytest.fail.Exception, match="Cannot connect") as error:
        build_test_engine(url)

    assert PASSWORD not in str(error.value)
