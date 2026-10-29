"""The model types the weekly retrain chooses between.

Every candidate gets the same inputs and the same treatment of missing location
detail: categoricals are ordinal-encoded with unknown *and* missing mapped to
NaN, so a model that handles NaN natively (all the tree models here) learns one
"missing" branch from the location-dropout rows. An unseen postal code then
prices like no postal code. Linear regression can't take NaN, so it imputes --
it is the floor the tree models should clearly beat, not a serious contender.

Only the estimator varies. Adding a candidate is one entry in ``CANDIDATES``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from xgboost import XGBRegressor

from realestate.data.features import CATEGORICAL_FEATURES

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


@dataclass(frozen=True)
class Candidate:
    name: str  # stable id, stored in artifact metadata
    label: str  # human-readable, for reports and the technical model card
    make_estimator: Callable[[], Any]
    needs_imputation: bool = False

    def build(self) -> Pipeline:
        encoder = OrdinalEncoder(
            handle_unknown="use_encoded_value",
            unknown_value=np.nan,
            encoded_missing_value=np.nan,
        )
        pre = ColumnTransformer(
            [("cat", encoder, CATEGORICAL_FEATURES)],
            remainder="passthrough",
            verbose_feature_names_out=False,
        )
        pre.set_output(transform="pandas")
        steps: list[tuple[str, Any]] = [("pre", pre)]
        if self.needs_imputation:
            steps.append(("impute", SimpleImputer(strategy="median")))
        steps.append(("model", self.make_estimator()))
        return Pipeline(steps)


CANDIDATES: dict[str, Candidate] = {
    c.name: c
    for c in [
        Candidate("xgboost", "XGBoost", lambda: XGBRegressor(**XGB_PARAMS)),
        Candidate(
            "hist_gradient_boosting",
            "HistGradientBoosting",
            lambda: HistGradientBoostingRegressor(max_depth=5, random_state=42),
        ),
        Candidate(
            "random_forest",
            "Random forest",
            lambda: RandomForestRegressor(
                n_estimators=300, max_depth=12, random_state=42, n_jobs=-1
            ),
        ),
        Candidate(
            "linear_regression", "Linear regression", LinearRegression, needs_imputation=True
        ),
    ]
}

# What artifacts trained before automatic selection were (metadata had no
# "model_type"), and what a fresh deployment starts from.
DEFAULT_MODEL = "xgboost"
