"""FastAPI app. Run: `uvicorn realestate.api.main:app`. Phase 5 adds /predict + /stats."""

from __future__ import annotations

from fastapi import FastAPI

from realestate import __version__

app = FastAPI(title="Germany Real-Estate Price API", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# TODO(phase-5): POST /predict, GET /stats, model load on startup
