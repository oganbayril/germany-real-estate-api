"""API endpoints: /health, /predict, /model, /stats."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from realestate.api import main as api_main
from realestate.db.models import Base, Listing, ScrapeRun, utcnow
from realestate.model import registry
from realestate.model.train import train_model


def _synthetic_df(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    cities = rng.choice(["berlin", "hamburg", "leipzig"], n)
    area = rng.uniform(35, 150, n)
    rates = {"berlin": 6400, "hamburg": 5200, "leipzig": 3100}
    price = area * pd.Series(cities).map(rates).to_numpy() * rng.normal(1, 0.07, n)
    return pd.DataFrame(
        {
            "expose_id": [f"e{i}" for i in range(n)],
            "city": cities,
            "district": rng.choice(["A", "B", "C"], n),
            "quarter": rng.choice(["q1", "q2", "q3"], n),
            "postal_code": rng.choice(["10115", "20095", "04103"], n),
            "address": None,
            "price_eur": price,
            "living_area_sqm": area,
            "rooms": np.clip((area / 30).round(), 1, 5),
            "floor": rng.integers(0, 6, n).astype(float),
            "energy_efficiency_class": rng.choice(["A", "C", "E", None], n),
            "property_type": "apartment",
            "listing_status": "active",
        }
    )


@pytest.fixture(autouse=True)
def _rate_limit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main.limiter, "enabled", False)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(registry, "_models_root", lambda: tmp_path)
    result = train_model(_synthetic_df(), seed=1)
    registry.save(result.pipeline, metrics=result.metrics, metadata=result.metadata)

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed:
        for i, city in enumerate(["berlin"] * 6 + ["hamburg"] * 3 + ["leipzig"] * 2):
            seed.add(
                Listing(
                    expose_id=f"x{i}",
                    city=city,
                    price_eur=300_000 + i * 25_000,
                    living_area_sqm=60 + i,
                    listing_status="active",
                )
            )
        seed.add(
            ScrapeRun(
                status="success",
                finished_at=utcnow(),
                listings_new=11,
                exposes_seen=11,
                cities=["berlin"],
            )
        )
        seed.commit()

    class _Scope:
        def __enter__(self) -> Session:
            self._s = factory()
            return self._s

        def __exit__(self, *exc: object) -> None:
            self._s.close()

    monkeypatch.setattr(api_main, "session_scope", _Scope)

    with TestClient(api_main.app) as c:
        yield c
    engine.dispose()


def test_health_reports_model(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model_version"]


def test_predict_happy_path(client: TestClient) -> None:
    resp = client.post(
        "/predict",
        json={
            "city": "berlin",
            "living_area_sqm": 90,
            "rooms": 3,
            "district": "A",
            "quarter": "q1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 150_000 < body["predicted_price_eur"] < 2_000_000
    assert body["predicted_price_per_sqm_eur"] == pytest.approx(
        body["predicted_price_eur"] / 90, rel=1e-6
    )
    assert body["typical_error_pct"] is not None


def test_predict_validation_error(client: TestClient) -> None:
    resp = client.post("/predict", json={"city": "berlin", "living_area_sqm": 5000})
    assert resp.status_code == 422


def test_predict_rejects_overlong_string(client: TestClient) -> None:
    resp = client.post("/predict", json={"city": "x" * 5000, "living_area_sqm": 70})
    assert resp.status_code == 422


def test_predict_unknown_city_still_predicts(client: TestClient) -> None:
    resp = client.post("/predict", json={"city": "Atlantis", "living_area_sqm": 80})
    assert resp.status_code == 200
    assert resp.json()["predicted_price_eur"] > 0


def test_predict_is_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main.limiter, "enabled", True)
    api_main.limiter.reset()
    payload = {"city": "berlin", "living_area_sqm": 70}
    codes = {client.post("/predict", json=payload).status_code for _ in range(35)}
    assert 429 in codes
    api_main.limiter.reset()


def test_model_endpoint(client: TestClient) -> None:
    body = client.get("/model").json()
    assert body["feature_columns"]
    assert "holdout" in body["metrics"]
    assert set(body["cities"]) <= {"berlin", "hamburg", "leipzig"}


def test_stats_endpoint(client: TestClient) -> None:
    body = client.get("/stats").json()
    assert body["listings_total"] == 11
    assert body["by_city"]["berlin"] == 6
    assert body["price_eur"]["min"] == 300_000
    assert body["last_scrape"]["status"] == "success"


def test_predict_503_without_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_models_root", lambda: tmp_path)  # empty -> no model
    with TestClient(api_main.app) as c:
        assert c.get("/health").json()["model_loaded"] is False
        assert c.post("/predict", json={"city": "berlin", "living_area_sqm": 70}).status_code == 503
