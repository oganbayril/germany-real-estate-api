# germany-real-estate-api

A German real-estate price predictor, end to end:

```
Immowelt scraper  ->  Postgres  ->  feature pipeline  ->  XGBoost model  ->  FastAPI
```

Portfolio project demonstrating a full ML deployment: periodic data collection,
a trained regression model, and a served prediction API — deployed on a Hetzner
VPS with plain systemd (services + timer), native Postgres.

## Status

Phases 0-1 complete. See phases below.

| Phase | Scope | State |
|------|-------|-------|
| 0 | Package layout, tooling, config | done |
| 1 | Schema, SQLAlchemy models, Alembic, upsert repository | done |
| 2 | Immowelt scraper (sitemap discovery, rate-limited, robots-aware) | done |
| 3 | Cleaning + feature engineering | todo |
| 4 | XGBoost training + eval + artifact registry | todo |
| 5 | FastAPI serving (`/predict`, `/health`, `/stats`) | todo |
| 6 | systemd deployment units + runbook | todo |
| 7 | CI, architecture diagram, sample fixture | todo |

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                       # create .venv, install deps + dev group
cp .env.example .env          # adjust as needed

uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format .          # format

uv run alembic upgrade head        # create / migrate the database
uv run alembic revision --autogenerate -m "..."   # after changing models

uv run realestate-scrape run --cities berlin --max-searches 5   # scrape
uv run realestate-scrape run --dry-run                          # fetch + parse, no writes
uv run realestate-scrape fetch-fixture <url>                    # save HTML for a parser test
uv run realestate-train --help
uv run uvicorn realestate.api.main:app --reload
```

## Data model

| Table | Purpose |
|-------|---------|
| `listings` | current state of each expose, keyed by `expose_id` |
| `listing_price_history` | append-only price observations (first sight + every change) |
| `scrape_runs` | one row per scrape invocation — counts, status, errors |

Migrations live in [alembic/versions/](alembic/versions/); the connection URL is
supplied by `realestate.config` (env `RE_DATABASE_URL`), not `alembic.ini`.

## Scraping policy

The scraper is the primary data source, run periodically via a systemd timer.
It is deliberately low-volume and non-evasive: single-threaded, ~8-15s jittered
delay, honest descriptive User-Agent, respects `robots.txt`, scoped to ~5 cities,
no proxy rotation or browser emulation. Listing URLs come from Immowelt's own
published XML sitemaps rather than from reverse-engineering its internal API.
Raw scraped data and DB dumps are never committed; the repo ships code plus a
small sanitized sample fixture only.

Only search-results pages are fetched, not per-listing detail pages: Immowelt (and
ImmoScout24) guard detail pages with DataDome. Everything stored comes from the
results cards — price, area, rooms, floor, district, postal code, energy class.
`scraper/immoscout.py` is a dormant placeholder (IS24 blocks plain HTTP entirely).

## Layout

```
src/realestate/
  config.py      central settings (env / .env, RE_ prefix)
  db/            SQLAlchemy models, session, upsert repository
  scraper/       httpx client, HTML parsing, pipeline, CLI
  data/          cleaning + feature engineering (shared by training and API)
  model/         training, prediction, artifact registry
  api/           FastAPI app + schemas
tests/           pytest; parser tests run against tests/fixtures/immowelt/
sample/          sanitized CSV sample (committed)
deploy/          systemd units + runbook
```
