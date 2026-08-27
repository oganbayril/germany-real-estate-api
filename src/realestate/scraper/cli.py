"""`realestate-scrape` entrypoint. Phase 2 fills in the real pipeline call."""

from __future__ import annotations

import argparse

from realestate.config import get_settings


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="realestate-scrape",
        description="Scrape ImmoScout24 listings.",
    )
    parser.add_argument(
        "--cities",
        nargs="+",
        default=settings.scrape_cities,
        help="City slugs to scrape (default: from config).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=settings.scrape_max_pages_per_city,
        help="Max search-result pages per city.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse but do not write to the DB.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # TODO(phase-2): from realestate.scraper.pipeline import run_scrape; run_scrape(...)
    print(
        f"[stub] would scrape cities={args.cities} "
        f"max_pages={args.max_pages} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
