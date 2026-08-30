"""Load the trained artifact and score listings.

The API holds one long-lived ``PricePredictor``. Feature construction goes
through the exact same ``build_feature_frame`` used in training, so a prediction
request only needs to supply the raw listing fields.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from realestate.data.features import FEATURE_COLUMNS, build_feature_frame, target_to_price
from realestate.model import registry


@dataclass(frozen=True)
class Prediction:
    price_eur: float
    price_per_sqm_eur: float | None
    model_version: str


class PricePredictor:
    def __init__(self, artifact: registry.Artifact) -> None:
        self._artifact = artifact

    @classmethod
    def load(cls, version: str | None = None) -> PricePredictor:
        return cls(registry.load(version))

    @property
    def version(self) -> str:
        return self._artifact.version

    @property
    def metrics(self) -> dict:
        return self._artifact.metrics

    @property
    def metadata(self) -> dict:
        return self._artifact.metadata

    def predict_prices(self, df: pd.DataFrame) -> np.ndarray:
        feats = build_feature_frame(df)[FEATURE_COLUMNS]
        return target_to_price(self._artifact.pipeline.predict(feats))

    def predict_one(self, record: dict) -> Prediction:
        price = float(self.predict_prices(pd.DataFrame([record]))[0])
        area = record.get("living_area_sqm")
        ppsqm = round(price / area, 2) if isinstance(area, int | float) and area else None
        return Prediction(
            price_eur=round(price, 2),
            price_per_sqm_eur=ppsqm,
            model_version=self.version,
        )
