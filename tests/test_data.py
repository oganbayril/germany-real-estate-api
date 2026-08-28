"""Cleaning and feature-engineering transforms (synthetic frames, no DB)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from realestate.data.clean import DEFAULT_BOUNDS, clean
from realestate.data.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    TARGET,
    build_feature_frame,
    target_to_price,
)


def _raw(**over: object) -> dict[str, object]:
    base = {
        "expose_id": "e1",
        "city": "berlin",
        "district": "Mitte",
        "postal_code": "10115",
        "price_eur": 400_000.0,
        "living_area_sqm": 80.0,
        "rooms": 3.0,
        "floor": 2.0,
        "energy_efficiency_class": "C",
        "property_type": "apartment",
        "address": "Some Street 1, Mitte",
        "listing_status": "active",
    }
    base.update(over)
    return base


# -- clean --------------------------------------------------------------
def test_clean_drops_rows_without_price_or_area() -> None:
    df = pd.DataFrame(
        [
            _raw(),
            _raw(expose_id="e2", price_eur=None),
            _raw(expose_id="e3", living_area_sqm=None),
        ]
    )
    assert list(clean(df)["expose_id"]) == ["e1"]


def test_clean_removes_price_and_ppsqm_outliers() -> None:
    df = pd.DataFrame(
        [
            _raw(expose_id="ok"),
            _raw(expose_id="too_cheap", price_eur=5_000.0),
            _raw(expose_id="ppsqm_absurd", price_eur=4_000_000.0, living_area_sqm=40.0),
        ]
    )
    assert set(clean(df)["expose_id"]) == {"ok"}


def test_clean_keeps_rows_with_missing_optional_fields() -> None:
    df = pd.DataFrame([_raw(floor=None, rooms=None, energy_efficiency_class=None)])
    cleaned = clean(df)
    assert len(cleaned) == 1
    assert pd.isna(cleaned.loc[0, "floor"])


def test_clean_dedupes_same_flat() -> None:
    df = pd.DataFrame([_raw(expose_id="a"), _raw(expose_id="b")])  # same address/price/area
    assert len(clean(df)) == 1


def test_clean_adds_price_per_sqm() -> None:
    cleaned = clean(pd.DataFrame([_raw()]))
    assert cleaned.loc[0, "price_per_sqm"] == pytest.approx(5000.0)


def test_default_bounds_are_sane() -> None:
    assert DEFAULT_BOUNDS.price_eur[0] < DEFAULT_BOUNDS.price_eur[1]


# -- features ---------------------------------------------------------
def test_feature_frame_has_exact_contract() -> None:
    out = build_feature_frame(pd.DataFrame([_raw()]))
    assert list(out.columns) == [*FEATURE_COLUMNS, TARGET]
    for col in CATEGORICAL_FEATURES:
        assert isinstance(out[col].dtype, pd.CategoricalDtype)


def test_feature_engineering_values() -> None:
    out = build_feature_frame(pd.DataFrame([_raw(living_area_sqm=90.0, rooms=3.0, floor=4.0)]))
    row = out.iloc[0]
    assert row["area_per_room"] == pytest.approx(30.0)
    assert row["energy_class_ordinal"] == 5  # C in H..A+ order
    assert row["floor_missing"] == 0
    assert row["price_log"] == pytest.approx(np.log(_raw()["price_eur"]))


def test_missing_floor_and_energy_set_flags() -> None:
    out = build_feature_frame(pd.DataFrame([_raw(floor=None, energy_efficiency_class=None)]))
    row = out.iloc[0]
    assert row["floor_missing"] == 1
    assert row["energy_class_missing"] == 1
    assert pd.isna(row["energy_class_ordinal"])


def test_postal_prefix_derived() -> None:
    out = build_feature_frame(pd.DataFrame([_raw(postal_code="10437")]))
    assert out.iloc[0]["postal_prefix"] == "104"


def test_quarter_extracted_from_address() -> None:
    df = pd.DataFrame(
        [
            _raw(address="Berlichingenstraße 15, Moabit, Mitte (10553)"),
            _raw(address="Winterhude, Nord (22299)"),
            _raw(address="Mitte (10559)"),
        ]
    )
    out = build_feature_frame(df)
    assert out["quarter"].tolist()[:2] == ["Moabit", "Winterhude"]
    assert pd.isna(out["quarter"].iloc[2])


def test_single_row_inference_path_has_no_target() -> None:
    # API sends a record with no price
    payload = {k: v for k, v in _raw().items() if k != "price_eur"}
    out = build_feature_frame(pd.DataFrame([payload]))
    assert TARGET not in out.columns
    assert list(out.columns) == FEATURE_COLUMNS


def test_target_round_trip() -> None:
    prices = np.array([150_000.0, 640_000.0])
    assert target_to_price(np.log(prices)) == pytest.approx(prices)
