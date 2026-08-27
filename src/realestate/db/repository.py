"""Write helpers: upsert a listing (recording price history), track scrape runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from realestate.db.models import Listing, ListingPriceHistory, ScrapeRun, utcnow

# Fields a parsed record may set directly on Listing.
_LISTING_FIELDS = {
    "url",
    "city",
    "postal_code",
    "district",
    "address",
    "latitude",
    "longitude",
    "price_eur",
    "living_area_sqm",
    "plot_area_sqm",
    "rooms",
    "bedrooms",
    "bathrooms",
    "year_built",
    "floor",
    "total_floors",
    "property_type",
    "condition",
    "heating_type",
    "energy_efficiency_class",
    "energy_consumption_kwh",
    "title",
    "has_balcony",
    "has_garden",
    "has_elevator",
    "has_cellar",
    "has_parking",
    "is_barrier_free",
    "raw",
}


@dataclass
class UpsertResult:
    listing: Listing
    is_new: bool
    price_changed: bool


def upsert_listing(
    session: Session, record: dict[str, Any], *, scrape_run_id: int | None = None
) -> UpsertResult:
    """Insert or update a listing by expose_id.

    Appends a ``listing_price_history`` row on first sight and whenever the price
    differs from the last known value.
    """
    expose_id = str(record["expose_id"])
    listing = session.scalars(select(Listing).where(Listing.expose_id == expose_id)).one_or_none()

    now = utcnow()
    is_new = listing is None
    old_price = None if listing is None else listing.price_eur

    if listing is None:
        listing = Listing(expose_id=expose_id, first_seen_at=now)
        session.add(listing)

    for key in _LISTING_FIELDS & record.keys():
        setattr(listing, key, record[key])

    listing.last_seen_at = now
    listing.listing_status = "active"

    new_price = record.get("price_eur")
    price_changed = (
        new_price is not None and old_price is not None and float(new_price) != float(old_price)
    )
    if new_price is not None and (is_new or price_changed):
        session.add(
            ListingPriceHistory(
                listing=listing,
                expose_id=expose_id,
                price_eur=float(new_price),
                observed_at=now,
                scrape_run_id=scrape_run_id,
            )
        )

    session.flush()
    return UpsertResult(listing=listing, is_new=is_new, price_changed=price_changed)


def start_scrape_run(session: Session, cities: list[str], *, dry_run: bool = False) -> ScrapeRun:
    run = ScrapeRun(cities=list(cities), dry_run=dry_run, status="running")
    session.add(run)
    session.flush()
    return run


def finish_scrape_run(
    session: Session, run: ScrapeRun, *, status: str, error: str | None = None, **counts: int
) -> ScrapeRun:
    run.status = status
    run.error = error
    run.finished_at = utcnow()
    for key, value in counts.items():
        setattr(run, key, value)
    session.flush()
    return run
