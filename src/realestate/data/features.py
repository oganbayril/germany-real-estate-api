"""Turn cleaned listing rows into a model-ready feature frame.

This module fits nothing and touches no database: it is a pure transform so the
training job and the prediction API run the *exact same* feature code. Encoding
of the categorical columns and model fitting happen in ``realestate.model``.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

TARGET = "price_log"

NUMERIC_FEATURES = [
    "living_area_sqm",
    "rooms",
    "floor",
    "area_per_room",
    "energy_class_ordinal",
]
FLAG_FEATURES = [
    "floor_missing",
    "rooms_missing",
    "energy_class_missing",
]
CATEGORICAL_FEATURES = [
    "city",
    "district",
    "quarter",
    "postal_prefix",
]
FEATURE_COLUMNS = NUMERIC_FEATURES + FLAG_FEATURES + CATEGORICAL_FEATURES

# Energy certificate classes, worst -> best. Missing -> NaN + a flag.
_ENERGY_ORDER = ["H", "G", "F", "E", "D", "C", "B", "A", "A+"]
_ENERGY_ORDINAL = {cls: i for i, cls in enumerate(_ENERGY_ORDER)}


def build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return a new frame with exactly ``FEATURE_COLUMNS`` (+ ``TARGET`` if price is present)."""
    out = pd.DataFrame(index=df.index)

    area = _numeric(df, "living_area_sqm")
    rooms = _numeric(df, "rooms")
    floor = _numeric(df, "floor")

    out["living_area_sqm"] = area
    out["rooms"] = rooms
    out["floor"] = floor
    out["area_per_room"] = area / rooms.where(rooms > 0)

    energy = df.get("energy_efficiency_class")
    energy = energy if energy is not None else pd.Series(index=df.index, dtype="object")
    out["energy_class_ordinal"] = energy.map(_ENERGY_ORDINAL).astype("float64")

    out["floor_missing"] = floor.isna().astype("int8")
    out["rooms_missing"] = rooms.isna().astype("int8")
    out["energy_class_missing"] = out["energy_class_ordinal"].isna().astype("int8")

    out["city"] = _as_category(df.get("city"), df.index)
    out["district"] = _as_category(df.get("district"), df.index)
    out["quarter"] = _as_category(_quarter(df.get("address"), df.index), df.index)
    out["postal_prefix"] = _as_category(_postal_prefix(df.get("postal_code"), df.index), df.index)

    if "price_eur" in df.columns:
        price = pd.to_numeric(df["price_eur"], errors="coerce")
        out[TARGET] = np.log(price.where(price > 0))

    return out


def target_to_price(y_log: np.ndarray | pd.Series) -> np.ndarray:
    """Inverse of the log target: model output -> euros."""
    return np.exp(np.asarray(y_log, dtype="float64"))


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def _as_category(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        series = pd.Series(index=index, dtype="object")
    return series.astype("object").where(series.notna(), None).astype("category")


def _postal_prefix(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(index=index, dtype="object")
    return series.astype("string").str.replace(r"\D", "", regex=True).str[:3].replace("", pd.NA)


def _quarter(series: pd.Series | None, index: pd.Index) -> pd.Series:
    """Ortsteil from an address like "Street 1, Moabit, Mitte (10553)" -> "Moabit".

    The address is ``[street,] quarter, district (plz)``. The quarter is the
    second-to-last comma segment once the "(plz)" tail is removed; if there is
    only one segment (just the district), there is no quarter.
    """
    if series is None:
        return pd.Series(index=index, dtype="object")

    def pick(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        head = re.sub(r"\s*\(\d{5}\)\s*$", "", value).strip()
        parts = [p.strip() for p in head.split(",") if p.strip()]
        return parts[-2] if len(parts) >= 2 else None

    return pd.Series([pick(v) for v in series], index=index, dtype="object")
