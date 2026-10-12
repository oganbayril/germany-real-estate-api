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
- **Size:** grows with every scrape. The model retrains weekly on everything
  collected so far; the live row count is on
  [/model-card](https://germany-real-estate.duckdns.org/model-card) (plain
  language) and [/model](https://germany-real-estate.duckdns.org/model) (JSON).
- **Bootstrap:** a fresh deployment with an empty database (fewer than
  `RE_MIN_TRAIN_ROWS`, default 200) trains on the bundled
  `sample/listings_sample.csv` (180 rows, Berlin only) until enough real data
  exists.

## Performance

Each training run reports two evaluations, in euro terms (predictions are
exponentiated back from log price):

- **CV:** 5-fold out-of-fold predictions on the 80% train split.
- **Hold-out:** a 20% split the model never sees while fitting.

Metrics: median absolute % error, MAPE, MAE, RMSE, median absolute error, and
R² on both log and euro scale. They are saved with every artifact
(`models/<timestamp>/metrics.json`) and served live at
[/model](https://germany-real-estate.duckdns.org/model), and the hold-out
median % error is what `/predict` returns as `typical_error_pct`. They aren't
copied here because the model retrains every week and this file would go stale.

A recent snapshot, to show the scale: model `2026-10-10T04-03-47Z`, 1,197 rows
(957 train / 240 hold-out), hold-out median APE 15.5 %, MAPE 21.2 %,
R² (log) 0.84, R² (euro) 0.81. Read: roughly **half of predictions land within
~15 %** of the asking price. The gap between log-R² and euro-R² is the long right
tail: a few large errors on expensive flats dominate the squared and absolute
euro metrics.

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
- **Small, non-random sample.** About a thousand rows across 5 cities (Berlin
  alone is roughly half), skewed toward
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
