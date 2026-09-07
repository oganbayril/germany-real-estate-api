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

- **Scope:** Berlin, Hamburg, Leipzig (München and Köln are configured but not
  yet in the dataset).
- **Target:** the listed **asking** price, not a transaction price.
- **Current model** (`2026-09-05T20-13-24Z`): trained on the bundled
  `sample/listings_sample.csv` — **180 listings**, prices rounded to €1,000 and
  street numbers removed. This is a bootstrap so the API is live; it is replaced
  automatically once the real scraped dataset is large enough
  (`RE_MIN_TRAIN_ROWS`, default 200).

## Performance

5-fold out-of-fold CV and a 20% hold-out, in euro terms (n = 180 total):

| metric | CV (n=144) | hold-out (n=36) |
|---|---|---|
| median abs. % error | 18.6 % | 20.5 % |
| MAPE | 25.6 % | 26.2 % |
| MAE | €136,500 | €126,700 |
| R² (log price) | 0.81 | 0.77 |
| R² (euro price) | 0.73 | 0.64 |

Read: roughly **half of predictions land within ~20 %** of the asking price. The
gap between log-R² and euro-R² is the long right tail — a few large errors on
expensive flats dominate the squared/absolute euro metrics.

Feature importance is led by `rooms`, `city`, and `living_area_sqm`, then the
location columns (`postal_prefix`, `district`, `quarter`).

## Limitations & intended use

- **Asking ≠ sold.** It models what sellers list, which runs above achieved
  prices, especially in a soft market.
- **Small, non-random sample.** 180 rows, 3 cities, skewed toward the
  neighbourhoods that appear first in Immowelt's sitemaps. Not representative of
  the German market.
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
