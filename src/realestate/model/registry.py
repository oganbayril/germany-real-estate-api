"""Versioned model artifacts under Settings.model_dir.

Layout: models/<timestamp>/model.joblib + metrics.json, with a `latest` pointer
resolving the model the API should load. Phase 4.
"""

from __future__ import annotations

# TODO(phase-4): new_version_dir(), latest_version_dir(), save_artifact(), load_artifact()
