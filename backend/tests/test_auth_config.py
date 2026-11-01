"""Supabase settings: validation, derived URLs, and the production startup rule."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

URL = "https://synthetic-project.example.invalid"


def make(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_defaults_leave_supabase_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)

    settings = make()

    assert settings.supabase_url is None
    assert settings.supabase_issuer is None
    assert settings.supabase_jwks_url is None
    assert settings.supabase_jwt_audience == "authenticated"


def test_issuer_and_jwks_url_are_derived_from_the_project_url() -> None:
    settings = make(supabase_url=URL)

    assert settings.supabase_issuer == f"{URL}/auth/v1"
    assert settings.supabase_jwks_url == f"{URL}/auth/v1/.well-known/jwks.json"


def test_reads_the_url_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", URL + "/")

    assert make().supabase_url == URL


def test_an_empty_value_means_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "  ")

    assert make().supabase_url is None


@pytest.mark.parametrize(
    "value",
    [
        "synthetic-project.example.invalid",
        "ftp://synthetic-project.example.invalid",
        "https://:443",
        f"{URL}/auth/v1",
        f"{URL}?x=1",
        f"{URL}#frag",
        "https://user:pass@synthetic-project.example.invalid",
        "https://synthetic-project.example.invalid:notaport",
        "http://synthetic-project.example.invalid",
    ],
)
def test_invalid_urls_are_rejected_without_echoing_the_value(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        make(supabase_url=value)

    assert value not in str(caught.value)


def test_plain_http_is_allowed_only_for_loopback_outside_production() -> None:
    assert make(app_env="test", supabase_url="http://127.0.0.1:8123").supabase_url == (
        "http://127.0.0.1:8123"
    )
    with pytest.raises(ValidationError):
        make(app_env="production", supabase_url="http://127.0.0.1:8123")


def test_production_requires_the_url() -> None:
    with pytest.raises(ValidationError):
        make(app_env="production")
    with pytest.raises(ValidationError):
        make(app_env="production", supabase_url=" ")

    assert make(app_env="production", supabase_url=URL).supabase_url == URL


@pytest.mark.parametrize("env", ["development", "test"])
def test_development_and_test_start_without_the_url(env: str) -> None:
    assert make(app_env=env).supabase_url is None


def test_audience_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        make(supabase_jwt_audience=" ")
