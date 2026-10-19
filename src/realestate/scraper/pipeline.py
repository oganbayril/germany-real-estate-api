"""Scrape orchestration.

``discover`` yields search-results URLs (each tagged with a city). For each we
fetch the page and upsert every listing card it contains. Each listing is
committed on its own so a crash or a block partway through keeps everything
gathered so far.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from realestate.config import Settings, get_settings
from realestate.db.models import ScrapeRun
from realestate.db.repository import finish_scrape_run, start_scrape_run, upsert_listing
from realestate.db.session import session_scope
from realestate.scraper.base import LayoutChanged, Source
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
    first_block: dict[str, Any] | None = None

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
        allowed_hosts=getattr(source, "allowed_hosts", None),
    )

    with session_scope() as session:
        run_id = start_scrape_run(session, cities, dry_run=dry_run).id

    status = "success"
    error: str | None = None
    try:
        with client:
            _run(client, source, run_id, dry_run, stats)
    except BlockedError as exc:
        if stats.listings_new or stats.listings_updated:
            # got interrupted, but real data landed -- don't starve the retrain
            # guard forever just because DataDome eventually cuts every run off.
            status, error = "partial", f"blocked partway through: {exc}"
            log.warning("scrape blocked after partial progress: %s", exc)
        else:
            status, error = "blocked", str(exc)
            log.error("scrape blocked: %s", exc)
    except LayoutChanged as exc:
        # stop at the first such page: every later page would fail the same way
        status, error = "failed", f"layout changed: {exc}"
        stats.errors.append(error)
        log.error("scrape stopped: %s", exc)
    except Exception as exc:
        status, error = "failed", str(exc)
        raise
    finally:
        if status == "success" and stats.pages_fetched >= 3 and stats.exposes_seen == 0:
            # pages loaded but nothing parsed out of any of them -> soft block
            status = "blocked"
            error = error or (
                "fetched pages but parsed 0 listings (a soft block, or a layout change "
                "the parser didn't detect -- check the response sizes in diagnostics)"
            )
        elif status == "success" and stats.errors:
            status = "partial"
        diagnostics = _diagnostics(client.request_log, settings, cap)
        stats.first_block = diagnostics["first_block"]
        with session_scope() as session:
            run = session.get(ScrapeRun, run_id)
            if run is not None:
                finish_scrape_run(
                    session,
                    run,
                    status=status,
                    error=error or ("; ".join(stats.errors[:5]) or None),
                    diagnostics=diagnostics,
                    **stats.as_counts(),
                )

    return stats


def _diagnostics(request_log: list[dict[str, Any]], settings: Settings, cap: int) -> dict[str, Any]:
    """Summarise where in a run the first block hit, alongside the full log.

    The pacing settings are stored with it because they change between runs;
    without them, old runs couldn't be compared with new ones.
    """
    first = next((e for e in request_log if e.get("blocked")), None)
    first_block = None
    if first is not None:
        before = request_log[: first["n"] - 1]
        first_block = {
            "request_n": first["n"],
            "minutes_in": round(first["t_s"] / 60, 1),
            "ok_requests_before": sum(1 for e in before if e.get("status") == 200),
            "kind": first["kind"],
            "path": first["path"],
            "status": first.get("status"),
            "marker": first.get("marker"),
        }
    return {
        "delay_s": [settings.scrape_delay_min_s, settings.scrape_delay_max_s],
        "max_search_urls_per_city": cap,
        "requests_total": len(request_log),
        "first_block": first_block,
        "requests": request_log,
    }


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
