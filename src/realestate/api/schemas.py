"""Request/response models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    city: str = Field(examples=["berlin"])
    living_area_sqm: float = Field(gt=5, le=1000, examples=[72.0])
    rooms: float | None = Field(default=None, gt=0, le=20, examples=[3.0])
    floor: int | None = Field(default=None, ge=0, le=50, examples=[2])
    postal_code: str | None = Field(default=None, examples=["10437"])
    district: str | None = Field(default=None, examples=["Pankow"])
    quarter: str | None = Field(default=None, examples=["Prenzlauer Berg"])
    energy_efficiency_class: str | None = Field(default=None, examples=["C"])


class PredictResponse(BaseModel):
    predicted_price_eur: float
    predicted_price_per_sqm_eur: float | None
    model_version: str
    typical_error_pct: float | None = Field(
        description="Model's hold-out median absolute percentage error, as a rough ± guide."
    )


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str | None


class ModelInfoResponse(BaseModel):
    version: str
    trained_at: str | None
    n_rows_total: int | None
    feature_columns: list[str]
    cities: list[str]
    metrics: dict


class PriceSummary(BaseModel):
    min: float
    p25: float
    median: float
    p75: float
    max: float


class LastScrape(BaseModel):
    finished_at: str | None
    status: str
    listings_new: int
    exposes_seen: int


class StatsResponse(BaseModel):
    listings_total: int
    active_listings: int
    by_city: dict[str, int]
    price_eur: PriceSummary | None
    last_scrape: LastScrape | None
