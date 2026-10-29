"""Compare every candidate model type on one held-out split.

The weekly retrain already chooses between these automatically (see
``train.select_model``, which cross-validates on the training split). This is
the manual view: the same ``candidates.CANDIDATES``, each fitted the way
production fits it (location-dropout copies included), graded on a hold-out,
as scraped and as city-only requests -- what the demo page sends by default.
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd
from sklearn.model_selection import train_test_split

from realestate.data.features import FEATURE_COLUMNS, TARGET, build_feature_frame
from realestate.model.candidates import CANDIDATES
from realestate.model.train import LOCATION_DROPOUT, _blank, _fit, _metrics

log = logging.getLogger(__name__)


def compare_models(
    df: pd.DataFrame, *, test_size: float = 0.2, seed: int = 42
) -> dict[str, dict[str, dict[str, float]]]:
    """Fit every candidate on the same train split, score on the same holdout,
    as scraped (``holdout``) and as city-only requests (``holdout_city_only``)."""
    feats = build_feature_frame(df).dropna(subset=[TARGET])
    x = feats[FEATURE_COLUMNS]
    y = feats[TARGET].to_numpy()
    x_tr, x_te, y_tr, y_te = train_test_split(x, y, test_size=test_size, random_state=seed)
    x_te_city_only = _blank(x_te, LOCATION_DROPOUT[-1])

    results = {}
    for name in CANDIDATES:
        pipe = _fit(x_tr, y_tr, name)
        results[name] = {
            "holdout": _metrics(y_te, pipe.predict(x_te)),
            "holdout_city_only": _metrics(y_te, pipe.predict(x_te_city_only)),
        }
    return results


def _print_report(results: dict[str, dict[str, dict[str, float]]]) -> None:
    for split in ("holdout", "holdout_city_only"):
        print()
        print(split)
        print(
            f"{'model':<22} {'n':>5} {'MAE EUR':>12} {'median APE':>11} {'MAPE':>7} {'R2(EUR)':>8}"
        )
        for name, by_split in results.items():
            m = by_split[split]
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
    # Read-only diagnostic: nothing is deployed, so a blocked last scrape doesn't matter.
    df = _load_frame(args.from_sample, allow_blocked_scrape=True)
    log.info("comparing on %d cleaned rows", len(df))
    results = compare_models(df, test_size=args.test_size, seed=args.seed)
    _print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
