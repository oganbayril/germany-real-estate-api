"""The cities the project knows how to scrape and show.

One entry per city: our slug (stored in ``listings.city``, used in requests and
``RE_SCRAPE_CITIES``), the display name, and the slug Immowelt uses in its
search URLs (umlauts folded without the "e", spaces as hyphens). Adding a city
is one entry here, verified against a real sitemap URL, plus its slug in
``RE_SCRAPE_CITIES``. The demo page offers it once the model has learned from
enough of its listings (``RE_MIN_CITY_ROWS``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    slug: str
    name: str
    immowelt_slug: str


CITIES: dict[str, City] = {
    c.slug: c
    for c in [
        City("berlin", "Berlin", "berlin"),
        City("hamburg", "Hamburg", "hamburg"),
        City("muenchen", "München", "munchen"),
        City("koeln", "Köln", "koln"),
        City("leipzig", "Leipzig", "leipzig"),
        # added 2026-10-29; Immowelt slugs checked against its sitemap that day
        City("frankfurt", "Frankfurt am Main", "frankfurt-am-main"),
        City("stuttgart", "Stuttgart", "stuttgart"),
        City("duesseldorf", "Düsseldorf", "dusseldorf"),
        City("dresden", "Dresden", "dresden"),
        City("hannover", "Hannover", "hannover"),
    ]
}


def city_name(slug: str) -> str:
    city = CITIES.get(slug)
    return city.name if city else slug.title()
