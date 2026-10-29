"""Model training, the artifact registry, and prediction."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from realestate.model import registry
from realestate.model import train as train_mod
from realestate.model.candidates import CANDIDATES
from realestate.model.predict import PricePredictor
from realestate.model.train import (
    TrainingBlocked,
    build_pipeline,
    check_not_worse,
    live_model_type,
    train_model,
)


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

    assert set(result.metrics) == {"cv", "cv_city_only", "holdout", "holdout_city_only"}
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


def test_guard_blocks_city_only_regression() -> None:
    registry.save(
        build_pipeline(),
        metrics={
            "holdout": {"median_ape_pct": 15.0},
            "holdout_city_only": {"median_ape_pct": 17.0},
        },
        metadata={},
    )
    new = {"holdout": {"median_ape_pct": 15.0}, "holdout_city_only": {"median_ape_pct": 20.5}}
    with pytest.raises(TrainingBlocked, match="city-only"):
        check_not_worse(new, max_regression_pts=3.0)


# -- automatic model selection -------------------------------------------
def _fake_scores(monkeypatch: pytest.MonkeyPatch, city_only: dict[str, float]) -> None:
    """Make each candidate's CV score a fixed number instead of fitting."""

    def fake(x, y, model_type, cv):  # noqa: ARG001
        m = {"median_ape_pct": city_only[model_type], "mape_pct": 0.0, "r2_eur": 0.0}
        return {"cv": dict(m), "cv_city_only": dict(m)}

    monkeypatch.setattr(train_mod, "_cv_metrics", fake)


def _select(incumbent: str | None, margin: float = 1.0) -> train_mod.Selection:
    return train_mod.select_model(
        pd.DataFrame(),
        np.array([]),
        candidates=["xgboost", "hist_gradient_boosting", "linear_regression"],
        incumbent=incumbent,
        margin_pts=margin,
        cv=None,
    )


def test_selection_picks_best_when_nothing_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_scores(
        monkeypatch, {"xgboost": 18, "hist_gradient_boosting": 16, "linear_regression": 30}
    )
    sel = _select(None)
    assert sel.chosen == "hist_gradient_boosting"
    assert not sel.switched


def test_selection_keeps_incumbent_within_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.6 pts better is inside the noise: don't flip the deployed type for it
    _fake_scores(
        monkeypatch, {"xgboost": 17.0, "hist_gradient_boosting": 16.4, "linear_regression": 30}
    )
    sel = _select("xgboost")
    assert sel.chosen == "xgboost"
    assert not sel.switched
    assert "keeping XGBoost" in sel.reason


def test_selection_switches_on_a_clear_win(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_scores(
        monkeypatch, {"xgboost": 18.0, "hist_gradient_boosting": 16.5, "linear_regression": 30}
    )
    sel = _select("xgboost")
    assert sel.chosen == "hist_gradient_boosting"
    assert sel.switched
    assert sel.reason.startswith("switched XGBoost -> HistGradientBoosting")


def test_selection_ignores_an_incumbent_that_is_no_longer_a_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_scores(
        monkeypatch, {"xgboost": 18, "hist_gradient_boosting": 16, "linear_regression": 30}
    )
    assert _select("random_forest").chosen == "hist_gradient_boosting"


def test_train_model_chooses_between_all_candidates(synthetic_listings: pd.DataFrame) -> None:
    result = train_model(synthetic_listings, seed=0, candidates=list(CANDIDATES))
    meta = result.metadata
    assert set(meta["selection"]["candidates"]) == set(CANDIDATES)
    assert meta["model_type"] == meta["selection"]["chosen"]
    assert meta["model_label"] == CANDIDATES[meta["model_type"]].label
    # the price is a non-linear city x area interaction: a tree model must win
    assert meta["model_type"] != "linear_regression"
    assert set(result.metrics) == {"cv", "cv_city_only", "holdout", "holdout_city_only"}
    assert sum(meta["city_rows"].values()) == meta["n_rows_total"]


def test_train_model_rejects_unknown_candidate(synthetic_listings: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="unknown model type"):
        train_model(synthetic_listings, candidates=["xgboost", "catboost"])


def test_live_model_type_defaults_for_older_artifacts() -> None:
    assert live_model_type() is None
    registry.save(build_pipeline(), metrics={}, metadata={})  # no "model_type" key
    assert live_model_type() == "xgboost"


@pytest.mark.parametrize("model_type", ["hist_gradient_boosting", "random_forest"])
def test_non_default_model_types_serve_predictions(
    synthetic_listings: pd.DataFrame, model_type: str
) -> None:
    result = train_model(synthetic_listings, seed=0, candidates=[model_type])
    registry.save(result.pipeline, metrics=result.metrics, metadata=result.metadata)
    predictor = PricePredictor.load()
    assert predictor.metadata["model_type"] == model_type
    # a city-only request, and one naming a district the model never saw
    for record in (
        {"city": "berlin", "living_area_sqm": 80.0},
        {"city": "berlin", "living_area_sqm": 80.0, "district": "Nowhere"},
    ):
        out = predictor.predict_one(record)
        assert 100_000 < out.price_eur < 2_000_000


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
