import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings() -> Settings:
    # Ignore any developer `.env` file so tests depend only on the process environment.
    return Settings(_env_file=None)


def test_defaults_when_environment_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)

    settings = make_settings()

    assert settings.app_env == "development"
    assert settings.app_name == "Relationship Archive API"


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    assert make_settings().app_env == "test"


def test_rejects_unknown_environment_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging-ish")

    with pytest.raises(ValidationError):
        make_settings()
