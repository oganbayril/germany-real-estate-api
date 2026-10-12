"""FastAPI app. Run: ``uvicorn realestate.api.main:app``.

The trained model is loaded once at startup and held on ``app.state``. If no
model has been trained yet the app still starts (and ``/health`` reports it), but
``/predict`` returns 503.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from realestate import __version__
from realestate.api.deps import get_predictor
from realestate.api.schemas import (
    CityLocations,
    HealthResponse,
    LastScrape,
    ModelInfoResponse,
    PredictRequest,
    PredictResponse,
    PriceSummary,
    StatsResponse,
)
from realestate.data.features import quarter_from_address
from realestate.db.models import Listing, ScrapeRun
from realestate.db.session import session_scope
from realestate.model.predict import PricePredictor

log = logging.getLogger(__name__)

PredictorDep = Annotated[PricePredictor, Depends(get_predictor)]

limiter = Limiter(key_func=get_remote_address, default_limits=["240/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        app.state.predictor = PricePredictor.load()
        log.info("loaded model version %s", app.state.predictor.version)
    except FileNotFoundError:
        app.state.predictor = None
        log.warning("no trained model found; /predict will return 503")
    yield


app = FastAPI(title="Germany Real-Estate Price API", version=__version__, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_STATIC = Path(__file__).parent / "static"
# LinkedIn and other crawlers cache og:image by URL and don't re-fetch it even
# when the page itself is re-scraped, so the URL carries a hash of the file:
# a re-rendered image gets a new URL and previews pick it up.
_OG_IMAGE_VERSION = hashlib.sha256((_STATIC / "og-image.png").read_bytes()).hexdigest()[:10]
_INDEX_HTML = (
    (_STATIC / "index.html")
    .read_text(encoding="utf-8")
    .replace("/og-image.png", f"/og-image.png?v={_OG_IMAGE_VERSION}")
)
_MODEL_CARD_HTML = (_STATIC / "model_card.html").read_text(encoding="utf-8")


# HEAD too, like /health: link checkers and monitors pointed at the homepage
# send HEAD, and a GET-only route answers 405.
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
def root() -> str:
    return _INDEX_HTML


# The plain-language counterpart to GET /model (which stays JSON for API
# clients). The page fills in its numbers from /model in the browser.
@app.api_route(
    "/model-card", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False
)
def model_card() -> str:
    return _MODEL_CARD_HTML


# Explicit media types rather than guessing from the extension: Caddy sends
# X-Content-Type-Options: nosniff, so a wrong type means the browser drops it.
_STATIC_ASSET_CACHE = {"Cache-Control": "public, max-age=86400"}


@app.api_route(
    "/favicon.svg", methods=["GET", "HEAD"], response_class=FileResponse, include_in_schema=False
)
def favicon() -> FileResponse:
    return FileResponse(
        _STATIC / "favicon.svg", media_type="image/svg+xml", headers=_STATIC_ASSET_CACHE
    )


# Link-preview image for the og:image tag on the demo page (LinkedIn etc.).
@app.api_route(
    "/og-image.png", methods=["GET", "HEAD"], response_class=FileResponse, include_in_schema=False
)
def og_image() -> FileResponse:
    return FileResponse(
        _STATIC / "og-image.png", media_type="image/png", headers=_STATIC_ASSET_CACHE
    )


# HEAD too: uptime monitors (e.g. UptimeRobot) check with HEAD by default, and a
# GET-only route answers 405. The server drops the body for HEAD responses.
@app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
def health() -> HealthResponse:
    predictor: PricePredictor | None = getattr(app.state, "predictor", None)
    return HealthResponse(
        status="ok",
        model_loaded=predictor is not None,
        model_version=predictor.version if predictor else None,
    )


@app.post("/predict", response_model=PredictResponse)
@limiter.limit("30/minute")
def predict(request: Request, body: PredictRequest, predictor: PredictorDep) -> PredictResponse:
    result = predictor.predict_one(body.model_dump(exclude_none=True))
    holdout = predictor.metrics.get("holdout", {})
    return PredictResponse(
        predicted_price_eur=result.price_eur,
        predicted_price_per_sqm_eur=result.price_per_sqm_eur,
        model_version=result.model_version,
        typical_error_pct=holdout.get("median_ape_pct"),
    )


@app.get("/locations", response_model=dict[str, CityLocations])
@limiter.limit("60/minute")
def locations(request: Request) -> dict[str, CityLocations]:
    """Real district/quarter values seen per city -- feeds the demo page's
    dropdowns so a request can only ever name a place the model actually knows
    about, instead of free text."""
    with session_scope() as session:
        rows = session.execute(
            select(Listing.city, Listing.district, Listing.address).where(
                Listing.listing_status == "active"
            )
        ).all()

    districts: dict[str, set[str]] = defaultdict(set)
    quarters: dict[str, set[str]] = defaultdict(set)
    for city, district, address in rows:
        if district:
            districts[city].add(district)
        quarter = quarter_from_address(address)
        if quarter:
            quarters[city].add(quarter)

    return {
        city: CityLocations(districts=sorted(districts[city]), quarters=sorted(quarters[city]))
        for city in sorted(set(districts) | set(quarters))
    }


@app.get("/model", response_model=ModelInfoResponse)
def model_info(predictor: PredictorDep) -> ModelInfoResponse:
    meta = predictor.metadata
    return ModelInfoResponse(
        version=predictor.version,
        trained_at=meta.get("trained_at"),
        n_rows_total=meta.get("n_rows_total"),
        feature_columns=meta.get("feature_columns", []),
        cities=meta.get("cities", []),
        metrics=predictor.metrics,
    )


@app.get("/stats", response_model=StatsResponse)
@limiter.limit("60/minute")
def stats(request: Request) -> StatsResponse:
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(Listing)) or 0
        active = (
            session.scalar(
                select(func.count()).select_from(Listing).where(Listing.listing_status == "active")
            )
            or 0
        )
        by_city = dict(
            session.execute(
                select(Listing.city, func.count())
                .group_by(Listing.city)
                .order_by(func.count().desc())
            ).all()
        )
        price_eur = _price_summary(session)
        last = session.scalars(
            select(ScrapeRun)
            .where(ScrapeRun.finished_at.is_not(None))
            .order_by(ScrapeRun.id.desc())
        ).first()
        last_scrape = (
            LastScrape(
                finished_at=last.finished_at.isoformat() if last.finished_at else None,
                status=last.status,
                listings_new=last.listings_new,
                exposes_seen=last.exposes_seen,
            )
            if last
            else None
        )

    return StatsResponse(
        listings_total=total,
        active_listings=active,
        by_city={str(k): int(v) for k, v in by_city.items()},
        price_eur=price_eur,
        last_scrape=last_scrape,
    )


def _price_summary(session: Session) -> PriceSummary | None:
    """Percentiles in SQL on Postgres; fall back to pulling the column on SQLite."""
    col = Listing.price_eur
    present = col.is_not(None)

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        row = session.execute(
            select(
                func.min(col),
                func.percentile_cont(0.25).within_group(col),
                func.percentile_cont(0.5).within_group(col),
                func.percentile_cont(0.75).within_group(col),
                func.max(col),
            ).where(present)
        ).one()
        if row[0] is None:
            return None
        return PriceSummary(min=row[0], p25=row[1], median=row[2], p75=row[3], max=row[4])

    prices = [p for (p,) in session.execute(select(col).where(present)).all()]
    if not prices:
        return None
    arr = np.asarray(prices, dtype="float64")
    p25, p50, p75 = (float(v) for v in np.percentile(arr, [25, 50, 75]))
    return PriceSummary(min=float(arr.min()), p25=p25, median=p50, p75=p75, max=float(arr.max()))
