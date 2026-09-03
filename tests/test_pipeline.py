"""Pipeline orchestration: DB writes, stats, and scrape-run bookkeeping."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from realestate.config import Settings
from realestate.db.models import Base, Listing, ScrapeRun
from realestate.scraper import pipeline
from realestate.scraper.base import SearchTask


class FakeSource:
    name = "fake"

    def __init__(self, n_listings: int = 3) -> None:
        self.n_listings = n_listings

    def discover(self, fetch):  # noqa: ARG002
        yield SearchTask(url="https://example.test/search/1", city="berlin")

    def parse_listings(self, html: str, task: SearchTask) -> list[dict]:  # noqa: ARG002
        return [
            {
                "expose_id": f"id-{i}",
                "url": f"https://example.test/expose/{i}",
                "city": task.city,
                "price_eur": 300_000.0,
                "living_area_sqm": 70.0,
                "rooms": 3.0,
            }
            for i in range(self.n_listings)
        ]


class SoftBlockedSource(FakeSource):
    """Search pages load (200) but yield no listings -- what DataDome does to a
    flagged datacenter IP."""

    def discover(self, fetch):  # noqa: ARG002
        for i in range(4):
            yield SearchTask(url=f"https://example.test/search/{i}", city="berlin")

    def parse_listings(self, html: str, task: SearchTask) -> list[dict]:  # noqa: ARG002
        return []


@pytest.fixture
def wired_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class _Scope:
        def __enter__(self) -> Session:
            self._s = factory()
            return self._s

        def __exit__(self, *exc: object) -> None:
            if exc[0] is None:
                self._s.commit()
            else:
                self._s.rollback()
            self._s.close()

    monkeypatch.setattr(pipeline, "session_scope", _Scope)
    yield factory
    engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, scrape_delay_min_s=0, scrape_delay_max_s=0)


def test_run_scrape_persists_listings_and_run(
    wired_db: sessionmaker[Session], settings: Settings, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="https://example.test/robots.txt", text="User-agent: *\nAllow: /")
    httpx_mock.add_response(text="<html>ok</html>", is_reusable=True)

    stats = pipeline.run_scrape(
        cities=["berlin"], source=FakeSource(n_listings=3), settings=settings
    )

    assert stats.exposes_seen == 3
    assert stats.listings_new == 3

    session = wired_db()
    assert session.query(Listing).count() == 3
    run = session.query(ScrapeRun).one()
    assert run.status == "success"
    assert run.listings_new == 3
    assert run.finished_at is not None
    session.close()


def test_second_run_updates_not_inserts(
    wired_db: sessionmaker[Session], settings: Settings, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="https://example.test/robots.txt", text="User-agent: *\nAllow: /")
    httpx_mock.add_response(text="<html>ok</html>", is_reusable=True)

    pipeline.run_scrape(cities=["berlin"], source=FakeSource(n_listings=2), settings=settings)
    stats = pipeline.run_scrape(
        cities=["berlin"], source=FakeSource(n_listings=2), settings=settings
    )

    assert stats.listings_new == 0
    assert stats.listings_updated == 2
    session = wired_db()
    assert session.query(Listing).count() == 2
    session.close()


def test_soft_block_marks_run_blocked(
    wired_db: sessionmaker[Session], settings: Settings, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="https://example.test/robots.txt", text="User-agent: *\nAllow: /")
    httpx_mock.add_response(text="<html>ok</html>", is_reusable=True)

    pipeline.run_scrape(cities=["berlin"], source=SoftBlockedSource(), settings=settings)

    session = wired_db()
    run = session.query(ScrapeRun).one()
    assert run.status == "blocked"
    assert run.pages_fetched == 4
    assert run.exposes_seen == 0
    session.close()
