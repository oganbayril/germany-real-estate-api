"""SQLAlchemy ORM models.

Tables:
  listings              - current state of each expose (natural key: expose_id)
  listing_price_history - append-only row per observed price (first sight + changes)
  scrape_runs           - one row per scrape invocation (audit / freshness)
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class Listing(TimestampMixin, Base):
    __tablename__ = "listings"
    __table_args__ = (
        Index("ix_listings_city_price", "city", "price_eur"),
        Index("ix_listings_status_last_seen", "listing_status", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    expose_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    url: Mapped[str | None] = mapped_column(String(512))

    # Location
    city: Mapped[str] = mapped_column(String(64), index=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), index=True)
    district: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(256))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # Core numeric attributes
    price_eur: Mapped[float | None] = mapped_column(Float)
    living_area_sqm: Mapped[float | None] = mapped_column(Float)
    plot_area_sqm: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)

    # Categorical / descriptive
    property_type: Mapped[str | None] = mapped_column(String(64))
    condition: Mapped[str | None] = mapped_column(String(64))
    heating_type: Mapped[str | None] = mapped_column(String(64))
    energy_efficiency_class: Mapped[str | None] = mapped_column(String(8))
    energy_consumption_kwh: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(String(256))

    # Boolean amenities (nullable: unknown vs absent)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    has_garden: Mapped[bool | None] = mapped_column(Boolean)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_cellar: Mapped[bool | None] = mapped_column(Boolean)
    has_parking: Mapped[bool | None] = mapped_column(Boolean)
    is_barrier_free: Mapped[bool | None] = mapped_column(Boolean)

    # Lifecycle
    listing_status: Mapped[str] = mapped_column(String(16), default="active")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Full parsed payload, kept for reprocessing without a re-scrape
    raw: Mapped[dict | None] = mapped_column(JSON)

    price_history: Mapped[list[ListingPriceHistory]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="ListingPriceHistory.observed_at",
    )


class ListingPriceHistory(Base):
    __tablename__ = "listing_price_history"
    __table_args__ = (Index("ix_price_history_listing_observed", "listing_id", "observed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    expose_id: Mapped[str] = mapped_column(String(32), index=True)
    price_eur: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    scrape_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scrape_runs.id", ondelete="SET NULL")
    )

    listing: Mapped[Listing] = relationship(back_populates="price_history")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    cities: Mapped[list | None] = mapped_column(JSON)

    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    exposes_seen: Mapped[int] = mapped_column(Integer, default=0)
    listings_new: Mapped[int] = mapped_column(Integer, default=0)
    listings_updated: Mapped[int] = mapped_column(Integer, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
