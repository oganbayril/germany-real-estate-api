# Model card — German apartment price predictor

## What it does

Given a few attributes of a German apartment listing, it predicts the **asking
price in euros**. Served at `POST /predict` on
<https://germany-real-estate.duckdns.org>.

- **Type:** gradient-boosted trees (`XGBRegressor`) on `ln(price)`, wrapped in an
  sklearn `Pipeline` with an `OrdinalEncoder` for the categorical location
  columns. Predictions are exponentiated back to euros.
- **Inputs:** `city`, `living_area_sqm` (required); `rooms`, `floor`,
  `postal_code`, `district`, `quarter`, `energy_efficiency_class` (optional).
  Engineered: `area_per_room`, an energy-class ordinal, and missing-value flags.
- **Output:** `predicted_price_eur`, `predicted_price_per_sqm_eur`, the model
  version, and the model's own typical error (`typical_error_pct`).

## Training data

Apartment-for-sale listings from **immowelt.de**, collected by this project's
scraper from public search-results pages (no detail pages — those are behind a
bot wall). Fields therefore come from the results cards only.

- **Scope:** Berlin, Hamburg, München, Köln, Leipzig.
- **Target:** the listed **asking** price, not a transaction price.
- **Current model** (`2026-10-03T04-43-15Z`): trained on **912 real scraped
  listings** across all 5 cities. The very first deployed model (until enough
  data existed) trained on the bundled `sample/listings_sample.csv` (180 rows,
  Berlin only) — that bootstrap path still exists for a fresh deployment with an
  empty database (`RE_MIN_TRAIN_ROWS`, default 200).

## Performance

5-fold out-of-fold CV and a 20% hold-out, in euro terms (n = 912 total):

| metric | CV (n=729) | hold-out (n=183) |
|---|---|---|
| median abs. % error | 15.8 % | 17.8 % |
| MAPE | 21.7 % | 21.0 % |
| MAE | €121,954 | €137,983 |
| R² (log price) | 0.80 | 0.85 |
| R² (euro price) | 0.72 | 0.77 |

Read: roughly **half of predictions land within ~18 %** of the asking price. The
gap between log-R² and euro-R² is the long right tail — a few large errors on
expensive flats dominate the squared/absolute euro metrics.

Feature importance is led by `rooms`, `city`, and `living_area_sqm`, then the
location columns (`postal_prefix`, `district`, `quarter`).

## Model selection

XGBoost was the first thing tried and never actually benchmarked against
alternatives — `realestate-compare-models` (`src/realestate/model/compare.py`)
closes that gap. It fits a linear regression, a random forest, sklearn's
`HistGradientBoostingRegressor`, and `XGBRegressor` on the same train/hold-out
split with identical preprocessing, so only the estimator varies.

First run, at 667 cleaned rows, actually favored the alternative:
`HistGradientBoostingRegressor` edged out `XGBRegressor` on every metric. With
more data the picture changed. Re-run against the live database (912 cleaned
rows, one 20% hold-out):

| model | median APE | MAPE | R² (euro) | MAE |
|---|---|---|---|---|
| linear regression | 24.2 % | 29.3 % | -0.44 | €223,300 |
| random forest | 18.4 % | 22.8 % | 0.700 | €156,150 |
| hist gradient boosting | **16.8 %** | 22.1 % | 0.723 | €145,040 |
| XGBoost (deployed) | 17.6 % | **21.5 %** | **0.794** | **€140,230** |

Both boosted-tree methods still clearly beat linear regression and random
forest — gradient boosting is the right family of model here. (Linear
regression's R² actually went negative: ordinal-encoding four categorical
columns and feeding the result to a linear model doesn't hold up as the
category cardinality grows, which is a reason to not use that encoding for a
linear model, not a data problem.) XGBoost and HistGradientBoosting are now a
genuine toss-up — HistGradientBoosting still wins on median APE, but XGBoost
leads on MAE, R², and MAPE, a reversal from the first run. That reversal is
itself the lesson: the earlier "HistGradientBoosting is better" reading was
mostly sample noise at 667 rows, not a real, stable gap. XGBoost stays in
production. The database is still small for this kind of comparison, so this
isn't a closed question — re-run `realestate-compare-models` again as more
data accumulates, rather than trusting either single snapshot.

## Limitations & intended use

- **Asking ≠ sold.** It models what sellers list, which runs above achieved
  prices, especially in a soft market.
- **Small, non-random sample.** A few hundred rows across 5 cities, skewed toward
  whichever districts and filter combinations the scraper's sitemap-derived
  discovery has sampled so far. Not representative of the German market.
- **Thin features.** No year built, condition, heating type, or amenities — those
  live on detail pages the scraper doesn't fetch. Two identical-on-paper flats in
  very different states get the same prediction.
- **No calibrated uncertainty.** `typical_error_pct` is just the hold-out median
  APE, offered as a rough ± guide, not a prediction interval.
- **Intended use:** a portfolio demonstration of an end-to-end ML system
  (scrape → store → train → serve → deploy). **Not** for valuation, lending, or
  any real financial decision.

## Retraining

`realestate-train` runs weekly on the VPS. It refuses to retrain if the latest
scrape failed or the dataset is below `RE_MIN_TRAIN_ROWS`, and on success
restarts the API so the new artifact (`models/<timestamp>/`) is picked up. Each
artifact ships its own `metrics.json` and `metadata.json`.
