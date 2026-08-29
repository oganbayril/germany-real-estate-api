"""Versioned model artifacts on the local filesystem.

Layout under ``Settings.model_dir``::

    models/
      2026-08-28T17-40-05Z/
        model.joblib      # the fitted sklearn Pipeline (encoder + regressor)
        metrics.json      # hold-out + CV metrics
        metadata.json     # feature columns, row counts, library versions, timestamp
      latest.txt          # name of the version the API should load

A plain ``latest.txt`` pointer is used instead of a symlink so it works the same
on Windows and Linux.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
from sklearn.pipeline import Pipeline

from realestate.config import get_settings

_LATEST = "latest.txt"
_MODEL_FILE = "model.joblib"
_METRICS_FILE = "metrics.json"
_METADATA_FILE = "metadata.json"


@dataclass(frozen=True)
class Artifact:
    version: str
    path: Path
    pipeline: Pipeline
    metrics: dict[str, Any]
    metadata: dict[str, Any]


def _models_root() -> Path:
    root = get_settings().model_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_version() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def save(
    pipeline: Pipeline,
    *,
    metrics: dict[str, Any],
    metadata: dict[str, Any],
    version: str | None = None,
    make_latest: bool = True,
) -> Path:
    version = version or new_version()
    dest = _models_root() / version
    dest.mkdir(parents=True, exist_ok=True)

    joblib.dump(pipeline, dest / _MODEL_FILE)
    (dest / _METRICS_FILE).write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    (dest / _METADATA_FILE).write_text(
        json.dumps({**metadata, "version": version}, indent=2, default=str), encoding="utf-8"
    )
    if make_latest:
        (_models_root() / _LATEST).write_text(version, encoding="utf-8")
    return dest


def list_versions() -> list[str]:
    root = _models_root()
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / _MODEL_FILE).exists())


def latest_version() -> str | None:
    pointer = _models_root() / _LATEST
    if pointer.exists():
        name = pointer.read_text(encoding="utf-8").strip()
        if (_models_root() / name / _MODEL_FILE).exists():
            return name
    versions = list_versions()
    return versions[-1] if versions else None


def load(version: str | None = None) -> Artifact:
    version = version or latest_version()
    if version is None:
        raise FileNotFoundError("no trained model found; run `realestate-train` first")
    path = _models_root() / version
    if not (path / _MODEL_FILE).exists():
        raise FileNotFoundError(f"model version {version!r} not found under {_models_root()}")
    return Artifact(
        version=version,
        path=path,
        pipeline=joblib.load(path / _MODEL_FILE),
        metrics=_read_json(path / _METRICS_FILE),
        metadata=_read_json(path / _METADATA_FILE),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
