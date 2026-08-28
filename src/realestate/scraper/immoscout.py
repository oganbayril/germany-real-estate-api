"""ImmoScout24 source -- DORMANT.

ImmoScout24 serves a DataDome "Ich bin kein Roboter" CAPTCHA (HTTP 401) to every
plain HTTP client, on both search and expose URLs. Getting past it needs a
headless browser or residential proxies -- both outside this project's
polite-scraping policy -- so Immowelt is the active source (see ``immowelt.py``).

Kept as a placeholder in case IS24 access becomes possible later (e.g. an
official partner feed).
"""

from __future__ import annotations

from collections.abc import Iterator

from realestate.scraper.base import Fetcher, Record, SearchTask

BASE_URL = "https://www.immobilienscout24.de"


class ImmoScout24Source:
    name = "immoscout24"

    def discover(self, fetch: Fetcher) -> Iterator[SearchTask]:
        raise NotImplementedError(
            "ImmoScout24 blocks plain HTTP clients (401 CAPTCHA wall); use ImmoweltSource."
        )
        yield  # pragma: no cover - makes this a generator for typing

    def parse_listings(self, html: str, task: SearchTask) -> list[Record]:
        raise NotImplementedError
