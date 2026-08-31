"""Rate-limited, robots-aware HTTP client for scraping.

Deliberately simple and non-evasive: one request at a time, a jittered delay
between requests, an honest descriptive User-Agent, and robots.txt enforcement.
No proxy rotation, no header spoofing, no browser emulation.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.robotparser
from types import TracebackType
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger(__name__)

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# Substrings that, in an HTML body, indicate a bot-check / challenge page rather
# than real content.
_BLOCK_MARKERS = (
    "datadome",
    "captcha",
    "px-captcha",
    "access denied",
    "are you a human",
    "bitte bestätigen sie",
    "unusual traffic",
    "enable javascript and cookies to continue",
)


class ScrapeError(RuntimeError):
    """A request could not be completed."""


class RobotsDisallowed(ScrapeError):
    """The target URL is disallowed by the site's robots.txt."""


class BlockedError(ScrapeError):
    """The server responded with something that looks like bot-blocking."""


def _looks_blocked(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return False
    body = response.text[:20_000].lower()
    return any(marker in body for marker in _BLOCK_MARKERS)


def _site_root(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class ScrapeClient:
    def __init__(
        self,
        *,
        user_agent: str,
        delay_min_s: float = 5.0,
        delay_max_s: float = 10.0,
        timeout_s: float = 30.0,
        respect_robots: bool = True,
        max_retries: int = 3,
        backoff_base_s: float = 2.0,
        blocked_backoff_s: float = 60.0,
        allowed_hosts: frozenset[str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._delay = (delay_min_s, delay_max_s)
        self._respect_robots = respect_robots
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._blocked_backoff_s = blocked_backoff_s
        # When set, every request URL and every redirect hop must be on one of
        # these hosts -- stops a redirect (from the site or a MITM) sending the
        # scraper to a link-local / cloud-metadata address.
        self._allowed_hosts = allowed_hosts
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
            },
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request_at = 0.0

    # -- lifecycle -------------------------------------------------------------
    def __enter__(self) -> ScrapeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- robots -------------------------------------------------------------
    def _robots_parser(self, url: str) -> urllib.robotparser.RobotFileParser:
        root = _site_root(url)
        parser = self._robots.get(root)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self._client.get(urljoin(root, "/robots.txt"))
                lines = resp.text.splitlines() if resp.status_code == 200 else []
            except httpx.HTTPError:
                lines = []
            parser.parse(lines)
            self._robots[root] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        return self._robots_parser(url).can_fetch(self._user_agent, url)

    # -- throttle -------------------------------------------------------------
    def _throttle(self) -> None:
        target = random.uniform(*self._delay)
        elapsed = time.monotonic() - self._last_request_at
        if 0 < elapsed < target:
            time.sleep(target - elapsed)
        elif self._last_request_at == 0.0:
            pass  # first request, no wait
        self._last_request_at = time.monotonic()

    def _check_host(self, url: str) -> None:
        if self._allowed_hosts is not None and urlsplit(url).netloc not in self._allowed_hosts:
            raise ScrapeError(f"refusing to fetch off-allowlist host: {url}")

    # -- fetch -------------------------------------------------------------
    def get(self, url: str, *, referer: str | None = None) -> httpx.Response:
        self._check_host(url)
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        self._throttle()

        headers = {"Referer": referer} if referer else None
        backoff = self._backoff_base_s
        blocked = False
        last_error: BaseException | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._client.get(url, headers=headers)
            except httpx.TransportError as exc:
                last_error = exc
                log.warning("transport error on %s (attempt %d): %s", url, attempt, exc)
            else:
                for hop in (*resp.history, resp):
                    self._check_host(str(hop.url))
                if resp.status_code == 403 or _looks_blocked(resp):
                    # Immowelt rate-limits with a 403 challenge; a longer pause
                    # usually clears it, so retry hard before giving up.
                    blocked = True
                    last_error = BlockedError(f"{url} (status {resp.status_code})")
                    backoff = max(backoff, self._blocked_backoff_s)
                    log.warning("blocked on %s (try %d); pausing %.0fs", url, attempt, backoff)
                elif resp.status_code in _RETRY_STATUS:
                    last_error = ScrapeError(f"HTTP {resp.status_code} for {url}")
                    log.warning("retryable %d on %s (try %d)", resp.status_code, url, attempt)
                else:
                    resp.raise_for_status()
                    return resp

            if attempt < self._max_retries:
                time.sleep(backoff)
                backoff = min(backoff * 2, self._blocked_backoff_s)

        if blocked:
            raise BlockedError(f"still blocked on {url} after {self._max_retries} attempts")
        raise ScrapeError(f"gave up on {url} after {self._max_retries} attempts") from last_error
