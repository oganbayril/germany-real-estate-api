"""Phase 0 smoke tests: package imports, config loads, API health endpoint responds."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from realestate.api.main import app
from realestate.config import Settings


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.scrape_delay_min_s < s.scrape_delay_max_s
    assert "berlin" in s.scrape_cities


def test_cities_from_csv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE_SCRAPE_CITIES", "berlin, hamburg")
    s = Settings(_env_file=None)
    assert s.scrape_cities == ["berlin", "hamburg"]


def test_health() -> None:
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
