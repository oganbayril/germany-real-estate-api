"""Immowelt source.

Discovery walks the site's published XML sitemaps: the sitemap index links to
per-category sub-sitemaps, and the buy/apartment ones list district- and
filter-scoped search URLs (each its own page-1 result set of ~30 listings).
The city-matching URLs are cached to a JSON file and reused for
``scrape_discovery_cache_days`` -- so most runs spend their (rate-limited,
DataDome-watched) requests only on real search pages. Each run scrapes a fresh
random slice of the pool.

We do not fetch expose pages -- Immowelt guards them with DataDome. Everything is
parsed from the search-results cards (server-rendered ``data-testid`` blocks);
verified against ``tests/fixtures/immowelt/search_berlin_p1.html``.
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from selectolax.parser import HTMLParser, Node

from realestate.config import get_settings
from realestate.scraper.base import Fetcher, Record, SearchTask

log = logging.getLogger(__name__)

BASE_URL = "https://www.immowelt.de"
SITEMAP_INDEX = f"{BASE_URL}/sitemaps/sitemap_index.xml"
_POOL_PER_CITY = 80  # search URLs kept per city in the discovery cache

# our canonical city slug -> the slug Immowelt uses in URLs (umlauts folded, no 'e')
CITY_TO_IMMOWELT = {
    "berlin": "berlin",
    "muenchen": "munchen",
    "hamburg": "hamburg",
    "koeln": "koln",
    "leipzig": "leipzig",
}
_IMMOWELT_TO_CITY = {v: k for k, v in CITY_TO_IMMOWELT.items()}

_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_EXPOSE_RE = re.compile(r"/expose/([0-9a-f-]{36})")
_CITY_SEG_RE = re.compile(r"/([a-z]+)-\d{4,5}/")
_NUM_RE = re.compile(r"-?\d[\d.]*(?:,\d+)?")
_FLOOR_RE = re.compile(r"(\d+)\.\s*(?:OG|Geschoss|Obergeschoss)", re.IGNORECASE)


class ImmoweltSource:
    name = "immowelt"
    allowed_hosts = frozenset({"www.immowelt.de"})

    def __init__(
        self,
        *,
        cities: list[str] | None = None,
        max_search_urls_per_city: int = 40,
        cache_path: Path | None = None,
        cache_max_age_days: float | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.cities = [c for c in (cities or list(CITY_TO_IMMOWELT)) if c in CITY_TO_IMMOWELT]
        self.max_search_urls_per_city = max_search_urls_per_city
        settings = get_settings()
        self._cache_path = cache_path or (settings.data_dir / "immowelt_search_urls.json")
        if cache_max_age_days is None:
            cache_max_age_days = settings.scrape_discovery_cache_days
        self._cache_max_age = timedelta(days=cache_max_age_days)
        self._rng = rng or random.Random()

    # -- discovery ----------------------------------------------------------
    def discover(self, fetch: Fetcher) -> Iterator[SearchTask]:
        """Yield a fresh random slice of the cached search-URL pool.

        The pool is (re)built from the sitemaps only when the cache is missing or
        older than ``cache_max_age_days``.
        """
        pool = self._load_pool()
        if pool is None:
            pool = self._build_pool(fetch)
            self._save_pool(pool)

        by_city: dict[str, list[str]] = {}
        for city, url in pool:
            by_city.setdefault(city, []).append(url)
        for city, urls in sorted(by_city.items()):
            self._rng.shuffle(urls)
            for url in urls[: self.max_search_urls_per_city]:
                yield SearchTask(url=url, city=city)

    # -- discovery cache -------------------------------------------------
    def _load_pool(self) -> list[tuple[str, str]] | None:
        try:
            data = json.loads(self._cache_path.read_text("utf-8"))
            built_at = datetime.fromisoformat(data["built_at"])
        except (OSError, KeyError, ValueError):
            return None
        if datetime.now(UTC) - built_at > self._cache_max_age:
            log.info("discovery cache is stale; rebuilding from sitemaps")
            return None
        pool = [(c, u) for c, u in data.get("urls", []) if c in self.cities]
        if pool:
            log.info("discovery cache hit: %d urls (built %s)", len(pool), data["built_at"][:10])
        return pool or None

    def _save_pool(self, pool: list[tuple[str, str]]) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(
                    {"built_at": datetime.now(UTC).isoformat(), "urls": [list(p) for p in pool]}
                ),
                "utf-8",
            )
        except OSError as exc:
            log.warning("could not write discovery cache %s: %s", self._cache_path, exc)

    def _build_pool(self, fetch: Fetcher) -> list[tuple[str, str]]:
        """Walk the sitemaps, spreading the pool across the filter slices they expose."""
        wanted = {CITY_TO_IMMOWELT[c] for c in self.cities}
        try:
            index_xml = fetch(SITEMAP_INDEX)
        except Exception as exc:  # noqa: BLE001 - discovery failure is non-fatal
            log.error("could not fetch sitemap index: %s", exc)
            return []

        sub_sitemaps = [
            loc for loc in _LOC_RE.findall(index_xml) if "_APARTMENT_" in loc and "RENT" not in loc
        ]
        quota = max(1, _POOL_PER_CITY // 10)
        per_city = dict.fromkeys(wanted, 0)
        seen: set[str] = set()
        pool: list[tuple[str, str]] = []
        for sub_url in sub_sitemaps:
            if all(n >= _POOL_PER_CITY for n in per_city.values()):
                break
            try:
                sub_xml = fetch(sub_url)
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping sub-sitemap %s: %s", sub_url, exc)
                continue
            taken = dict.fromkeys(wanted, 0)
            for url in _LOC_RE.findall(sub_xml):
                match = _CITY_SEG_RE.search(url)
                if not match or url in seen:
                    continue
                slug = match.group(1)
                if slug not in wanted or per_city[slug] >= _POOL_PER_CITY or taken[slug] >= quota:
                    continue
                seen.add(url)
                per_city[slug] += 1
                taken[slug] += 1
                pool.append((_IMMOWELT_TO_CITY[slug], url))
        log.info("built discovery pool: %d urls across %d cities", len(pool), len(per_city))
        return pool

    # -- parsing ----------------------------------------------------------
    def parse_listings(self, html: str, task: SearchTask) -> list[Record]:
        tree = HTMLParser(html)
        records: dict[str, Record] = {}
        for card in tree.css("[data-testid='serp-core-classified-card-testid']"):
            record = _card_record(card, task.city)
            if record is not None:
                records.setdefault(record["expose_id"], record)
        return list(records.values())


# --------------------------------------------------------------------------
# card parsing
# --------------------------------------------------------------------------
def _card_record(card: Node, city: str) -> Record | None:
    link = card.css_first("a[href*='/expose/']")
    match = _EXPOSE_RE.search(link.attributes.get("href") or "") if link else None
    if match is None:
        return None
    expose_id = match.group(1)

    price = _price(card)
    rooms, area, floor = _keyfacts(_tid_text(card, "cardmfe-keyfacts-testid"))
    if price is None or area is None:
        return None

    address = _tid_text(card, "cardmfe-description-box-address")
    district, postal = _split_address(address)

    return {
        "expose_id": expose_id,
        "url": f"{BASE_URL}/expose/{expose_id}",
        "city": city,
        "price_eur": price,
        "living_area_sqm": area,
        "rooms": rooms,
        "floor": floor,
        "address": address,
        "district": district,
        "postal_code": postal,
        "energy_efficiency_class": _energy_class(
            _tid_text(card, "card-mfe-energy-performance-class")
        ),
        "property_type": _property_type(_tid_text(card, "cardmfe-description-box-text-test-id")),
        "raw": {
            "source": "immowelt-card",
            "price_per_sqm": round(price / area, 2) if area else None,
            "keyfacts": _tid_text(card, "cardmfe-keyfacts-testid"),
        },
    }


def _tid_text(node: Node, testid: str) -> str | None:
    found = node.css_first(f"[data-testid='{testid}']")
    if found is None:
        return None
    return re.sub(r"\s+", " ", found.text(separator=" ", strip=True)) or None


def _price(card: Node) -> float | None:
    node = card.css_first("[data-testid='cardmfe-price-testid']")
    if node is None:
        return None
    # aria-label is the clean integer, e.g. "170000 €"
    return _num(node.attributes.get("aria-label")) or _num(node.text())


def _keyfacts(text: str | None) -> tuple[float | None, float | None, int | None]:
    if not text:
        return None, None, None
    rooms = _num(_search1(r"([\d,]+)\s*Zimmer", text))
    area = _num(_search1(r"([\d,]+)\s*m", text))
    floor_match = _FLOOR_RE.search(text)
    floor = int(floor_match.group(1)) if floor_match else None
    return rooms, area, floor


def _split_address(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    postal = _search1(r"\((\d{5})\)", text)
    without_postal = re.sub(r"\s*\(\d{5}\)\s*$", "", text).strip(" ,")
    # district = last comma-separated segment (the others are street / quarter)
    district = without_postal.split(",")[-1].strip() or None if without_postal else None
    return district, postal


def _energy_class(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"\b([A-H][+]?)\b", text)
    return match.group(1) if match else None


def _property_type(text: str | None) -> str:
    low = (text or "").lower()
    if "haus" in low:
        return "house"
    return "apartment"


# --------------------------------------------------------------------------
# scalar parsing
# --------------------------------------------------------------------------
def _search1(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _num(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    match = _NUM_RE.search(str(raw))
    if not match:
        return None
    token = match.group(0).replace(".", "").replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None
