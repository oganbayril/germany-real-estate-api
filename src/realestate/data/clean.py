"""Load listings from the database and clean them into a training-ready frame.

Cleaning is deliberately conservative: drop rows we cannot model (no price or no
area), clip obvious data-entry errors, and de-duplicate the same flat relisted
under several expose ids. Everything here operates on a plain DataFrame so it can
be unit-tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.db.models import Listing

# Columns pulled from the DB into the raw frame.
LISTING_COLUMNS = (
    "expose_id",
    "city",
    "district",
    "postal_code",
    "price_eur",
    "living_area_sqm",
    "rooms",
    "floor",
    "energy_efficiency_class",
    "property_type",
    "address",
    "listing_status",
)


@dataclass(frozen=True)
class CleanBounds:
    """Inclusive bounds; rows outside any of these are dropped."""

    price_eur: tuple[float, float] = (20_000.0, 5_000_000.0)
    living_area_sqm: tuple[float, float] = (15.0, 400.0)
    price_per_sqm: tuple[float, float] = (500.0, 25_000.0)
    rooms: tuple[float, float] = (1.0, 12.0)
    floor: tuple[float, float] = (0.0, 40.0)


DEFAULT_BOUNDS = CleanBounds()


def load_listings(session: Session, *, active_only: bool = True) -> pd.DataFrame:
    """Read listings into a DataFrame (one row per expose)."""
    stmt = select(*(getattr(Listing, name) for name in LISTING_COLUMNS))
    if active_only:
        stmt = stmt.where(Listing.listing_status == "active")
    rows = session.execute(stmt).all()
    return pd.DataFrame(rows, columns=list(LISTING_COLUMNS))


def clean(df: pd.DataFrame, *, bounds: CleanBounds = DEFAULT_BOUNDS) -> pd.DataFrame:
    """Return a cleaned copy of ``df``: required fields present, outliers clipped out."""
    out = df.copy()

    # 1. must have a target and the single strongest predictor
    out = out.dropna(subset=["price_eur", "living_area_sqm"])
    out = out[(out["price_eur"] > 0) & (out["living_area_sqm"] > 0)]

    # 2. derived sanity column
    out["price_per_sqm"] = out["price_eur"] / out["living_area_sqm"]

    # 3. range filters (a missing optional value is kept; only real outliers drop)
    out = _within(out, "price_eur", bounds.price_eur)
    out = _within(out, "living_area_sqm", bounds.living_area_sqm)
    out = _within(out, "price_per_sqm", bounds.price_per_sqm)
    out = _within(out, "rooms", bounds.rooms, keep_na=True)
    out = _within(out, "floor", bounds.floor, keep_na=True)

    # 4. same flat listed more than once -> keep the first occurrence
    out = out.drop_duplicates(subset=["address", "price_eur", "living_area_sqm"], keep="first")

    return out.reset_index(drop=True)


def _within(
    df: pd.DataFrame, column: str, bounds: tuple[float, float], *, keep_na: bool = False
) -> pd.DataFrame:
    lo, hi = bounds
    in_range = df[column].between(lo, hi)
    if keep_na:
        in_range = in_range | df[column].isna()
    return df[in_range]
