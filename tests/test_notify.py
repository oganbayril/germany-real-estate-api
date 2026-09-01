"""Email notifications and the retrain safety guard."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from realestate.config import Settings
from realestate.db.models import Base, Listing, ScrapeRun, utcnow
from realestate.model.train import TrainingBlocked, _load_frame
from realestate.notify import send_email


def test_send_email_noop_when_unconfigured() -> None:
    settings = Settings(_env_file=None)  # no SMTP creds
    assert send_email("subject", "body", settings=settings) is False


def test_email_configured_flag() -> None:
    assert Settings(_env_file=None).email_configured is False
    full = Settings(
        _env_file=None,
        smtp_user="a@b.c",
        smtp_password="x",
        email_to="a@b.c",
    )
    assert full.email_configured is True


@pytest.fixture
def wired_scope(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class _Scope:
        def __enter__(self) -> Session:
            self._s = factory()
            return self._s

        def __exit__(self, *exc: object) -> None:
            self._s.close()

    monkeypatch.setattr("realestate.db.session.session_scope", _Scope)
    return factory


def _seed_listings(session: Session, n: int = 60) -> None:
    for i in range(n):
        session.add(
            Listing(
                expose_id=f"e{i}",
                city="berlin",
                price_eur=300_000 + i * 1000,
                living_area_sqm=70,
                listing_status="active",
            )
        )
    session.commit()


def test_load_frame_blocks_after_failed_scrape(wired_scope: sessionmaker[Session]) -> None:
    with wired_scope() as s:
        _seed_listings(s)
        s.add(ScrapeRun(status="blocked", finished_at=utcnow(), cities=["berlin"]))
        s.commit()

    with pytest.raises(TrainingBlocked, match="blocked"):
        _load_frame(from_sample=False)


def test_load_frame_ok_after_successful_scrape(wired_scope: sessionmaker[Session]) -> None:
    with wired_scope() as s:
        _seed_listings(s)
        s.add(ScrapeRun(status="success", finished_at=utcnow(), cities=["berlin"]))
        s.commit()

    df = _load_frame(from_sample=False)
    assert len(df) == 60
