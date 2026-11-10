"""The collation report tool (docs/database-collation-checklist.md) against a real database."""

import pytest
from sqlalchemy import Engine

from app.tools import db_collation


def test_it_reports_the_settings_of_the_real_test_database(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        info = db_collation.collect(connection)

    assert info["encoding"] == "UTF8"
    assert info["provider"] in {"libc", "icu", "builtin"}
    assert info["server"] and info["server"][0].isdigit()
    assert info["datcollate"] and info["datctype"]
    # The migrated test database has the table, and nothing sets a column collation.
    assert info["people_display_name_collation"] == "(database default)"


def test_plain_and_markdown_output_name_every_setting(migrated_engine: Engine) -> None:
    plain = db_collation.report(migrated_engine, markdown=False)
    table = db_collation.report(migrated_engine, markdown=True)

    for _, label in db_collation.FIELDS:
        assert label in plain
        assert f"| {label} |" in table
    assert table.startswith("### Database collation")


def test_the_output_never_contains_the_connection_details(migrated_engine: Engine) -> None:
    output = db_collation.report(migrated_engine, markdown=False) + db_collation.report(
        migrated_engine, markdown=True
    )
    url = migrated_engine.url
    for secret in (url.password, url.host, url.username, url.database):
        if secret:
            assert secret not in output


def test_it_reports_a_missing_database_url_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        db_collation, "get_settings", lambda: type("S", (), {"database_url": None})()
    )

    assert db_collation.main([]) == 2
    captured = capsys.readouterr()
    assert "DATABASE_URL is not set" in captured.err
    assert captured.out == ""
