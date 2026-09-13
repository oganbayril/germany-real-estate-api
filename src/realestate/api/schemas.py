"""Request/response models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from realestate.data.clean import DEFAULT_BOUNDS

_AREA_MIN, _AREA_MAX = DEFAULT_BOUNDS.living_area_sqm
_ROOMS_MIN, _ROOMS_MAX = DEFAULT_BOUNDS.rooms
_FLOOR_MIN, _FLOOR_MAX = DEFAULT_BOUNDS.floor


class PredictRequest(BaseModel):
    """Bounds mirror ``data.clean.DEFAULT_BOUNDS`` -- the same range the training
    data itself is cleaned to, so a request can't ask the model to extrapolate
    (or just get messed with) far outside anything it was ever trained on."""

    city: str = Field(max_length=100, examples=["berlin"])
    living_area_sqm: float = Field(ge=_AREA_MIN, le=_AREA_MAX, examples=[72.0])
    rooms: float | None = Field(default=None, ge=_ROOMS_MIN, le=_ROOMS_MAX, examples=[3.0])
    floor: int | None = Field(default=None, ge=int(_FLOOR_MIN), le=int(_FLOOR_MAX), examples=[2])
    postal_code: str | None = Field(default=None, max_length=10, examples=["10437"])
    district: str | None = Field(default=None, max_length=100, examples=["Pankow"])
    quarter: str | None = Field(default=None, max_length=100, examples=["Prenzlauer Berg"])
    energy_efficiency_class: str | None = Field(default=None, max_length=4, examples=["C"])


class CityLocations(BaseModel):
    districts: list[str]
    quarters: list[str]


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
