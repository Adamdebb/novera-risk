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
    llm_provider: str = "auto"
    """auto | anthropic | gemini | groq | openrouter | ollama | openai_compat | scripted. ``auto``
    picks the first provider with a key (Anthropic, Gemini, Groq, OpenRouter), else a configured
    base URL, else the scripted stand-in."""
    llm_model: str | None = None
    """Model id; None means the provider's default (claude-opus-5, gemini-3.6-flash, ...)."""
    llm_base_url: str | None = None
    """OpenAI-compatible endpoint (the .../v1 base) for ``openai_compat``; presets fill their own."""
    llm_api_key: str | None = None
    """Key for ``openai_compat`` or to override a preset's own key variable."""
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")

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
    proxies_enabled: bool = True
    """Proxy missing and stale market data before pricing (MD-002); the raw snapshot is kept."""

    # API. Browser origins allowed to call it (a React dev server, for example); empty = none.
    cors_origins: str = Field(default="", description="Comma-separated origins, e.g. http://localhost:5173")

    # Scheduler.
    eod_time: str = Field(
        default="18:30", description="Local wall-clock time HH:MM for the scheduled EOD run"
    )

    # Real market data adapters (optional).
    fred_api_key: str | None = Field(default=None, validation_alias="FRED_API_KEY")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
