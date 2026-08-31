"""``realestate-scrape`` entrypoint.

Subcommands:
  run             run the scrape pipeline
  fetch-fixture   fetch one URL through the polite client and save the HTML
                  (for building parser tests / probing whether we get blocked)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from realestate.config import get_settings

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="realestate-scrape")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the scrape pipeline.")
    run.add_argument("--cities", nargs="+", default=settings.scrape_cities)
    run.add_argument(
        "--max-searches",
        type=int,
        default=settings.scrape_max_search_urls_per_city,
        help="Max search-results URLs to visit per city.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse but do not write to the DB.",
    )

    fx = sub.add_parser("fetch-fixture", help="Fetch a URL and save its HTML for tests.")
    fx.add_argument("url")
    fx.add_argument("--name", help="Output filename (default: derived from the URL).")
    fx.add_argument(
        "--no-robots",
        action="store_true",
        help="Skip the robots.txt check for this one fetch.",
    )
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from realestate.scraper.pipeline import run_scrape

    stats = run_scrape(
        cities=args.cities,
        max_search_urls_per_city=args.max_searches,
        dry_run=args.dry_run,
    )
    print(
        f"pages={stats.pages_fetched} exposes={stats.exposes_seen} "
        f"new={stats.listings_new} updated={stats.listings_updated} "
        f"price_changes={stats.price_changes} errors={len(stats.errors)}"
    )
    for err in stats.errors[:10]:
        print(f"  ! {err}", file=sys.stderr)
    return 0


def _cmd_fetch_fixture(args: argparse.Namespace) -> int:
    from urllib.parse import urlsplit

    from realestate.scraper.client import ScrapeClient, ScrapeError

    settings = get_settings()
    # keep the output inside tests/fixtures/ even if --name contains path parts
    name = args.name or _fixture_name(args.url)
    dest = (FIXTURES_DIR / name).resolve()
    if FIXTURES_DIR.resolve() not in dest.parents:
        print(f"refusing to write outside {FIXTURES_DIR}", file=sys.stderr)
        return 2
    dest.parent.mkdir(parents=True, exist_ok=True)

    host = urlsplit(args.url).netloc
    with ScrapeClient(
        user_agent=settings.scrape_user_agent,
        delay_min_s=2,
        delay_max_s=4,
        timeout_s=settings.scrape_request_timeout_s,
        respect_robots=not args.no_robots,
        allowed_hosts=frozenset({host}) if host else None,
    ) as client:
        try:
            resp = client.get(args.url)
        except ScrapeError as exc:
            print(f"fetch failed: {exc}", file=sys.stderr)
            return 1

    dest.write_text(resp.text, encoding="utf-8")
    print(f"saved {len(resp.text):,} chars -> {dest} (HTTP {resp.status_code})")
    return 0


def _fixture_name(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    slug = (parts.path.strip("/").replace("/", "_") or parts.netloc) + ".html"
    return slug


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "fetch-fixture":
        return _cmd_fetch_fixture(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
