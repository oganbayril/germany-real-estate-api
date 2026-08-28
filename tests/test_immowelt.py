"""Immowelt parsing, verified against a saved (trimmed) search-results fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from realestate.scraper.base import SearchTask
from realestate.scraper.immowelt import ImmoweltSource

FIXTURES = Path(__file__).parent / "fixtures" / "immowelt"


@pytest.fixture
def source() -> ImmoweltSource:
    return ImmoweltSource()


def _search_html() -> str:
    return (FIXTURES / "search_berlin_p1.html").read_text(encoding="utf-8")


# -- discovery -------------------------------------------------------------
def test_discover_filters_to_target_cities(source: ImmoweltSource) -> None:
    index = """<sitemapindex>
      <sitemap><loc>https://www.immowelt.de/sitemaps/BUY_APARTMENT_X/x_1.xml</loc></sitemap>
      <sitemap><loc>https://www.immowelt.de/sitemaps/RENT_APARTMENT_Y/y_1.xml</loc></sitemap>
    </sitemapindex>"""
    sub = """<urlset>
      <url><loc>https://www.immowelt.de/suche/kaufen/wohnung/berlin-10115/moabit-10557/nbh2de1</loc></url>
      <url><loc>https://www.immowelt.de/suche/kaufen/wohnung/koln-50667/altstadt-50667/nbh2de2</loc></url>
      <url><loc>https://www.immowelt.de/suche/kaufen/wohnung/erfurt-99084/nord-99085/nbh2de3</loc></url>
    </urlset>"""
    pages = {
        "https://www.immowelt.de/sitemaps/sitemap_index.xml": index,
        "https://www.immowelt.de/sitemaps/BUY_APARTMENT_X/x_1.xml": sub,
    }
    tasks = list(source.discover(lambda url: pages[url]))

    assert {t.city for t in tasks} == {"berlin", "koeln"}  # erfurt + rent sitemap excluded


def test_discover_respects_per_city_cap() -> None:
    src = ImmoweltSource(cities=["berlin"], max_search_urls_per_city=3)
    subs = [f"https://www.immowelt.de/sitemaps/BUY_APARTMENT_{n}/s_1.xml" for n in range(5)]
    index = (
        "<sitemapindex>"
        + "".join(f"<sitemap><loc>{s}</loc></sitemap>" for s in subs)
        + "</sitemapindex>"
    )
    pages = {"https://www.immowelt.de/sitemaps/sitemap_index.xml": index}
    for j, s in enumerate(subs):
        pages[s] = (
            "<urlset>"
            + "".join(
                f"<url><loc>https://www.immowelt.de/suche/kaufen/wohnung/berlin-10115/q{j}{i}-1000{i}/nbh{j}{i}</loc></url>"
                for i in range(4)
            )
            + "</urlset>"
        )

    tasks = list(src.discover(lambda u: pages[u]))
    assert len(tasks) == 3  # cap honoured
    # quota is 1/sitemap (3 // 8 -> 1), so the 3 come from 3 different sub-sitemaps
    assert len({t.url.split("/nbh")[1][0] for t in tasks}) == 3


# -- listing cards ------------------------------------------------------
def test_parse_listings_shape(source: ImmoweltSource) -> None:
    task = SearchTask(url="https://www.immowelt.de/suche/...", city="berlin")
    records = source.parse_listings(_search_html(), task)

    assert len(records) >= 5
    for r in records:
        assert len(r["expose_id"]) == 36
        assert r["url"].startswith("https://www.immowelt.de/expose/")
        assert r["city"] == "berlin"
        assert r["price_eur"] > 10_000
        assert 10 < r["living_area_sqm"] < 1000
        assert r["property_type"] == "apartment"


def test_parse_listings_first_card_values(source: ImmoweltSource) -> None:
    task = SearchTask(url="u", city="berlin")
    records = source.parse_listings(_search_html(), task)
    by_id = {r["expose_id"]: r for r in records}

    first = by_id["1215b46c-9e03-4081-bc72-67209544ff9f"]
    assert first["price_eur"] == 170_000
    assert first["living_area_sqm"] == 45
    assert first["rooms"] == 2
    assert first["postal_code"] == "12557"
    assert first["district"] == "Treptow-Köpenick"
    assert first["energy_efficiency_class"] == "D"
    assert first["raw"]["price_per_sqm"] == pytest.approx(3777.78, abs=0.5)

    second = by_id["9b5ed64e-3bd9-41ef-9ef0-8b138f1c19c0"]
    assert second["price_eur"] == 310_200
    assert second["living_area_sqm"] == pytest.approx(57.4)
    assert second["floor"] == 2
    assert second["postal_code"] == "10437"
