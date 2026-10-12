"""``realestate-scrape`` entrypoint.

Subcommands:
  run             run the scrape pipeline
  fetch-fixture   fetch one URL through the polite client and save the HTML
                  (for building parser tests / probing whether we get blocked)
  block-report    one line per recent run: pacing, and where the first block hit
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
    run.add_argument(
        "--email", action="store_true", help="Email a one-line summary (VPS timer use)."
    )

    br = sub.add_parser("block-report", help="Show where blocks hit in recent runs.")
    br.add_argument("--last", type=int, default=20, help="How many recent runs (default 20).")

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
    summary = (
        f"pages={stats.pages_fetched} exposes={stats.exposes_seen} "
        f"new={stats.listings_new} updated={stats.listings_updated} "
        f"price_changes={stats.price_changes} errors={len(stats.errors)}"
    )
    if stats.first_block:
        summary += "\n" + _describe_block(stats.first_block)
    print(summary)
    for err in stats.errors[:10]:
        print(f"  ! {err}", file=sys.stderr)

    if args.email:
        from realestate.notify import send_email

        subject = "scrape had errors" if stats.errors else "scrape ok"
        body = summary + ("\n\n" + "\n".join(stats.errors[:20]) if stats.errors else "")
        send_email(subject, body)
    return 0


def _describe_block(block: dict) -> str:
    what = f"HTTP {block['status']}" if block.get("status") else "no response"
    if block.get("marker"):
        what += f" ({block['marker']!r} in body)"
    return (
        f"first block: request #{block['request_n']} ({block['kind']} {block['path']}), "
        f"{block['minutes_in']} min in, after {block['ok_requests_before']} OK requests; {what}"
    )


def _cmd_block_report(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from realestate.db.models import ScrapeRun
    from realestate.db.session import session_scope

    # plain column rows, not ORM objects, so nothing lazy-loads after the session closes
    columns = (
        ScrapeRun.id,
        ScrapeRun.started_at,
        ScrapeRun.status,
        ScrapeRun.pages_fetched,
        ScrapeRun.diagnostics,
    )
    with session_scope() as session:
        runs = list(
            reversed(
                session.execute(
                    select(*columns).order_by(ScrapeRun.id.desc()).limit(args.last + 1)
                ).all()
            )
        )

    print(
        f"{'run':>4}  {'started (UTC)':<16}  {'gap h':>5}  {'delay s':<8}  {'status':<8}  "
        f"{'pages':>5}  {'ok reqs':>7}  {'block @ min':>11}  block"
    )
    # one extra row is fetched so the oldest shown run still gets its gap
    for prev, run in zip([None, *runs], runs, strict=False):
        if prev is None and len(runs) > args.last:
            continue
        gap = ""
        if prev is not None and prev.started_at and run.started_at:
            gap = f"{(run.started_at - prev.started_at).total_seconds() / 3600:.0f}"
        diag = run.diagnostics or {}
        delay = "-".join(f"{d:g}" for d in diag.get("delay_s", [])) or "?"
        block = diag.get("first_block")
        ok = at = what = ""
        if block:
            ok, at = str(block["ok_requests_before"]), str(block["minutes_in"])
            what = f"{block.get('status') or 'err'} {block.get('marker') or ''}".strip()
        elif diag:
            ok, what = str(sum(1 for e in diag["requests"] if e.get("status") == 200)), "none"
        started = run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "?"
        print(
            f"{run.id:>4}  {started:<16}  {gap:>5}  {delay:<8}  {run.status:<8}  "
            f"{run.pages_fetched:>5}  {ok:>7}  {at:>11}  {what}"
        )
    print("\n(runs before diagnostics existed show '?' for delay and blank block columns)")
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
    if args.command == "block-report":
        return _cmd_block_report(args)
    if args.command == "fetch-fixture":
        return _cmd_fetch_fixture(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
