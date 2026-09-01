# germany-real-estate-api

A German real-estate price predictor, end to end:

```
Immowelt scraper  ->  Postgres  ->  feature pipeline  ->  XGBoost model  ->  FastAPI
```

Portfolio project demonstrating a full ML deployment: periodic data collection,
a trained regression model, and a served prediction API — deployed on a Hetzner
VPS with plain systemd (services + timer), native Postgres.

## Status

| Phase | Scope | State |
|------|-------|-------|
| 0 | Package layout, tooling, config | done |
| 1 | Schema, SQLAlchemy models, Alembic, upsert repository | done |
| 2 | Immowelt scraper (sitemap discovery, rate-limited, robots-aware) | done |
| 3 | Cleaning + feature engineering | done |
| 4 | XGBoost training + eval + artifact registry | done |
| 5 | FastAPI serving (`/predict`, `/model`, `/stats`, `/health`) | done |
| 6 | systemd units, Caddy, backups, run-summary emails | done |
| 7 | CI, architecture diagram | todo |

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

## Feature pipeline

`data/clean.py` loads listings and drops the unmodellable (no price / no area),
clips outliers (price, area, €/m², rooms, floor), and de-dupes relistings.

`data/features.py` is a **pure transform** — no fitting, no DB — so training and
the prediction API run identical feature code. It produces a fixed column
contract (`FEATURE_COLUMNS`): numeric (`living_area_sqm`, `rooms`, `floor`,
`area_per_room`, `energy_class_ordinal`), missing-value flags, and category-dtype
columns (`city`, `district`, `postal_prefix`). Target is `price_log = ln(price)`;
`target_to_price` inverts it. ## Model

`realestate-train` loads listings (or `--from-sample`), cleans, builds features,
and fits an sklearn `Pipeline`: `OrdinalEncoder` on the categorical location
columns (ordinal, not one-hot — cardinality is high and XGBoost splits ordinal
codes fine) → `XGBRegressor` on `price_log`. XGBoost handles NaNs natively, so
there is no imputation step.

Metrics are reported in euros (predictions exponentiated back from log): 5-fold
out-of-fold CV on the train split, plus a held-out test split. The saved artifact
is refit on every row.

`model/registry.py` versions artifacts under `models/<UTC-timestamp>/`
(`model.joblib` + `metrics.json` + `metadata.json`), with a `latest.txt` pointer.
`model/predict.py`'s `PricePredictor` loads the latest and scores a listing
through the same `build_feature_frame`.

Current model (≈560 Berlin/Leipzig/Hamburg listings, card-only features):
holdout median error ≈20%, R²(log) ≈0.82. Thin by design — more data from the
scheduled scraper and light tuning are the obvious next gains.

## API

`uvicorn realestate.api.main:app` — the model is loaded once at startup onto
`app.state`; if none is trained yet the app still serves and `/predict` returns
503.

| Route | Purpose |
|-------|---------|
| `POST /predict` | listing attributes → predicted price, €/m², model version, the model's typical % error |
| `GET /model` | version, metrics, feature list, training row count — a model card over HTTP |
| `GET /stats` | listing counts by city, price percentiles, last scrape — straight from the DB |
| `GET /health` | liveness + whether a model is loaded |

```bash
curl -s localhost:8000/predict -H 'content-type: application/json' \
  -d '{"city":"berlin","living_area_sqm":75,"rooms":3,"district":"Pankow","quarter":"Prenzlauer Berg"}'
```

## Deployment

Single Hetzner VPS, plain systemd (no Docker). `bash deploy/setup.sh` on a fresh
Debian/Ubuntu box provisions Caddy (auto-HTTPS), native Postgres, the API service,
and timers for scraping (Mon/Thu), retraining (Sat, restarts the API on success),
and nightly `pg_dump`. `deploy/update.sh` ships a new `main`. Full runbook,
schedules, and the Caddy-over-nginx rationale: **[deploy/README.md](deploy/README.md)**.

Hardening: API runs sandboxed (`ProtectSystem=strict`, scoped `ReadWritePaths`,
syscall filter) because the model artifact is `joblib`/pickle; `slowapi` rate
limits; `.env` is `chmod 600` via `EnvironmentFile=`; Postgres is localhost-only
with a non-superuser role; the scraper is pinned to one host (no SSRF).

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
