"""Compare candidate model types on the same held-out split.

A one-off diagnostic, not part of the production training path -- ``train.py``
is what actually gets deployed. Run this after a meaningful feature change, or
whenever picking XGBoost again deserves re-justifying against plainer
baselines: a linear regression floor, a random forest, and sklearn's own
gradient-boosting implementation (no extra dependency, comparable to
LightGBM). All four candidates share one preprocessing shape -- ordinal-encode
the categoricals, median-impute the numerics -- so the only thing that varies
is the estimator.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from xgboost import XGBRegressor

from realestate.data.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    FLAG_FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    build_feature_frame,
)
from realestate.model.train import XGB_PARAMS, _metrics

log = logging.getLogger(__name__)

CANDIDATES: dict[str, Any] = {
    "linear_regression": LinearRegression(),
    "random_forest": RandomForestRegressor(
        n_estimators=300, max_depth=12, random_state=42, n_jobs=-1
    ),
    "hist_gradient_boosting": HistGradientBoostingRegressor(max_depth=5, random_state=42),
    "xgboost": XGBRegressor(**XGB_PARAMS),
}


def _pipeline_for(estimator: Any) -> Pipeline:
    encoder = OrdinalEncoder(
        handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-2
    )
    pre = ColumnTransformer(
        [
            ("cat", encoder, CATEGORICAL_FEATURES),
            ("num", SimpleImputer(strategy="median"), NUMERIC_FEATURES + FLAG_FEATURES),
        ],
        verbose_feature_names_out=False,
    )
    pre.set_output(transform="pandas")
    return Pipeline([("pre", pre), ("model", estimator)])


def compare_models(
    df: pd.DataFrame, *, test_size: float = 0.2, seed: int = 42
) -> dict[str, dict[str, float]]:
    """Fit every candidate on the same train split, score on the same holdout."""
    feats = build_feature_frame(df).dropna(subset=[TARGET])
    x = feats[FEATURE_COLUMNS]
    y = feats[TARGET].to_numpy()
    x_tr, x_te, y_tr, y_te = train_test_split(x, y, test_size=test_size, random_state=seed)

    results = {}
    for name, estimator in CANDIDATES.items():
        pipe = _pipeline_for(estimator).fit(x_tr, y_tr)
        results[name] = _metrics(y_te, pipe.predict(x_te))
    return results


def _print_report(results: dict[str, dict[str, float]]) -> None:
    print(f"{'model':<22} {'n':>5} {'MAE EUR':>12} {'median APE':>11} {'MAPE':>7} {'R2(EUR)':>8}")
    for name, m in results.items():
        print(
            f"{name:<22} {m['n']:>5} {m['mae_eur']:>12,.0f} "
            f"{m['median_ape_pct']:>10.1f}% {m['mape_pct']:>6.1f}% {m['r2_eur']:>8.3f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="realestate-compare-models",
        description="Compare candidate model types on the same held-out split.",
    )
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--from-sample",
        action="store_true",
        help="Compare using sample/listings_sample.csv instead of the database.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from realestate.model.train import _load_frame

    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    df = _load_frame(args.from_sample)
    log.info("comparing on %d cleaned rows", len(df))
    results = compare_models(df, test_size=args.test_size, seed=args.seed)
    _print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
