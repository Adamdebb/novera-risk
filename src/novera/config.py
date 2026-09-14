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
    fund_db_path: Path = Path("./data/fund.duckdb")

    # AI layer. The engine never depends on these.
    llm_model: str = "claude-opus-5"
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    # Alerts. Stored always; delivered only through channels that are configured.
    alerts_enabled: bool = True
    slack_webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    alert_email_from: str | None = None
    alert_email_to: str | None = Field(default=None, description="Comma-separated recipients")

    # Counterparty risk.
    own_credit_spread_bp: float = Field(default=80.0, description="Firm's own CDS spread for DVA")
    lgd: float = Field(default=0.6, description="Loss given default for CVA and DVA")
    exposure_paths: int = 1000
    exposure_enabled: bool = True
    regulatory_enabled: bool = True

    # Scheduler.
    eod_time: str = Field(
        default="18:30", description="Local wall-clock time HH:MM for the scheduled EOD run"
    )

    # Real market data adapters (optional).
    fred_api_key: str | None = Field(default=None, validation_alias="FRED_API_KEY")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
