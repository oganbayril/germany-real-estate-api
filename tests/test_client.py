"""ScrapeClient: robots.txt enforcement, block detection, retry on transient errors."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from realestate.scraper.client import (
    BlockedError,
    RobotsDisallowed,
    ScrapeClient,
    ScrapeError,
)

UA = "test-agent/1.0"


def _client(**kw: object) -> ScrapeClient:
    kw.setdefault("delay_min_s", 0)
    kw.setdefault("delay_max_s", 0)
    kw.setdefault("max_retries", 3)
    kw.setdefault("backoff_base_s", 0)
    kw.setdefault("blocked_backoff_s", 0)
    return ScrapeClient(user_agent=UA, **kw)


def test_robots_disallow_blocks_fetch(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://site.test/robots.txt",
        text="User-agent: *\nDisallow: /private/",
    )
    with _client() as client, pytest.raises(RobotsDisallowed):
        client.get("https://site.test/private/x")


def test_robots_allow_permits_fetch(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", text="User-agent: *\nAllow: /")
    httpx_mock.add_response(url="https://site.test/ok", text="hi")
    with _client() as client:
        assert client.get("https://site.test/ok").text == "hi"


def test_missing_robots_is_permissive(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://site.test/ok", text="hi")
    with _client() as client:
        assert client.get("https://site.test/ok").status_code == 200


def test_403_retries_then_raises_blocked(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_response(
        url="https://site.test/x", status_code=403, text="nope", is_reusable=True
    )
    with _client(max_retries=2) as client, pytest.raises(BlockedError):
        client.get("https://site.test/x")
    assert len(httpx_mock.get_requests(url="https://site.test/x")) == 2


def test_403_then_success(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://site.test/x", status_code=403, text="nope")
    httpx_mock.add_response(url="https://site.test/x", status_code=200, text="ok now")
    with _client() as client:
        assert client.get("https://site.test/x").text == "ok now"


def test_challenge_body_raises_blocked(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_response(
        url="https://site.test/x",
        status_code=200,
        html="<html><body>Please enable JavaScript and cookies to continue</body></html>",
        is_reusable=True,
    )
    with _client(max_retries=2) as client, pytest.raises(BlockedError):
        client.get("https://site.test/x")


def test_retries_then_succeeds(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://site.test/x", status_code=503)
    httpx_mock.add_response(url="https://site.test/x", status_code=200, text="finally")
    with _client() as client:
        assert client.get("https://site.test/x").text == "finally"


def test_gives_up_after_max_retries(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://site.test/x", status_code=503, is_reusable=True)
    with _client(max_retries=2) as client, pytest.raises(ScrapeError):
        client.get("https://site.test/x")


def test_transport_error_is_retried(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_exception(httpx.ConnectError("boom"), url="https://site.test/x")
    httpx_mock.add_response(url="https://site.test/x", status_code=200, text="ok")
    with _client() as client:
        assert client.get("https://site.test/x").text == "ok"


def test_allowed_hosts_rejects_off_allowlist_url() -> None:
    with (
        _client(allowed_hosts=frozenset({"site.test"})) as client,
        pytest.raises(ScrapeError, match="off-allowlist"),
    ):
        client.get("http://169.254.169.254/latest/meta-data/")


def test_allowed_hosts_rejects_cross_host_redirect(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://site.test/robots.txt", status_code=404)
    httpx_mock.add_response(
        url="https://site.test/x",
        status_code=302,
        headers={"location": "http://169.254.169.254/"},
    )
    httpx_mock.add_response(url="http://169.254.169.254/", status_code=200, text="secrets")
    with (
        _client(allowed_hosts=frozenset({"site.test"})) as client,
        pytest.raises(ScrapeError, match="off-allowlist"),
    ):
        client.get("https://site.test/x")
