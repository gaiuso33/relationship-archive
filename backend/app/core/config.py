"""Application configuration.

Settings come from environment variables. For local development they may also be
read from an optional `.env` file at the repository root (never committed; see
`.env.example` for the placeholders). Configuration stays here, separate from
application logic.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# backend/app/core/config.py -> repository root is three levels above `core/`.
REPO_ROOT = Path(__file__).resolve().parents[3]

DATABASE_DRIVER = "postgresql+psycopg"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # A rejected value must never be echoed back: database URLs contain passwords.
        hide_input_in_errors=True,
    )

    app_name: str = "Relationship Archive API"
    app_env: Literal["development", "test", "production"] = "development"

    # Logging. LOG_LEVEL applies to application loggers only (see app/core/logging.py).
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    # Both URLs contain a password, so they are secrets: never log or print them.
    database_url: SecretStr | None = None
    # Used only by the automated tests. It must name a dedicated database whose name ends in
    # `_test`, because the tests create and drop schema objects in it.
    test_database_url: SecretStr | None = None

    # Supabase Auth (ADR-004). The project URL is public information, not a secret. The backend
    # needs no Supabase key of any kind: access tokens are verified with the project's public
    # signing keys. Never add a service-role key or the legacy JWT secret here.
    supabase_url: str | None = None
    supabase_jwt_audience: str = "authenticated"

    @field_validator("log_level", "log_format", mode="before")
    @classmethod
    def normalise_case(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().upper() if info.field_name == "log_level" else value.strip().lower()

    @field_validator("database_url", "test_database_url")
    @classmethod
    def require_psycopg_postgresql_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        try:
            driver = make_url(value.get_secret_value()).drivername
        except ArgumentError:
            # `from None`: the original error text can contain the URL.
            raise ValueError("the value is not a valid database URL") from None
        if driver != DATABASE_DRIVER:
            raise ValueError(f"the database URL must start with {DATABASE_DRIVER}://")
        return value

    @field_validator("supabase_url", mode="before")
    @classmethod
    def normalise_supabase_url(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("the Supabase URL must be text")
        value = value.strip()
        if not value:
            return None  # An empty variable in `.env` means "not configured".
        try:
            parts = urlsplit(value)
            host, port = parts.hostname, parts.port
        except ValueError:
            raise ValueError("the Supabase URL is not valid") from None
        if parts.scheme not in {"https", "http"} or not host:
            raise ValueError("the Supabase URL must be an https URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("the Supabase URL must not contain credentials, query or fragment")
        if parts.path not in {"", "/"}:
            raise ValueError("the Supabase URL must be the project origin, without a path")
        shown_host = f"[{host}]" if ":" in host else host
        return f"{parts.scheme}://{shown_host}" + (f":{port}" if port else "")

    @field_validator("supabase_jwt_audience")
    @classmethod
    def require_audience(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("the audience must not be empty")
        return value

    @model_validator(mode="after")
    def check_supabase_url_for_environment(self) -> "Settings":
        if self.app_env == "production" and self.supabase_url is None:
            raise ValueError("SUPABASE_URL is required in production")
        if self.supabase_url is not None and self.supabase_url.startswith("http://"):
            host = urlsplit(self.supabase_url).hostname
            if self.app_env == "production" or host not in LOOPBACK_HOSTS:
                raise ValueError("the Supabase URL must use https (http is allowed only locally)")
        return self

    @property
    def supabase_issuer(self) -> str | None:
        """The `iss` claim that Supabase Auth puts in access tokens for this project."""
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @property
    def supabase_jwks_url(self) -> str | None:
        """Where the project publishes its public signing keys. Derived from configuration
        only, never from a token."""
        return f"{self.supabase_issuer}/.well-known/jwks.json" if self.supabase_url else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
