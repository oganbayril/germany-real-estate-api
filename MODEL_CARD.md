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
- **Current model** (`2026-09-13T23-37-44Z`): trained on **645 real scraped
  listings** across all 5 cities. The very first deployed model (until enough
  data existed) trained on the bundled `sample/listings_sample.csv` (180 rows,
  Berlin only) — that bootstrap path still exists for a fresh deployment with an
  empty database (`RE_MIN_TRAIN_ROWS`, default 200).

## Performance

5-fold out-of-fold CV and a 20% hold-out, in euro terms (n = 645 total):

| metric | CV (n=516) | hold-out (n=129) |
|---|---|---|
| median abs. % error | 16.9 % | 15.5 % |
| MAPE | 22.9 % | 19.7 % |
| MAE | €141,500 | €122,600 |
| R² (log price) | 0.78 | 0.83 |
| R² (euro price) | 0.61 | 0.82 |

Read: roughly **half of predictions land within ~16 %** of the asking price. The
gap between log-R² and euro-R² is the long right tail — a few large errors on
expensive flats dominate the squared/absolute euro metrics.

Feature importance is led by `rooms`, `city`, and `living_area_sqm`, then the
location columns (`postal_prefix`, `district`, `quarter`).

## Model selection

XGBoost was the first thing tried and never actually benchmarked against
alternatives — `realestate-compare-models` (`src/realestate/model/compare.py`)
closes that gap. It fits a linear regression, a random forest, sklearn's
`HistGradientBoostingRegressor`, and `XGBRegressor` on the same train/hold-out
split with identical preprocessing, so only the estimator varies. Run against
the live database (667 cleaned rows, one 20% hold-out):

| model | median APE | MAPE | R² (euro) | MAE |
|---|---|---|---|---|
| linear regression | 23.1 % | 27.0 % | 0.684 | €156,000 |
| random forest | 19.5 % | 22.5 % | 0.717 | €133,700 |
| hist gradient boosting | **13.7 %** | **20.0 %** | **0.806** | **€112,500** |
| XGBoost (deployed) | 15.2 % | 20.3 % | 0.783 | €117,700 |

Both boosted-tree methods clearly beat linear regression and random forest,
confirming gradient boosting is the right family of model for this
data. `HistGradientBoostingRegressor` edges out `XGBRegressor` on every metric
here — the gap is consistent, if modest. XGBoost stays in production for now:
switching estimator families is a bigger change than a single comparison run
justifies on its own, and neither model's hyperparameters have been tuned, so
the ranking could plausibly flip. Revisit if the gap holds up as more data
comes in, or after an actual tuning pass on both.

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
