"""Central configuration. All runtime knobs come from the environment or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RE_",
        extra="ignore",
    )

    # --- Database ---
    database_url: str = Field(
        default="sqlite+pysqlite:///./data/realestate.db",
        description="SQLAlchemy URL. Postgres in production, SQLite for local/test.",
    )

    # --- Scraper ---
    scrape_cities: Annotated[list[str], NoDecode] = Field(
        default=["berlin", "muenchen", "hamburg", "koeln", "leipzig"]
    )
    scrape_delay_min_s: float = 8.0
    scrape_delay_max_s: float = 15.0
    scrape_max_search_urls_per_city: int = 40
    # Reuse the sitemap-derived search-URL pool for this many days so most runs
    # spend requests only on real search pages, not on re-walking the sitemaps.
    scrape_discovery_cache_days: float = 7.0
    scrape_user_agent: str = (
        "germany-real-estate-api/0.1 (personal portfolio project; "
        "non-commercial; contact: oganby@gmail.com)"
    )
    scrape_request_timeout_s: float = 30.0

    # --- Model / training ---
    min_train_rows: int = 200

    # --- Filesystem ---
    data_dir: Path = PROJECT_ROOT / "data"
    model_dir: Path = PROJECT_ROOT / "models"

    # --- Deployment ---
    public_domain: str | None = None

    # --- Email (run-summary notifications; all-or-nothing) ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_to: str | None = None

    # --- Logging ---
    log_level: str = "INFO"

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.email_to)

    @field_validator("scrape_cities", mode="before")
    @classmethod
    def _split_cities(cls, v: object) -> object:
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    @field_validator("scrape_cities")
    @classmethod
    def _cities_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("scrape_cities must not be empty")
        return v

    @field_validator("data_dir", "model_dir")
    @classmethod
    def _ensure_dir(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
