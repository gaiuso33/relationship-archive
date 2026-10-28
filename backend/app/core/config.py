"""Application configuration.

Settings come from environment variables. For local development they may also be
read from an optional `.env` file at the repository root (never committed; see
`.env.example` for the placeholders). Configuration stays here, separate from
application logic.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repository root is three levels above `core/`.
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Relationship Archive API"
    app_env: Literal["development", "test", "production"] = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
