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


PASSWORD = "synthetic-password-for-tests"


def test_database_urls_default_to_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    settings = make_settings()

    assert settings.database_url is None
    assert settings.test_database_url is None


def test_reads_database_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    url = f"postgresql+psycopg://user:{PASSWORD}@127.0.0.1:5432/example"
    monkeypatch.setenv("DATABASE_URL", url)

    settings = make_settings()

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == url


def test_database_url_is_not_revealed_by_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg://user:{PASSWORD}@127.0.0.1/example")

    settings = make_settings()

    assert PASSWORD not in repr(settings)
    assert PASSWORD not in str(settings)


@pytest.mark.parametrize(
    "bad_url",
    [
        f"postgresql://user:{PASSWORD}@127.0.0.1/example",
        f"mysql+pymysql://user:{PASSWORD}@127.0.0.1/example",
        f"not a url {PASSWORD}",
    ],
)
def test_rejects_unsupported_database_url_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, bad_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", bad_url)

    with pytest.raises(ValidationError) as error:
        make_settings()

    assert PASSWORD not in str(error.value)


def test_logging_defaults_to_info_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    settings = make_settings()

    assert settings.log_level == "INFO"
    assert settings.log_format == "json"


def test_logging_settings_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_FORMAT", "TEXT")

    settings = make_settings()

    assert settings.log_level == "DEBUG"
    assert settings.log_format == "text"


@pytest.mark.parametrize(("name", "value"), [("LOG_LEVEL", "verbose"), ("LOG_FORMAT", "xml")])
def test_rejects_unknown_logging_settings(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        make_settings()
