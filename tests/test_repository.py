"""Phase 1: upsert semantics and price-history tracking."""

from __future__ import annotations

from sqlalchemy.orm import Session

from realestate.db.models import Listing, ListingPriceHistory
from realestate.db.repository import (
    finish_scrape_run,
    start_scrape_run,
    upsert_listing,
)


def _record(**overrides: object) -> dict[str, object]:
    base = {
        "expose_id": "12345",
        "url": "https://www.immobilienscout24.de/expose/12345",
        "city": "berlin",
        "postal_code": "10115",
        "price_eur": 500_000.0,
        "living_area_sqm": 80.0,
        "rooms": 3.0,
        "year_built": 1998,
        "has_balcony": True,
    }
    base.update(overrides)
    return base


def test_insert_new_listing_records_initial_price(db_session: Session) -> None:
    result = upsert_listing(db_session, _record())
    db_session.commit()

    assert result.is_new is True
    assert result.price_changed is False
    assert db_session.query(Listing).count() == 1
    history = db_session.query(ListingPriceHistory).all()
    assert len(history) == 1
    assert history[0].price_eur == 500_000.0


def test_reupsert_same_price_adds_no_history(db_session: Session) -> None:
    upsert_listing(db_session, _record())
    db_session.commit()
    result = upsert_listing(db_session, _record(living_area_sqm=82.0))
    db_session.commit()

    assert result.is_new is False
    assert result.price_changed is False
    assert db_session.query(ListingPriceHistory).count() == 1
    assert db_session.query(Listing).one().living_area_sqm == 82.0


def test_price_change_appends_history(db_session: Session) -> None:
    upsert_listing(db_session, _record())
    db_session.commit()
    result = upsert_listing(db_session, _record(price_eur=475_000.0))
    db_session.commit()

    assert result.price_changed is True
    prices = [
        h.price_eur
        for h in db_session.query(ListingPriceHistory).order_by(ListingPriceHistory.observed_at)
    ]
    assert prices == [500_000.0, 475_000.0]
    assert db_session.query(Listing).one().price_eur == 475_000.0


def test_scrape_run_lifecycle(db_session: Session) -> None:
    run = start_scrape_run(db_session, ["berlin", "hamburg"], dry_run=False)
    assert run.status == "running"
    assert run.cities == ["berlin", "hamburg"]

    upsert_listing(db_session, _record(), scrape_run_id=run.id)
    finish_scrape_run(
        db_session, run, status="success", exposes_seen=1, listings_new=1, pages_fetched=2
    )
    db_session.commit()

    assert run.status == "success"
    assert run.finished_at is not None
    assert run.listings_new == 1
    assert run.pages_fetched == 2
