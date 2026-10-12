"""Model training, the artifact registry, and prediction."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from realestate.model import registry
from realestate.model.predict import PricePredictor
from realestate.model.train import TrainingBlocked, build_pipeline, check_not_worse, train_model


@pytest.fixture
def synthetic_listings() -> pd.DataFrame:
    """A few hundred listings whose price is a noisy function of the features."""
    rng = np.random.default_rng(0)
    n = 400
    cities = rng.choice(["berlin", "hamburg", "leipzig"], n)
    area = rng.uniform(30, 160, n)
    rooms = np.clip((area / 28).round(), 1, 6)
    floor = rng.integers(0, 8, n).astype(float)
    rates = {"berlin": 6500, "hamburg": 5500, "leipzig": 3200}
    city_factor = pd.Series(cities).map(rates).to_numpy()
    price = area * city_factor * rng.normal(1.0, 0.08, n)
    return pd.DataFrame(
        {
            "expose_id": [f"e{i}" for i in range(n)],
            "city": cities,
            "district": rng.choice(["A", "B", "C", "D"], n),
            "postal_code": rng.choice(["10115", "20095", "04103"], n),
            "address": "Somewhere, District (10115)",
            "price_eur": price,
            "living_area_sqm": area,
            "rooms": rooms,
            "floor": floor,
            "energy_efficiency_class": rng.choice(["A", "C", "E", None], n),
            "property_type": "apartment",
            "listing_status": "active",
        }
    )


CITY_RATES = {"muenchen": 8000, "berlin": 5000, "leipzig": 3000}  # EUR per m2


@pytest.fixture
def located_listings() -> pd.DataFrame:
    """Like real scraped data: every listing has a district and postal code, and
    both belong to exactly one city -- so location detail fully determines city."""
    rng = np.random.default_rng(1)
    rows = []
    for city, rate in CITY_RATES.items():
        for d in range(4):
            district, plz = f"{city}-d{d}", f"{city[:1].upper()}{d}000"
            district_factor = 0.85 + 0.1 * d
            for _ in range(60):
                area = rng.uniform(35, 140)
                rows.append(
                    {
                        "city": city,
                        "district": district,
                        "postal_code": plz,
                        "address": f"Street 1, {district} ({plz})",
                        "living_area_sqm": area,
                        "rooms": float(np.clip(round(area / 28), 1, 6)),
                        "price_eur": area * rate * district_factor * rng.normal(1.0, 0.06),
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _tmp_model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_models_root", lambda: tmp_path)


# -- training ---------------------------------------------------------
def test_train_model_reports_reasonable_metrics(synthetic_listings: pd.DataFrame) -> None:
    result = train_model(synthetic_listings, seed=0)

    assert set(result.metrics) == {"cv", "holdout", "holdout_city_only"}
    holdout = result.metrics["holdout"]
    # the synthetic signal is strong -> the model should be well within 25% MAPE
    assert holdout["mape_pct"] < 25
    assert holdout["r2_eur"] > 0.6
    assert result.metadata["feature_columns"]
    assert result.metadata["n_rows_total"] == len(synthetic_listings)


def test_train_model_rejects_tiny_frames() -> None:
    with pytest.raises(ValueError, match="need >="):
        train_model(pd.DataFrame({"price_eur": [1.0] * 10, "living_area_sqm": [1.0] * 10}))


def test_train_model_honours_min_rows(synthetic_listings: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="need >=100000"):
        train_model(synthetic_listings, min_rows=100_000)


def test_pipeline_handles_unseen_category(synthetic_listings: pd.DataFrame) -> None:
    pipe = build_pipeline().fit(_features(synthetic_listings), _target(synthetic_listings))
    novel = synthetic_listings.iloc[:3].copy()
    novel["district"] = "NEVER_SEEN"
    preds = pipe.predict(_features(novel))
    assert np.isfinite(preds).all()


# -- registry ------------------------------------------------------
def test_registry_round_trip(synthetic_listings: pd.DataFrame) -> None:
    result = train_model(synthetic_listings, seed=0)
    path = registry.save(result.pipeline, metrics=result.metrics, metadata=result.metadata)
    assert path.exists()

    loaded = registry.load()
    assert loaded.version in registry.list_versions()
    assert loaded.metrics == result.metrics
    assert loaded.metadata["target"] == "price_log"


def test_registry_latest_pointer(synthetic_listings: pd.DataFrame) -> None:
    result = train_model(synthetic_listings, seed=0)
    registry.save(result.pipeline, metrics={}, metadata={}, version="2020-01-01T00-00-00Z")
    registry.save(result.pipeline, metrics={}, metadata={}, version="2026-01-01T00-00-00Z")
    assert registry.latest_version() == "2026-01-01T00-00-00Z"


def test_load_without_any_model_raises() -> None:
    with pytest.raises(FileNotFoundError):
        registry.load()


# -- deploy guard -------------------------------------------------
def _live_model(median_ape: float, **metadata: str) -> None:
    registry.save(
        build_pipeline(),
        metrics={"holdout": {"median_ape_pct": median_ape}},
        metadata=metadata,
    )


def _new(median_ape: float) -> dict:
    return {"holdout": {"median_ape_pct": median_ape}}


def test_guard_allows_first_model() -> None:
    check_not_worse(_new(40.0), max_regression_pts=3.0)


def test_guard_allows_change_within_slack() -> None:
    _live_model(15.5)
    check_not_worse(_new(18.4), max_regression_pts=3.0)


def test_guard_blocks_clearly_worse_model() -> None:
    _live_model(15.5)
    with pytest.raises(TrainingBlocked, match="keeping the live model"):
        check_not_worse(_new(18.6), max_regression_pts=3.0)


def test_guard_ignores_sample_bootstrap_model() -> None:
    # The 180-row sample model isn't a fair yardstick for one on real data.
    _live_model(10.0, source="sample")
    check_not_worse(_new(20.0), max_regression_pts=3.0)


# -- prediction ---------------------------------------------------
def test_predict_one_after_training(synthetic_listings: pd.DataFrame) -> None:
    result = train_model(synthetic_listings, seed=0)
    registry.save(result.pipeline, metrics=result.metrics, metadata=result.metadata)

    predictor = PricePredictor.load()
    out = predictor.predict_one(
        {
            "city": "berlin",
            "district": "A",
            "postal_code": "10115",
            "address": "X, A (10115)",
            "living_area_sqm": 90.0,
            "rooms": 3.0,
            "floor": 2.0,
            "energy_efficiency_class": "C",
        }
    )
    assert 150_000 < out.price_eur < 1_500_000
    assert out.price_per_sqm_eur == pytest.approx(out.price_eur / 90.0, rel=1e-6)
    assert out.model_version == predictor.version


@pytest.mark.parametrize("city", list(CITY_RATES))
def test_city_only_request_prices_like_that_city(located_listings: pd.DataFrame, city: str) -> None:
    # The demo page sends no postal code and defaults district/quarter to "any".
    # Training rows always have them, so without location dropout the model never
    # learned what "city only" means and priced every city about the same.
    pipe = train_model(located_listings, seed=0).pipeline
    request = pd.DataFrame([{"city": city, "living_area_sqm": 70.0, "rooms": 3.0}])
    per_sqm = float(np.exp(pipe.predict(_features(request))[0])) / 70.0
    assert per_sqm == pytest.approx(CITY_RATES[city], rel=0.15)


def test_unseen_postal_code_falls_back_to_city_level(located_listings: pd.DataFrame) -> None:
    pipe = train_model(located_listings, seed=0).pipeline
    base = {"city": "muenchen", "living_area_sqm": 70.0, "rooms": 3.0}
    city_only = pipe.predict(_features(pd.DataFrame([base])))[0]
    unseen = pipe.predict(_features(pd.DataFrame([{**base, "postal_code": "99999"}])))[0]
    assert unseen == pytest.approx(city_only, abs=1e-6)


def test_metrics_grade_city_only_requests(located_listings: pd.DataFrame) -> None:
    metrics = train_model(located_listings, seed=0).metrics
    assert metrics["holdout_city_only"]["n"] == metrics["holdout"]["n"]
    assert metrics["holdout_city_only"]["median_ape_pct"] < 20


def _features(df: pd.DataFrame) -> pd.DataFrame:
    from realestate.data.features import FEATURE_COLUMNS, build_feature_frame

    return build_feature_frame(df)[FEATURE_COLUMNS]


def _target(df: pd.DataFrame) -> np.ndarray:
    from realestate.data.features import TARGET, build_feature_frame

    return build_feature_frame(df)[TARGET].to_numpy()
