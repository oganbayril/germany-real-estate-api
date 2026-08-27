"""Rate-limited HTTP client with robots.txt enforcement. Phase 2."""

from __future__ import annotations


class ScrapeClient:
    """Single-threaded httpx wrapper: jittered delay, honest UA, robots.txt Disallow check."""

    # TODO(phase-2): implement __init__/get with tenacity retry + urllib.robotparser
