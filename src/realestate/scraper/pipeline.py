"""Scrape orchestration.

``discover`` yields search-results URLs (each tagged with a city). For each we
fetch the page and upsert every listing card it contains. Each listing is
committed on its own so a crash or a block partway through keeps everything
gathered so far.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from realestate.config import Settings, get_settings
from realestate.db.models import ScrapeRun
from realestate.db.repository import finish_scrape_run, start_scrape_run, upsert_listing
from realestate.db.session import session_scope
from realestate.scraper.base import Source
from realestate.scraper.client import BlockedError, ScrapeClient, ScrapeError
from realestate.scraper.immowelt import ImmoweltSource

log = logging.getLogger(__name__)


@dataclass
class ScrapeStats:
    pages_fetched: int = 0
    exposes_seen: int = 0
    listings_new: int = 0
    listings_updated: int = 0
    price_changes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_counts(self) -> dict[str, int]:
        return {
            "pages_fetched": self.pages_fetched,
            "exposes_seen": self.exposes_seen,
            "listings_new": self.listings_new,
            "listings_updated": self.listings_updated,
            "price_changes": self.price_changes,
        }


def run_scrape(
    cities: list[str] | None = None,
    *,
    max_search_urls_per_city: int | None = None,
    dry_run: bool = False,
    source: Source | None = None,
    settings: Settings | None = None,
) -> ScrapeStats:
    settings = settings or get_settings()
    cities = cities or settings.scrape_cities
    cap = (
        max_search_urls_per_city
        if max_search_urls_per_city is not None
        else settings.scrape_max_search_urls_per_city
    )
    source = source or ImmoweltSource(cities=cities, max_search_urls_per_city=cap)

    stats = ScrapeStats()
    client = ScrapeClient(
        user_agent=settings.scrape_user_agent,
        delay_min_s=settings.scrape_delay_min_s,
        delay_max_s=settings.scrape_delay_max_s,
        timeout_s=settings.scrape_request_timeout_s,
    )

    with session_scope() as session:
        run_id = start_scrape_run(session, cities, dry_run=dry_run).id

    status = "success"
    error: str | None = None
    try:
        with client:
            _run(client, source, run_id, dry_run, stats)
    except BlockedError as exc:
        status, error = "blocked", str(exc)
        log.error("scrape blocked: %s", exc)
    except Exception as exc:
        status, error = "failed", str(exc)
        raise
    finally:
        if status == "success" and stats.errors:
            status = "partial"
        with session_scope() as session:
            run = session.get(ScrapeRun, run_id)
            if run is not None:
                finish_scrape_run(
                    session,
                    run,
                    status=status,
                    error=error or ("; ".join(stats.errors[:5]) or None),
                    **stats.as_counts(),
                )

    return stats


def _run(
    client: ScrapeClient, source: Source, run_id: int, dry_run: bool, stats: ScrapeStats
) -> None:
    seen: set[str] = set()

    for task in source.discover(lambda url: client.get(url).text):
        try:
            html = client.get(task.url).text
        except BlockedError:
            raise
        except ScrapeError as exc:
            stats.errors.append(f"search {task.url}: {exc}")
            continue
        stats.pages_fetched += 1

        for record in source.parse_listings(html, task):
            if record["expose_id"] in seen:
                continue
            seen.add(record["expose_id"])
            stats.exposes_seen += 1
            if dry_run:
                continue

            with session_scope() as session:
                result = upsert_listing(session, record, scrape_run_id=run_id)
                is_new, price_changed = result.is_new, result.price_changed
            stats.listings_new += int(is_new)
            stats.listings_updated += int(not is_new)
            stats.price_changes += int(price_changed)
