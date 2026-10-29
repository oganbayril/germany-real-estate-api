"""Train the price model.

Pipeline: ordinal-encode the (high-cardinality) categorical location columns,
pass the numeric + flag columns straight through, fit a regressor on
``price_log``. Which regressor is chosen each run from ``candidates.CANDIDATES``
(see ``select_model``): every candidate is cross-validated on the training split
only, ranked by city-only error first, and the live model's type is kept unless
another beats it by a clear margin -- so the deployed type doesn't flip on noise.
The hold-out split is never used for choosing, so its metrics stay honest.

Every scraped listing has a district and postal code, but a request often has
neither (the demo page has no postal-code field). So every fit also sees copies
of its rows with location detail removed, coarsest last -- see
``_with_location_dropout`` -- and an unseen location value is treated the same
as a missing one: both fall back to what the model knows at the coarser level.

Metrics are reported in euro terms (predictions are exponentiated back from log
space): out-of-fold cross-validation on the training split for a stable estimate,
plus a held-out test split, graded twice: as scraped, and as a city-only request.
The artifact that gets saved is refit on *all* rows.
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
from sklearn.metrics import r2_score
from sklearn.model_selection import KFold, train_test_split
from sklearn.pipeline import Pipeline

from realestate.data.features import (
    FEATURE_COLUMNS,
    TARGET,
    build_feature_frame,
    target_to_price,
)
from realestate.model import registry
from realestate.model.candidates import CANDIDATES, DEFAULT_MODEL, XGB_PARAMS

__all__ = ["XGB_PARAMS", "build_pipeline", "select_model", "train_model"]

log = logging.getLogger(__name__)


@dataclass
class TrainResult:
    pipeline: Pipeline
    metrics: dict[str, Any]
    metadata: dict[str, Any]


# Location detail a request may leave out, finest first. Each entry is one extra
# copy of the training rows with those columns blanked.
LOCATION_DROPOUT: list[list[str]] = [
    ["postal_prefix"],
    ["postal_prefix", "quarter"],
    ["postal_prefix", "quarter", "district"],
]


def build_pipeline(model_type: str = DEFAULT_MODEL) -> Pipeline:
    return CANDIDATES[model_type].build()


def _with_location_dropout(x: pd.DataFrame, y: np.ndarray) -> tuple[pd.DataFrame, np.ndarray]:
    """The rows as scraped plus one copy per ``LOCATION_DROPOUT`` level."""
    copies = [x] + [_blank(x, cols) for cols in LOCATION_DROPOUT]
    return pd.concat(copies, ignore_index=True), np.tile(y, len(copies))


def _blank(x: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = x.copy()
    for col in cols:
        out[col] = out[col].where(pd.Series(False, index=out.index))  # keeps the dtype
    return out


def _fit(x: pd.DataFrame, y: np.ndarray, model_type: str = DEFAULT_MODEL) -> Pipeline:
    return build_pipeline(model_type).fit(*_with_location_dropout(x, y))


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


@dataclass
class Selection:
    chosen: str
    incumbent: str | None
    switched: bool
    reason: str
    # per candidate: CV metrics as scraped ("cv") and as city-only requests
    scores: dict[str, dict[str, dict[str, float]]]

    def as_dict(self, margin_pts: float) -> dict[str, Any]:
        return {
            "rule": "lowest CV city-only median APE, then as-scraped; keep the "
            f"incumbent unless beaten by >= {margin_pts:g} pts",
            "chosen": self.chosen,
            "incumbent": self.incumbent,
            "switched": self.switched,
            "reason": self.reason,
            "candidates": {
                name: {
                    "cv_median_ape_pct": m["cv"]["median_ape_pct"],
                    "cv_city_only_median_ape_pct": m["cv_city_only"]["median_ape_pct"],
                    "cv_mape_pct": m["cv"]["mape_pct"],
                    "cv_r2_eur": m["cv"]["r2_eur"],
                }
                for name, m in self.scores.items()
            },
        }


def _cv_metrics(
    x: pd.DataFrame, y: np.ndarray, model_type: str, cv: KFold
) -> dict[str, dict[str, float]]:
    # Dropout copies are made inside each fold, never before splitting: a blanked
    # copy of a validation row in the training folds would leak its price.
    oof = np.empty(len(y))
    oof_city_only = np.empty(len(y))
    for fit_idx, val_idx in cv.split(x):
        pipe = _fit(x.iloc[fit_idx], y[fit_idx], model_type)
        val = x.iloc[val_idx]
        oof[val_idx] = pipe.predict(val)
        oof_city_only[val_idx] = pipe.predict(_blank(val, LOCATION_DROPOUT[-1]))
    return {"cv": _metrics(y, oof), "cv_city_only": _metrics(y, oof_city_only)}


def select_model(
    x: pd.DataFrame,
    y: np.ndarray,
    *,
    candidates: list[str],
    incumbent: str | None,
    margin_pts: float,
    cv: KFold,
) -> Selection:
    """Cross-validate every candidate on the same folds and pick one.

    Ranked by city-only median % error (the demo's default request), then by
    as-scraped error. The incumbent -- the live model's type -- is kept unless
    the best candidate beats its city-only error by at least ``margin_pts``.
    """
    scores = {name: _cv_metrics(x, y, name, cv) for name in candidates}

    def key(name: str) -> tuple[float, float]:
        m = scores[name]
        return (m["cv_city_only"]["median_ape_pct"], m["cv"]["median_ape_pct"])

    best = min(candidates, key=key)
    label = CANDIDATES[best].label
    if incumbent not in scores:
        return Selection(best, incumbent, False, f"{label} scored best", scores)
    if best == incumbent:
        return Selection(best, incumbent, False, f"{label} scored best; no change", scores)

    gain = key(incumbent)[0] - key(best)[0]
    old_label = CANDIDATES[incumbent].label
    if gain < margin_pts:
        reason = (
            f"{label} beat {old_label} by only {gain:.1f} pts city-only "
            f"(< {margin_pts:g}); keeping {old_label}"
        )
        return Selection(incumbent, incumbent, False, reason, scores)
    reason = f"switched {old_label} -> {label}: {gain:.1f} pts better city-only"
    return Selection(best, incumbent, True, reason, scores)


def train_model(
    df: pd.DataFrame,
    *,
    test_size: float = 0.2,
    seed: int = 42,
    cv_folds: int = 5,
    min_rows: int = 50,
    candidates: list[str] | None = None,
    incumbent: str | None = None,
    switch_margin_pts: float = 1.0,
) -> TrainResult:
    """Choose, train and grade one model.

    ``candidates`` defaults to just the default type (fast -- what tests want);
    the CLI passes every type so the weekly retrain chooses between them.
    """
    candidates = list(candidates or [DEFAULT_MODEL])
    unknown = set(candidates) - set(CANDIDATES)
    if unknown:
        raise ValueError(f"unknown model type(s) {sorted(unknown)}; known: {sorted(CANDIDATES)}")

    feats = build_feature_frame(df).dropna(subset=[TARGET])
    if len(feats) < max(min_rows, 50):
        raise ValueError(f"need >={max(min_rows, 50)} usable rows to train, got {len(feats)}")

    x = feats[FEATURE_COLUMNS]
    y = feats[TARGET].to_numpy()
    x_tr, x_te, y_tr, y_te = train_test_split(x, y, test_size=test_size, random_state=seed)

    folds = min(cv_folds, max(2, len(x_tr) // 20))
    cv = KFold(n_splits=folds, shuffle=True, random_state=seed)
    selection = select_model(
        x_tr, y_tr, candidates=candidates, incumbent=incumbent, margin_pts=switch_margin_pts, cv=cv
    )
    chosen = selection.chosen
    log.info("model selection: %s", selection.reason)

    holdout_pipe = _fit(x_tr, y_tr, chosen)
    holdout_metrics = _metrics(y_te, holdout_pipe.predict(x_te))
    # What the demo page sends by default: city, size, rooms -- no location detail.
    city_only = _blank(x_te, LOCATION_DROPOUT[-1])
    city_only_metrics = _metrics(y_te, holdout_pipe.predict(city_only))

    # the deployed artifact is refit on every row
    final_pipe = _fit(x, y, chosen)

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
        "model_type": chosen,
        "model_label": CANDIDATES[chosen].label,
        "model_params": final_pipe.named_steps["model"].get_params(),
        "selection": selection.as_dict(switch_margin_pts),
        "location_dropout": LOCATION_DROPOUT,
        "cities": sorted(map(str, pd.Series(df.get("city")).dropna().unique())),
        # rows per city the model actually learned from; the API only offers a
        # city once it has enough of them
        "city_rows": {str(k): int(v) for k, v in feats["city"].value_counts().items()},
        "versions": {
            "python": platform.python_version(),
            "xgboost": xgboost.__version__,
            "scikit_learn": sklearn.__version__,
        },
    }
    scores = selection.scores[chosen]
    metrics = {
        "cv": scores["cv"],
        "cv_city_only": scores["cv_city_only"],
        "holdout": holdout_metrics,
        "holdout_city_only": city_only_metrics,
    }
    return TrainResult(final_pipe, metrics, metadata)


class TrainingBlocked(RuntimeError):
    """A safety guard refused to (re)train."""


def check_not_worse(new_metrics: dict[str, Any], *, max_regression_pts: float) -> None:
    """Refuse to replace the live model with one that grades clearly worse.

    Compares hold-out median % error, as scraped and city-only, against the
    current ``latest`` artifact.
    Skipped when there is no current model, or when it was the sample bootstrap
    (180 Berlin-only rows -- not a fair yardstick for a model on real data).
    """
    current = registry.latest_version()
    if current is None:
        return
    meta = registry.read_metadata(current)
    if meta.get("source") == "sample":
        return
    live = registry.read_metrics(current)
    # graded both as scraped and as a city-only request (artifacts from before
    # city-only grading only have the first)
    for split, what in (("holdout", "hold-out"), ("holdout_city_only", "city-only")):
        old = live.get(split, {}).get("median_ape_pct")
        new = new_metrics.get(split, {}).get("median_ape_pct")
        if old is not None and new is not None and new > old + max_regression_pts:
            raise TrainingBlocked(
                f"new model's {what} median error {new:.1f}% is more than "
                f"{max_regression_pts:g} pts worse than live model {current} ({old:.1f}%); "
                "keeping the live model (rerun with --force to deploy anyway)"
            )


def live_model_type() -> str | None:
    """The live artifact's model type; artifacts from before automatic
    selection were always the default type."""
    current = registry.latest_version()
    if current is None:
        return None
    return registry.read_metadata(current).get("model_type", DEFAULT_MODEL)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _load_frame(from_sample: bool, *, allow_blocked_scrape: bool = False) -> pd.DataFrame:
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
            if allow_blocked_scrape:
                log.warning(
                    "latest scrape run #%d ended %r; training anyway (--allow-blocked-scrape)",
                    last.id,
                    last.status,
                )
                return clean(load_listings(session))
            raise TrainingBlocked(
                f"latest scrape run #{last.id} ended {last.status!r}; refusing to retrain on it"
            )
        return clean(load_listings(session))


def _selection_table(selection: dict[str, Any]) -> str:
    lines = [f"{'model':<22} {'city-only':>10} {'as scraped':>11} {'R2(EUR)':>8}"]
    ranked = sorted(
        selection["candidates"].items(),
        key=lambda kv: (kv[1]["cv_city_only_median_ape_pct"], kv[1]["cv_median_ape_pct"]),
    )
    for name, m in ranked:
        mark = " <- deployed" if name == selection["chosen"] else ""
        lines.append(
            f"{CANDIDATES[name].label:<22} {m['cv_city_only_median_ape_pct']:>9.1f}% "
            f"{m['cv_median_ape_pct']:>10.1f}% {m['cv_r2_eur']:>8.3f}{mark}"
        )
    return "\n".join(lines)


def _print_report(result: TrainResult, saved_to: str) -> None:
    selection = result.metadata["selection"]
    print("candidates (CV median abs. % error on the training split):")
    print(_selection_table(selection))
    print(selection["reason"])
    for split in ("cv", "holdout", "holdout_city_only"):
        m = result.metrics[split]
        print(
            f"{split:>17}  n={m['n']:<5} "
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
    parser.add_argument(
        "--allow-blocked-scrape",
        action="store_true",
        help="Train even if the latest scrape run was blocked/failed (manual runs only: "
        "e.g. a code fix to ship on the data already in the database).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Deploy even if the new model grades worse than the live one.",
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
        df = _load_frame(args.from_sample, allow_blocked_scrape=args.allow_blocked_scrape)
        log.info("training on %d cleaned rows", len(df))
        result = train_model(
            df,
            test_size=args.test_size,
            seed=args.seed,
            min_rows=min_rows,
            candidates=settings.train_candidates,
            incumbent=live_model_type(),
            switch_margin_pts=settings.train_switch_margin_pts,
        )
        result.metadata["source"] = "sample" if args.from_sample else "database"
        if not args.no_save and not args.force:
            check_not_worse(result.metrics, max_regression_pts=settings.max_error_regression_pts)
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
    city = result.metrics["holdout_city_only"]
    selection = result.metadata["selection"]
    _notify(
        "retrained, switched model type" if selection["switched"] else "retrained",
        f"{result.metadata['model_label']} on rows={result.metadata['n_rows_total']}: "
        f"hold-out median APE {hold['median_ape_pct']:.1f}% "
        f"(city-only {city['median_ape_pct']:.1f}%), "
        f"MAE EUR {hold['mae_eur']:,.0f} R2(log) {hold['r2_log']:.3f}\n"
        f"{selection['reason']}\n\n{_selection_table(selection)}\n\n{saved_to}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
