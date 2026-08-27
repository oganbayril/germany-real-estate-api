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
    scrape_delay_min_s: float = 5.0
    scrape_delay_max_s: float = 10.0
    scrape_max_pages_per_city: int = 5
    scrape_user_agent: str = (
        "germany-real-estate-api/0.1 (personal portfolio project; "
        "non-commercial; contact: oganby@gmail.com)"
    )
    scrape_request_timeout_s: float = 30.0

    # --- Filesystem ---
    data_dir: Path = PROJECT_ROOT / "data"
    model_dir: Path = PROJECT_ROOT / "models"

    # --- Logging ---
    log_level: str = "INFO"

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
