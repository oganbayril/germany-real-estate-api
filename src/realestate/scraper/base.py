"""Source-agnostic types for the scraper.

A ``Source`` knows how to:
  * ``discover`` search-results page URLs (each tagged with the city it covers), and
  * ``parse_listings`` an HTML results page into normalized listing records whose
    keys are a subset of ``realestate.db.repository._LISTING_FIELDS`` plus
    ``expose_id``.

Detail ("expose") pages are intentionally not fetched: the major German portals
put them behind a DataDome bot wall. Everything we store comes from the
search-results cards, which are served without challenge.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# Fetch a URL and return its response body as text.
Fetcher = Callable[[str], str]

# A normalized listing record: {"expose_id": ..., "city": ..., "price_eur": ..., ...}
Record = dict[str, Any]


@dataclass(frozen=True)
class SearchTask:
    """One search-results page to fetch, and the city it belongs to."""

    url: str
    city: str


@runtime_checkable
class Source(Protocol):
    name: str
    # Hosts the scraper is allowed to fetch / be redirected to for this source.
    allowed_hosts: frozenset[str]

    def discover(self, fetch: Fetcher) -> Iterator[SearchTask]:
        """Yield search-results page URLs to scrape."""

    def parse_listings(self, html: str, task: SearchTask) -> list[Record]:
        """Extract normalized listing records from one search-results page."""
