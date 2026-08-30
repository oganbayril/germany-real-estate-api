"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from realestate.model.predict import PricePredictor


def get_predictor(request: Request) -> PricePredictor:
    predictor: PricePredictor | None = getattr(request.app.state, "predictor", None)
    if predictor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="no model is loaded; train one with `realestate-train`",
        )
    return predictor
