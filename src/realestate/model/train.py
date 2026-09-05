"""Train the price model.

Pipeline: ordinal-encode the (high-cardinality) categorical location columns,
pass the numeric + flag columns straight through, fit an XGBoost regressor on
``price_log``. XGBoost handles the NaNs in the numeric columns natively, so there
is no imputation step.

Metrics are reported in euro terms (predictions are exponentiated back from log
space): out-of-fold cross-validation on the training split for a stable estimate,
plus a held-out test split. The artifact that gets saved is refit on *all* rows.
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.metrics import r2_score
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from xgboost import XGBRegressor

from realestate.data.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    TARGET,
    build_feature_frame,
    target_to_price,
)
from realestate.model import registry

log = logging.getLogger(__name__)

XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 400,
    "max_depth": 5,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 3,
    "reg_lambda": 1.0,
    "objective": "reg:squarederror",
    "n_jobs": -1,
    "random_state": 42,
}


@dataclass
class TrainResult:
    pipeline: Pipeline
    metrics: dict[str, Any]
    metadata: dict[str, Any]


def build_pipeline() -> Pipeline:
    encoder = OrdinalEncoder(
        handle_unknown="use_encoded_value",
        unknown_value=-1,
        encoded_missing_value=-2,
    )
    pre = ColumnTransformer(
        [("cat", encoder, CATEGORICAL_FEATURES)],
        remainder="passthrough",
        verbose_feature_names_out=False,
    )
    pre.set_output(transform="pandas")
    return Pipeline([("pre", pre), ("model", XGBRegressor(**XGB_PARAMS))])


def _metrics(y_true_log: np.ndarray, y_pred_log: np.ndarray) -> dict[str, float]:
    true_eur = target_to_price(y_true_log)
    pred_eur = target_to_price(y_pred_log)
    err = pred_eur - true_eur
    ape = np.abs(err) / true_eur
    return {
        "n": int(len(y_true_log)),
        "mae_eur": float(np.mean(np.abs(err))),
        "rmse_eur": float(np.sqrt(np.mean(err**2))),
        "median_ae_eur": float(np.median(np.abs(err))),
        "mape_pct": float(np.mean(ape) * 100),
        "median_ape_pct": float(np.median(ape) * 100),
        "r2_log": float(r2_score(y_true_log, y_pred_log)),
        "r2_eur": float(r2_score(true_eur, pred_eur)),
    }


def train_model(
    df: pd.DataFrame,
    *,
    test_size: float = 0.2,
    seed: int = 42,
    cv_folds: int = 5,
    min_rows: int = 50,
) -> TrainResult:
    feats = build_feature_frame(df).dropna(subset=[TARGET])
    if len(feats) < max(min_rows, 50):
        raise ValueError(f"need >={max(min_rows, 50)} usable rows to train, got {len(feats)}")

    x = feats[FEATURE_COLUMNS]
    y = feats[TARGET].to_numpy()
    x_tr, x_te, y_tr, y_te = train_test_split(x, y, test_size=test_size, random_state=seed)

    folds = min(cv_folds, max(2, len(x_tr) // 20))
    cv = KFold(n_splits=folds, shuffle=True, random_state=seed)
    oof_pred = cross_val_predict(build_pipeline(), x_tr, y_tr, cv=cv)
    cv_metrics = _metrics(y_tr, oof_pred)

    holdout_pipe = build_pipeline().fit(x_tr, y_tr)
    holdout_metrics = _metrics(y_te, holdout_pipe.predict(x_te))

    # the deployed artifact is refit on every row
    final_pipe = build_pipeline().fit(x, y)

    metadata = {
        "trained_at": datetime.now(UTC).isoformat(),
        "feature_columns": FEATURE_COLUMNS,
        "target": TARGET,
        "n_rows_total": int(len(feats)),
        "n_rows_train": int(len(x_tr)),
        "n_rows_test": int(len(x_te)),
        "cv_folds": folds,
        "test_size": test_size,
        "seed": seed,
        "xgb_params": XGB_PARAMS,
        "cities": sorted(map(str, pd.Series(df.get("city")).dropna().unique())),
        "versions": {
            "python": platform.python_version(),
            "xgboost": xgboost.__version__,
            "scikit_learn": sklearn.__version__,
        },
    }
    return TrainResult(final_pipe, {"cv": cv_metrics, "holdout": holdout_metrics}, metadata)


class TrainingBlocked(RuntimeError):
    """A safety guard refused to (re)train."""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _load_frame(from_sample: bool) -> pd.DataFrame:
    from realestate.data.clean import clean

    if from_sample:
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "sample" / "listings_sample.csv"
        return clean(pd.read_csv(path))

    from sqlalchemy import select

    from realestate.data.clean import load_listings
    from realestate.db.models import ScrapeRun
    from realestate.db.session import session_scope

    with session_scope() as session:
        last = session.scalars(
            select(ScrapeRun)
            .where(ScrapeRun.finished_at.is_not(None))
            .order_by(ScrapeRun.id.desc())
        ).first()
        if last is not None and last.status not in ("success", "partial"):
            raise TrainingBlocked(
                f"latest scrape run #{last.id} ended {last.status!r}; refusing to retrain on it"
            )
        return clean(load_listings(session))


def _print_report(result: TrainResult, saved_to: str) -> None:
    for split in ("cv", "holdout"):
        m = result.metrics[split]
        print(
            f"{split:>8}  n={m['n']:<5} "
            f"MAE EUR {m['mae_eur']:>10,.0f}  "
            f"median APE {m['median_ape_pct']:>5.1f}%  "
            f"MAPE {m['mape_pct']:>5.1f}%  "
            f"R2(EUR) {m['r2_eur']:.3f}"
        )
    print(f"saved -> {saved_to}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="realestate-train", description="Train the price model.")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--from-sample",
        action="store_true",
        help="Train from sample/listings_sample.csv instead of the database.",
    )
    parser.add_argument(
        "--no-save", action="store_true", help="Train and report but do not persist."
    )
    parser.add_argument(
        "--email", action="store_true", help="Email a one-line summary (VPS timer use)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from realestate.config import get_settings
    from realestate.notify import send_email

    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    def _notify(subject: str, body: str) -> None:
        if args.email:
            send_email(subject, body, settings=settings)

    # the bundled sample is a small deliberate bootstrap set; only the DB path
    # is held to the production row floor.
    min_rows = 50 if args.from_sample else settings.min_train_rows
    try:
        df = _load_frame(args.from_sample)
        log.info("training on %d cleaned rows", len(df))
        result = train_model(df, test_size=args.test_size, seed=args.seed, min_rows=min_rows)
    except (ValueError, TrainingBlocked) as exc:
        print(f"training aborted: {exc}", file=sys.stderr)
        _notify("retrain skipped", str(exc))
        return 1

    saved_to = "(not saved)"
    if not args.no_save:
        saved_to = str(
            registry.save(result.pipeline, metrics=result.metrics, metadata=result.metadata)
        )
    _print_report(result, saved_to)
    hold = result.metrics["holdout"]
    _notify(
        "retrained",
        f"rows={result.metadata['n_rows_total']} "
        f"median APE {hold['median_ape_pct']:.1f}% "
        f"MAE EUR {hold['mae_eur']:,.0f} R2(log) {hold['r2_log']:.3f}\n{saved_to}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
