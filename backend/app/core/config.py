"""Application configuration.

Settings come from environment variables. For local development they may also be
read from an optional `.env` file at the repository root (never committed; see
`.env.example` for the placeholders). Configuration stays here, separate from
application logic.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# backend/app/core/config.py -> repository root is three levels above `core/`.
REPO_ROOT = Path(__file__).resolve().parents[3]

DATABASE_DRIVER = "postgresql+psycopg"


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

    # Both URLs contain a password, so they are secrets: never log or print them.
    database_url: SecretStr | None = None
    # Used only by the automated tests. It must name a dedicated database whose name ends in
    # `_test`, because the tests create and drop schema objects in it.
    test_database_url: SecretStr | None = None

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
