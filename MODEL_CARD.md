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
median % error is what `/predict` returns as `typical_error_pct` (the
city-only one, below, for requests without location detail). They aren't
copied here because the model retrains every week and this file would go stale.

A recent snapshot, to show the scale: model `2026-10-12T14-57-02Z`, 1,197 rows
(957 train / 240 hold-out), hold-out median APE 16.7 %, MAPE 22.3 %,
R² (log) 0.83, R² (euro) 0.77; city-only median APE 20.1 %. Read: roughly
**half of predictions land within ~17 %** of the asking price when the
district or postal code is known, ~20 % from the city alone. The gap between log-R² and euro-R² is the long right
tail: a few large errors on expensive flats dominate the squared and absolute
euro metrics.

Hold-out is graded twice: on the listings as scraped, and again as **city-only
requests** (`holdout_city_only` at `/model`: the same flats with district,
quarter and postal code removed). The second is what the demo page sends unless
a district is picked, and it is less precise (20.1 % median APE vs 16.7 % for
model `2026-10-12T14-57-02Z`). `/predict` returns that city-only figure as
`typical_error_pct` when a request has no location detail.

Why it's graded separately: every scraped listing has a district and postal
code, so a model fit on them alone never sees a request without them. Up to
`2026-10-10T04-03-47Z` that is exactly what happened: given only a city, the
model fell back to roughly one price everywhere (about 3,500-4,400 EUR/m2 for
a 70 m2 flat in München and Leipzig alike) and a city-only hold-out median APE
of ~27 %. Each fit now also trains on copies of its rows with location detail
blanked (postal code; then quarter; then district), and an unseen location
value encodes the same as a missing one, so it falls back to the coarser level
it does know. Cost: hold-out error on fully located listings rose from ~16 % to
~17.5 %, and differences between districts within a city come out flatter than
the raw data shows.

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

### Re-run with location dropout (2026-10-12, 1,197 rows)

After location dropout was added (see above), the comparison was re-run with
every candidate trained the same way and graded twice, on listings as scraped
and as city-only requests. A 240-row hold-out moves by 1-2 points between
splits, so these are means over three splits (seeds 42, 1, 7):

| model | median APE (located) | median APE (city only) | MAE | R² (euro) |
|---|---|---|---|---|
| linear regression | 24.5 % | 23.9 % | €175,700 | -0.33 |
| random forest | 20.2 % | 20.4 % | €138,400 | 0.719 |
| hist gradient boosting | 16.9 % | 20.1 % | €122,500 | 0.747 |
| XGBoost (shared preprocessing) | 17.6 % | 20.2 % | €119,400 | 0.778 |
| **XGBoost (production pipeline)** | **16.7 %** | **19.8 %** | **€115,700** | **0.782** |

The production pipeline is best or tied-best on every column, but its lead
over HistGradientBoosting (0.2-0.3 points of median APE) is smaller than the
split-to-split noise, so this is still "both boosted-tree models are about
equally good", not a clear win. Linear regression and random forest barely
change between the two grades because they barely use location at all.
Re-run with `realestate-compare-models`.

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
scrape failed or the dataset is below `RE_MIN_TRAIN_ROWS`. It also won't replace
the live model with one whose hold-out median error is more than
`RE_MAX_ERROR_REGRESSION_PTS` (default 3) percentage points worse; the margin
absorbs the week-to-week wobble from the hold-out split changing as data grows,
so it only catches a real regression (e.g. a bad scrape). On success it
restarts the API so the new artifact (`models/<timestamp>/`) is picked up. Each
artifact ships its own `metrics.json` and `metadata.json`.
