"""Platform settings.

All user-facing branding reads ``settings.platform_name`` (ADR 0005). Environment
variables use the ``NOVERA_`` prefix; a ``.env`` file in the working directory is read
if present.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOVERA_", env_file=".env", extra="ignore")

    platform_name: str = "Novera"
    platform_tagline: str = "AI-native risk intelligence for global trading portfolios"
    reporting_currency: str = "USD"
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/novera.duckdb")

    # AI layer. The engine never depends on these.
    llm_model: str = "claude-fable-5-1"
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
