"""Tests for :mod:`id_churn_sentinel.core.fetch` — the network seam, tested with no network.

The two transport seams are monkeypatched throughout: `urlopen` (robots.txt) and
`_open_page` (page requests, where redirect hops are recorded). Nothing in this file
resolves a hostname; if it did, the suite would fail on a plane, in an air-gapped runner,
and on the day a state's website is down — which is exactly the day you most want to be
able to run your tests. The autouse `_no_network` fixture makes an unpatched page fetch a
loud failure rather than a quiet socket.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from email.message import Message
from http.client import HTTPMessage
from typing import Any

import pytest

from id_churn_sentinel.core import fetch as fetch_mod
from id_churn_sentinel.core.fetch import (
    ERROR_CLASS_BODY_TOO_LARGE,
    ERROR_CLASS_HTTP_ERROR,
    ERROR_CLASS_NON_HTTPS,
    ERROR_CLASS_ROBOTS_DISALLOWED,
    ERROR_CLASS_UNREACHABLE,
    USER_AGENT,
    FetchResult,
    HttpFetcher,
    RedirectHop,
)

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


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seams default to failing loudly (pages) or permissively (robots), never to a
    socket. A page fetch a test forgot to patch is an AssertionError, not a live request
    to a government server; a robots fetch it forgot to patch behaves as an unreachable
    robots.txt, which the fetcher already treats as permissive."""

    def no_page(
        request: urllib.request.Request, *, timeout: float, recorder: object
    ) -> FakeResponse:
        raise AssertionError(f"test attempted a real page fetch: {request.full_url}")

    def no_urlopen(*_: object, **__: object) -> FakeResponse:
        raise urllib.error.URLError("test attempted a real robots fetch")

    monkeypatch.setattr(fetch_mod, "_open_page", no_page)
    monkeypatch.setattr(urllib.request, "urlopen", no_urlopen)


def serve_page(
    monkeypatch: pytest.MonkeyPatch,
    page: bytes | Exception | FakeResponse = b"<p>hi</p>",
    *,
    hops: tuple[RedirectHop, ...] = (),
) -> list[str]:
    """Patch the page seam: serve `page`, record requested URLs, and replay `hops` into
    the recorder exactly as urllib's redirect handler would while following them."""
    requested: list[str] = []

    def opener(request: urllib.request.Request, *, timeout: float, recorder: Any) -> FakeResponse:
        requested.append(request.full_url)
        recorder.hops.extend(hops)
        if isinstance(page, Exception):
            raise page
        return FakeResponse(page) if isinstance(page, bytes) else page

    monkeypatch.setattr(fetch_mod, "_open_page", opener)
    return requested


def route(
    monkeypatch: pytest.MonkeyPatch,
    *,
    robots: bytes | Exception = b"User-agent: *\nAllow: /\n",
    page: bytes | Exception = b"<p>hi</p>",
) -> list[str]:
    """Serve robots.txt through `urlopen` and pages through the page seam, recording every
    URL requested on either.

    Routing (rather than stubbing `RobotFileParser` out) means these tests exercise the
    *real* stdlib robots parser against real robots.txt bytes — so a mistake in how we feed
    it (`parse(lines)` vs `read()`) is caught here rather than in production against a
    government server that has told us not to crawl.
    """
    requested: list[str] = []

    def robots_opener(request: urllib.request.Request, **_: object) -> FakeResponse:
        url = request.full_url
        requested.append(url)
        if isinstance(robots, Exception):
            raise robots
        return FakeResponse(robots)

    def page_opener(
        request: urllib.request.Request, *, timeout: float, recorder: object
    ) -> FakeResponse:
        requested.append(request.full_url)
        if isinstance(page, Exception):
            raise page
        return FakeResponse(page)

    monkeypatch.setattr(urllib.request, "urlopen", robots_opener)
    monkeypatch.setattr(fetch_mod, "_open_page", page_opener)
    return requested


@pytest.fixture(autouse=True)
def _never_really_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-host crawl spacing calls `time.sleep`. No test should burn wall-clock on it: the
    spacing maths is asserted separately with an injected fake clock, and every other test
    treats the sleep as instantaneous."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


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
    serve_page(monkeypatch, b"<p>hi</p>")
    result = HttpFetcher().fetch(URL)

    assert result.ok
    assert result.status == 200
    assert result.body == b"<p>hi</p>"
    assert result.content_type == "text/html"
    assert result.error is None


def test_a_successful_fetch_carries_its_complete_evidence(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """DATA-04: what the network did is recorded on the result, not discarded — a direct
    answer has an empty chain, its own URL as the final URL, and its byte accounting."""
    serve_page(monkeypatch, b"<p>hi</p>")
    result = HttpFetcher().fetch(URL)

    assert result.final_url == URL
    assert result.redirect_chain == ()
    assert result.bytes_received == len(b"<p>hi</p>")
    assert result.byte_limit == fetch_mod._MAX_BODY_BYTES
    assert result.truncated is False
    assert result.error_class == ""


def test_a_redirect_chain_is_recorded_hop_by_hop(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """The chain is evidence: a registry URL that 301s to a marketing page is one of the
    named registry-rot risks, and the retained record — not a lucky re-fetch — is what has
    to show which page actually answered."""
    hops = (
        RedirectHop(status=301, url="https://www.dps.texas.gov/moved"),
        RedirectHop(status=302, url="https://www.dps.texas.gov/final"),
    )
    serve_page(monkeypatch, b"<p>hi</p>", hops=hops)
    result = HttpFetcher().fetch(URL)

    assert result.ok
    assert result.redirect_chain == hops
    assert result.final_url == "https://www.dps.texas.gov/final"
    assert result.url == URL  # the requested URL is never overwritten by the journey


def test_an_http_error_partway_down_a_chain_keeps_the_hops(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """A failure's journey is evidence too: the hop that 404s is the hop a maintainer has
    to look at, and it is not the URL the registry names."""
    hops = (RedirectHop(status=301, url="https://www.dps.texas.gov/moved"),)

    def redirect_then_404(
        request: urllib.request.Request, *, timeout: float, recorder: Any
    ) -> FakeResponse:
        recorder.hops.extend(hops)
        raise urllib.error.HTTPError(hops[0].url, 404, "gone", Message(), None)

    monkeypatch.setattr(fetch_mod, "_open_page", redirect_then_404)
    result = HttpFetcher().fetch(URL)

    assert not result.ok
    assert result.redirect_chain == hops
    assert result.final_url == hops[0].url
    assert result.error_class == ERROR_CLASS_HTTP_ERROR


def test_the_redirect_recorder_records_exactly_what_the_stdlib_follows() -> None:
    """The recorder delegates the redirect decision to urllib and writes down only what
    urllib actually followed — same hop, same rewritten request, nothing invented."""
    recorder = fetch_mod._RedirectRecorder()
    request = urllib.request.Request(  # noqa: S310 — a constructed request; never opened
        URL, headers={"User-Agent": USER_AGENT}
    )

    new_request = recorder.redirect_request(
        request, None, 301, "Moved Permanently", HTTPMessage(), "https://www.dps.texas.gov/new"
    )

    assert new_request is not None
    assert recorder.hops == [RedirectHop(status=301, url="https://www.dps.texas.gov/new")]
    assert recorder.hops[0].url == new_request.full_url


def test_the_user_agent_names_the_project_and_a_contact(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """Polite crawling is structural. A government server operator who wants to know who is
    hitting them weekly can read it straight out of the log line."""
    seen: dict[str, Any] = {}

    def capture(
        request: urllib.request.Request, *, timeout: float, recorder: object
    ) -> FakeResponse:
        seen["ua"] = request.get_header("User-agent")
        return FakeResponse(b"ok")

    monkeypatch.setattr(fetch_mod, "_open_page", capture)
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

    # fp=None deliberately: urllib's HTTPError extends addinfourl, which extends
    # tempfile._TemporaryFileWrapper, and handing it a BytesIO leaves a closer that
    # raises from __del__ (which `filterwarnings = ["error"]` correctly turns into a
    # failure). We only need the error, not a readable body.
    serve_page(monkeypatch, urllib.error.HTTPError(URL, code, "nope", Message(), None))
    result = HttpFetcher().fetch(URL)

    assert not result.ok
    assert result.body == b""  # no bytes: there is nothing for a hasher to reach
    assert result.status == code
    assert f"HTTP {code}" in (result.error or "")
    assert result.error_class == ERROR_CLASS_HTTP_ERROR


def test_a_transport_error_is_a_failure(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    serve_page(monkeypatch, urllib.error.URLError("connection reset"))
    result = HttpFetcher().fetch(URL)

    assert not result.ok
    assert "unreachable" in (result.error or "")
    assert result.error_class == ERROR_CLASS_UNREACHABLE


def test_a_timeout_is_a_failure(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    serve_page(monkeypatch, TimeoutError("timed out"))
    result = HttpFetcher().fetch(URL)

    assert not result.ok
    assert result.error_class == ERROR_CLASS_UNREACHABLE


def test_a_non_https_url_is_refused_without_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt to the registry's braces. A watcher for a population that includes people in
    hostile jurisdictions does not fetch over plaintext, ever — and refusing *before* the
    request means no DNS lookup and no TCP connection happen either. The autouse
    `_no_network` fixture turns any request this test would make into a loud failure."""
    result = HttpFetcher().fetch("http://insecure.example.gov/p")

    assert not result.ok
    assert "non-https" in (result.error or "")
    assert result.error_class == ERROR_CLASS_NON_HTTPS
    assert result.byte_limit is None  # no request was made, so no bound was applied


def test_an_oversized_body_is_refused(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    serve_page(monkeypatch, b"x" * 5000)
    result = HttpFetcher(max_bytes=100).fetch(URL)

    assert not result.ok
    assert "exceeds" in (result.error or "")


def test_an_oversized_body_records_its_truncation_evidence(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """DATA-04: the refusal is recorded as what it is — a truncated read against a stated
    bound — not as a generic failure a later reader cannot distinguish from an outage."""
    serve_page(monkeypatch, b"x" * 5000)
    result = HttpFetcher(max_bytes=100).fetch(URL)

    assert result.error_class == ERROR_CLASS_BODY_TOO_LARGE
    assert result.truncated is True
    assert result.byte_limit == 100
    assert result.bytes_received == 101  # the bounded read stopped at limit + 1
    assert result.body == b""  # evidence of the refusal, never the refused bytes


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
    assert result.error_class == ERROR_CLASS_ROBOTS_DISALLOWED
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
    robots ourselves precisely so every socket is bounded — assert the timeout is passed on
    both seams."""
    seen: dict[str, Any] = {}

    def robots_opener(request: urllib.request.Request, **kwargs: object) -> FakeResponse:
        seen[request.full_url] = kwargs.get("timeout")
        return FakeResponse(b"User-agent: *\nAllow: /\n")

    def page_opener(
        request: urllib.request.Request, *, timeout: float, recorder: object
    ) -> FakeResponse:
        seen[request.full_url] = timeout
        return FakeResponse(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", robots_opener)
    monkeypatch.setattr(fetch_mod, "_open_page", page_opener)
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


def test_a_constructed_failure_derives_a_stable_error_class() -> None:
    """`failure()` classifies when the caller does not: a failure carrying an HTTP status
    is an HTTP error, anything else is transport-level unreachability — so a stub or an
    older caller still produces a classified failure the store will accept."""
    assert FetchResult.failure(URL, "HTTP 404", status=404).error_class == ERROR_CLASS_HTTP_ERROR
    assert FetchResult.failure(URL, "boom").error_class == ERROR_CLASS_UNREACHABLE
    assert (
        FetchResult.failure(URL, "no", error_class=ERROR_CLASS_ROBOTS_DISALLOWED).error_class
        == ERROR_CLASS_ROBOTS_DISALLOWED
    )


class FakeClock:
    """A deterministic monotonic clock for the crawl-spacing tests: `sleep` advances the
    clock and records how long it slept, so spacing is asserted with no wall-clock wait."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_consecutive_page_requests_to_one_host_are_spaced(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """The feature: two pages on the same government server are not fetched back-to-back.
    The first request never waits; the second waits the full interval."""
    serve_page(monkeypatch, b"ok")
    clock = FakeClock()
    fetcher = HttpFetcher(min_host_interval=2.0, sleep=clock.sleep, monotonic=clock.monotonic)

    assert fetcher.fetch(URL).ok
    assert fetcher.fetch(URL + "/two").ok

    assert clock.slept == [2.0]


def test_requests_to_different_hosts_are_not_spaced(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """Spacing is per host: hitting a different server does not wait, so the run is not
    serialised into one slow queue across unrelated hosts."""
    serve_page(monkeypatch, b"ok")
    clock = FakeClock()
    fetcher = HttpFetcher(min_host_interval=2.0, sleep=clock.sleep, monotonic=clock.monotonic)

    fetcher.fetch(URL)
    fetcher.fetch("https://other.example.gov/p")

    assert clock.slept == []


def test_spacing_waits_only_the_remaining_gap(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """Time already elapsed counts against the interval — the wait is the remainder, not a
    fresh full interval every time."""
    serve_page(monkeypatch, b"ok")
    clock = FakeClock()
    fetcher = HttpFetcher(min_host_interval=2.0, sleep=clock.sleep, monotonic=clock.monotonic)

    fetcher.fetch(URL)
    clock.advance(0.5)  # half a second passes before the next same-host crawl
    fetcher.fetch(URL + "/two")

    assert clock.slept == [1.5]


def test_a_slow_host_is_not_penalised_by_spacing(
    monkeypatch: pytest.MonkeyPatch, no_robots: None
) -> None:
    """The interval bounds the *rate*, not the host's answer time: a server that already
    spent longer than the interval answering is not made to wait again."""
    clock = FakeClock()

    def slow_open(
        request: urllib.request.Request, *, timeout: float, recorder: object
    ) -> FakeResponse:
        clock.advance(3.0)  # this host takes 3s to answer, more than the 2s interval
        return FakeResponse(b"ok")

    monkeypatch.setattr(fetch_mod, "_open_page", slow_open)
    fetcher = HttpFetcher(min_host_interval=2.0, sleep=clock.sleep, monotonic=clock.monotonic)

    fetcher.fetch(URL)
    fetcher.fetch(URL + "/two")

    assert clock.slept == []


def test_a_refused_request_never_sleeps(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    """Spacing sits after the guards: a non-https URL is refused without a request, so it
    must not consume a spacing wait either."""
    serve_page(monkeypatch, b"ok")
    clock = FakeClock()
    fetcher = HttpFetcher(min_host_interval=2.0, sleep=clock.sleep, monotonic=clock.monotonic)

    fetcher.fetch(URL)  # first real request to the host
    assert not fetcher.fetch("http://insecure.example.gov/p").ok

    assert clock.slept == []


def test_spacing_can_be_disabled(monkeypatch: pytest.MonkeyPatch, no_robots: None) -> None:
    """A zero interval turns spacing off — for a self-hosted target or a test that wants the
    old back-to-back behaviour."""
    serve_page(monkeypatch, b"ok")
    clock = FakeClock()
    fetcher = HttpFetcher(min_host_interval=0, sleep=clock.sleep, monotonic=clock.monotonic)

    fetcher.fetch(URL)
    fetcher.fetch(URL + "/two")

    assert clock.slept == []
