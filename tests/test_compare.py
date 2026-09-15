"""Candidate-model comparison (not part of the production training path)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from realestate.model.compare import CANDIDATES, compare_models


@pytest.fixture
def synthetic_listings() -> pd.DataFrame:
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


def test_compare_models_scores_every_candidate(synthetic_listings: pd.DataFrame) -> None:
    results = compare_models(synthetic_listings, seed=0)

    assert set(results) == set(CANDIDATES)
    for metrics in results.values():
        assert metrics["n"] > 0
        assert (
            metrics["mape_pct"] < 40
        )  # strong synthetic signal -> every candidate is in the ballpark
        assert np.isfinite(metrics["r2_eur"])


def test_xgboost_beats_the_linear_baseline(synthetic_listings: pd.DataFrame) -> None:
    # price is a genuinely non-linear function of the features here (multiplicative
    # city/area interaction), so a tree model should read it better than a plain
    # linear fit on ordinal-encoded categoricals.
    results = compare_models(synthetic_listings, seed=0)
    assert results["xgboost"]["mae_eur"] < results["linear_regression"]["mae_eur"]
