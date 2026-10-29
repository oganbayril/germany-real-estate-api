"""Immowelt parsing, verified against a saved (trimmed) search-results fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from realestate.scraper.base import SearchTask
from realestate.scraper.immowelt import ImmoweltSource

FIXTURES = Path(__file__).parent / "fixtures" / "immowelt"


@pytest.fixture
def source(tmp_path: Path) -> ImmoweltSource:
    return ImmoweltSource(cache_path=tmp_path / "cache.json")


def _search_html() -> str:
    return (FIXTURES / "search_berlin_p1.html").read_text(encoding="utf-8")


def _sitemaps(city_slugs: list[str], per: int = 4) -> dict[str, str]:
    subs = [f"https://www.immowelt.de/sitemaps/BUY_APARTMENT_{n}/s_1.xml" for n in range(6)]
    index = (
        "<sitemapindex>"
        + "".join(f"<sitemap><loc>{s}</loc></sitemap>" for s in subs)
        + "<sitemap><loc>https://www.immowelt.de/sitemaps/RENT_APARTMENT_R/r_1.xml</loc></sitemap>"
        + "</sitemapindex>"
    )
    pages = {"https://www.immowelt.de/sitemaps/sitemap_index.xml": index}
    for j, s in enumerate(subs):
        urls = [
            f"https://www.immowelt.de/suche/kaufen/wohnung/{slug}-10115/q{j}{i}-2000{i}/nbh{j}{i}"
            for slug in city_slugs
            for i in range(per)
        ]
        pages[s] = "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
    return pages


# -- discovery -------------------------------------------------------------
def test_discover_filters_to_target_cities(tmp_path: Path) -> None:
    src = ImmoweltSource(cache_path=tmp_path / "c.json")
    pages = _sitemaps(["berlin", "koln", "erfurt"])
    tasks = list(src.discover(lambda url: pages[url]))
    assert {t.city for t in tasks} == {"berlin", "koeln"}  # erfurt not a target


def test_discover_respects_per_city_cap(tmp_path: Path) -> None:
    src = ImmoweltSource(
        cities=["berlin"], max_search_urls_per_city=3, cache_path=tmp_path / "c.json"
    )
    pages = _sitemaps(["berlin"], per=10)
    tasks = list(src.discover(lambda url: pages[url]))
    assert len(tasks) == 3
    assert all(t.city == "berlin" for t in tasks)


def test_discover_uses_cache_on_second_run(tmp_path: Path) -> None:
    cache = tmp_path / "c.json"
    pages = _sitemaps(["berlin"], per=8)
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return pages[url]

    src = ImmoweltSource(cities=["berlin"], max_search_urls_per_city=3, cache_path=cache)
    list(src.discover(fetch))
    assert calls  # walked the sitemaps
    assert cache.exists()

    calls.clear()
    src2 = ImmoweltSource(cities=["berlin"], max_search_urls_per_city=3, cache_path=cache)
    tasks = list(src2.discover(fetch))
    assert calls == []  # served entirely from cache, zero network
    assert len(tasks) == 3


def test_discover_rebuilds_when_cache_stale(tmp_path: Path) -> None:
    cache = tmp_path / "c.json"
    cache.write_text(
        json.dumps({"built_at": "2000-01-01T00:00:00+00:00", "urls": [["berlin", "x"]]}), "utf-8"
    )
    pages = _sitemaps(["berlin"], per=4)
    calls: list[str] = []
    src = ImmoweltSource(cities=["berlin"], max_search_urls_per_city=3, cache_path=cache)
    list(src.discover(lambda u: (calls.append(u), pages[u])[1]))
    assert calls  # stale -> re-walked


def test_discover_handles_multi_word_city_slugs(tmp_path: Path) -> None:
    src = ImmoweltSource(cities=["frankfurt"], cache_path=tmp_path / "c.json")
    pages = _sitemaps(["frankfurt-am-main", "frankfurt-oder"])
    tasks = list(src.discover(lambda url: pages[url]))
    assert tasks
    assert all("/frankfurt-am-main-" in t.url and t.city == "frankfurt" for t in tasks)


def test_discover_rebuilds_fresh_cache_when_a_city_is_added(tmp_path: Path) -> None:
    cache = tmp_path / "c.json"
    pages = _sitemaps(["berlin", "dresden"], per=4)
    list(ImmoweltSource(cities=["berlin"], cache_path=cache).discover(lambda u: pages[u]))

    calls: list[str] = []
    src = ImmoweltSource(cities=["berlin", "dresden"], cache_path=cache)
    tasks = list(src.discover(lambda u: (calls.append(u), pages[u])[1]))
    assert calls  # cache is fresh, but was never built for dresden
    assert {t.city for t in tasks} == {"berlin", "dresden"}


def test_discover_does_not_rebuild_for_a_city_with_no_urls(tmp_path: Path) -> None:
    cache = tmp_path / "c.json"
    pages = _sitemaps(["berlin"], per=4)  # no hannover urls anywhere
    list(
        ImmoweltSource(cities=["berlin", "hannover"], cache_path=cache).discover(lambda u: pages[u])
    )

    calls: list[str] = []
    src = ImmoweltSource(cities=["berlin", "hannover"], cache_path=cache)
    list(src.discover(lambda u: (calls.append(u), pages[u])[1]))
    assert calls == []  # asked for already; re-walking every run would waste requests


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


# -- Oct 2026 layout ------------------------------------------------------
def test_parse_listings_2026_10_layout(source: ImmoweltSource) -> None:
    # the rooms/area/floor line lost its cardmfe-keyfacts-testid; every run
    # parsed 0 listings until the parser fell back to the unlabeled line
    html = (FIXTURES / "search_berlin_2026-10.html").read_text(encoding="utf-8")
    records = source.parse_listings(html, SearchTask(url="u", city="berlin"))
    assert len(records) == 5
    by_id = {r["expose_id"]: r for r in records}

    first = by_id["15a88be2-8f1b-4e42-b7fd-d1bcf7af04ea"]
    assert first["price_eur"] == 275_000
    assert first["living_area_sqm"] == 63
    assert first["rooms"] == 3
    assert first["floor"] == 3
    assert first["postal_code"] == "10785"
    # "Tiergarten, Berlin (10785)": no district segment, so no district
    assert first["district"] is None
    assert first["energy_efficiency_class"] is None

    second = by_id["9d16279f-9e09-4d46-b31b-9adb51dba7c2"]
    assert second["living_area_sqm"] == pytest.approx(92.5)
    assert second["energy_efficiency_class"] == "D"


def test_unparseable_cards_raise_layout_changed(source: ImmoweltSource) -> None:
    from realestate.scraper.base import LayoutChanged

    card = (
        '<div data-testid="serp-core-classified-card-testid">'
        '<a href="/expose/15a88be2-8f1b-4e42-b7fd-d1bcf7af04ea">x</a>'
        '<div data-testid="cardmfe-price-testid" aria-label="275000 €">275.000 €</div>'
        "<div>no key facts anywhere</div></div>"
    )
    with pytest.raises(LayoutChanged, match="5 listing cards"):
        source.parse_listings(card * 5, SearchTask(url="u", city="berlin"))
    # a couple of odd cards on their own are normal, not a layout change
    assert source.parse_listings(card * 2, SearchTask(url="u", city="berlin")) == []
