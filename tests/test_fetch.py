"""Tests for :mod:`id_churn_sentinel.core.fetch` — the network seam, tested with no network.

`urlopen` is monkeypatched throughout. Nothing in this file resolves a hostname; if it
did, the suite would fail on a plane, in an air-gapped runner, and on the day a state's
website is down — which is exactly the day you most want to be able to run your tests.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from id_churn_sentinel.core import fetch as fetch_mod
from id_churn_sentinel.core.fetch import USER_AGENT, FetchResult, HttpFetcher

URL = "https://www.dps.texas.gov/section/driver-license"


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, content_type: str = "text/html") -> None:
        self._body = body
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, amount: int | None = None) -> bytes:
        return self._body if amount is None else self._body[:amount]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def route(
    monkeypatch: pytest.MonkeyPatch,
    *,
    robots: bytes | Exception = b"User-agent: *\nAllow: /\n",
    page: bytes | Exception = b"<p>hi</p>",
) -> list[str]:
    """Monkeypatch `urlopen` with a router that serves robots.txt and page URLs separately,
    and record every URL requested.

    Routing (rather than stubbing `RobotFileParser` out) means these tests exercise the
    *real* stdlib robots parser against real robots.txt bytes — so a mistake in how we feed
    it (`parse(lines)` vs `read()`) is caught here rather than in production against a
    government server that has told us not to crawl.
    """
    requested: list[str] = []

    def opener(request: urllib.request.Request, **_: object) -> FakeResponse:
        url = request.full_url
        requested.append(url)
        target = robots if url.endswith("/robots.txt") else page
        if isinstance(target, Exception):
            raise target
        return FakeResponse(target)

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    return requested


@pytest.fixture
def no_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """For tests that are not about robots: a permissive robots.txt, served for real."""

    class AllowAll:
        def parse(self, lines: list[str]) -> None: ...
        def can_fetch(self, ua: str, url: str) -> bool:
            return True

    monkeypatch.setattr(fetch_mod, "RobotFileParser", AllowAll)


def test_successful_fetch_returns_body_status_and_type(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"<p>hi</p>"))
    result = HttpFetcher().fetch(URL)

    assert result.ok
    assert result.status == 200
    assert result.body == b"<p>hi</p>"
    assert result.content_type == "text/html"
    assert result.error is None


def test_the_user_agent_names_the_project_and_a_contact(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """Polite crawling is structural. A government server operator who wants to know who is
    hitting them weekly can read it straight out of the log line."""
    seen: dict[str, Any] = {}

    def capture(request: urllib.request.Request, **_: object) -> FakeResponse:
        seen["ua"] = request.get_header("User-agent")
        return FakeResponse(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    HttpFetcher().fetch(URL)

    assert seen["ua"] == USER_AGENT
    assert "id-churn-sentinel" in seen["ua"]
    assert "github.com/ChelseaKR" in seen["ua"]


@pytest.mark.parametrize("code", [403, 404, 429, 500, 503])
def test_an_http_error_is_a_failure_not_an_empty_page(
    monkeypatch: pytest.MonkeyPatch, no_robots: None, code: int
) -> None:
    """The rule, at its source. Hashing a 404 body would report "the page changed" the day
    a state stands up a WAF — and a WAF is not a policy change."""

    def raise_http(*_: object, **__: object) -> FakeResponse:
        # fp=None deliberately: urllib's HTTPError extends addinfourl, which extends
        # tempfile._TemporaryFileWrapper, and handing it a BytesIO leaves a closer that
        # raises from __del__ (which `filterwarnings = ["error"]` correctly turns into a
        # failure). We only need the error, not a readable body.
        raise urllib.error.HTTPError(URL, code, "nope", Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", raise_http)
    result = HttpFetcher().fetch(URL)

    assert not result.ok
    assert result.body == b""  # no bytes: there is nothing for a hasher to reach
    assert result.status == code
    assert f"HTTP {code}" in (result.error or "")


def test_a_transport_error_is_a_failure(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    def raise_url(*_: object, **__: object) -> FakeResponse:
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url)
    result = HttpFetcher().fetch(URL)

    assert not result.ok
    assert "unreachable" in (result.error or "")


def test_a_timeout_is_a_failure(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    def raise_timeout(*_: object, **__: object) -> FakeResponse:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
    assert not HttpFetcher().fetch(URL).ok


def test_a_non_https_url_is_refused_without_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt to the registry's braces. A watcher for a population that includes people in
    hostile jurisdictions does not fetch over plaintext, ever — and refusing *before* the
    request means no DNS lookup and no TCP connection happen either."""

    def explode(*_: object, **__: object) -> FakeResponse:
        raise AssertionError("no request may be made for a non-https URL")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    result = HttpFetcher().fetch("http://insecure.example.gov/p")

    assert not result.ok
    assert "non-https" in (result.error or "")


def test_an_oversized_body_is_refused(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"x" * 5000))
    result = HttpFetcher(max_bytes=100).fetch(URL)

    assert not result.ok
    assert "exceeds" in (result.error or "")


def test_robots_disallow_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A robots.txt that loads and disallows us is honoured without appeal — parsed by the
    real stdlib parser from real robots.txt bytes. These are government servers funded by
    the people this tool serves."""
    requested = route(
        monkeypatch,
        robots=b"User-agent: *\nDisallow: /section/\n",
        page=AssertionError("must not fetch a robots-disallowed path"),
    )

    result = HttpFetcher().fetch(URL)  # URL is /section/driver-license

    assert not result.ok
    assert "robots.txt disallows" in (result.error or "")
    assert requested == ["https://www.dps.texas.gov/robots.txt"]  # the page was never fetched


def test_a_robots_allowed_path_is_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half: a robots.txt that disallows a *different* path does not block us."""
    route(monkeypatch, robots=b"User-agent: *\nDisallow: /admin/\n")

    assert HttpFetcher().fetch(URL).ok


def test_a_missing_robots_is_treated_as_permissive(monkeypatch: pytest.MonkeyPatch) -> None:
    """The posture every mainstream crawler takes: a 404 robots.txt does not mean
    "disallow", it means "no rules published"."""
    route(monkeypatch, robots=urllib.error.HTTPError(URL, 404, "nope", Message(), None))

    assert HttpFetcher().fetch(URL).ok


def test_an_unreachable_robots_is_treated_as_permissive(monkeypatch: pytest.MonkeyPatch) -> None:
    route(monkeypatch, robots=urllib.error.URLError("connection reset"))

    assert HttpFetcher().fetch(URL).ok


def test_the_robots_fetch_is_bounded_by_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RobotFileParser.read()` calls urlopen with NO timeout, which would hang this
    unattended weekly job forever against a server that accepts and never answers. We fetch
    robots ourselves precisely so every socket is bounded — assert the timeout is passed."""
    seen: dict[str, Any] = {}

    def opener(request: urllib.request.Request, **kwargs: object) -> FakeResponse:
        seen[request.full_url] = kwargs.get("timeout")
        return FakeResponse(b"User-agent: *\nAllow: /\n")

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    HttpFetcher(timeout=7.0).fetch(URL)

    assert seen["https://www.dps.texas.gov/robots.txt"] == 7.0
    assert seen[URL] == 7.0


def test_robots_is_fetched_once_per_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cached per host: a 24-source registry must not mean 24 robots.txt fetches from the
    same server."""
    requested = route(monkeypatch)

    fetcher = HttpFetcher()
    fetcher.fetch(URL)
    fetcher.fetch(URL + "/other")
    fetcher.fetch("https://other.example.gov/p")

    robots_hits = [u for u in requested if u.endswith("/robots.txt")]
    assert robots_hits == [
        "https://www.dps.texas.gov/robots.txt",
        "https://other.example.gov/robots.txt",
    ]  # one per host, not one per URL


def test_robots_can_be_disabled_for_a_self_hosted_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = route(monkeypatch)

    assert HttpFetcher(respect_robots=False).fetch(URL).ok
    assert requested == [URL]  # robots.txt was never even requested


def test_failure_results_carry_no_body() -> None:
    """The type-level half of the rule: a failed result has no bytes, so there is no code
    path in which an outage can produce a change record."""
    result = FetchResult.failure(URL, "boom")

    assert not result.ok
    assert result.body == b""
    assert result.content_type is None
    assert result.error == "boom"
