"""The network seam — the *only* place this package touches the outside world.

Everything downstream of here (detection, diffing, review, publication) consumes a
:class:`FetchResult` and has no idea whether it came from a socket or a fixture. That is
deliberate and it is a test property, not a style preference: **the entire suite runs with
no network**, because `watch()` takes a `Fetcher` and the tests hand it a dict.

Two rules live here, and they are the difference between a watcher and a nuisance:

**A fetch failure is never drift.** This is the single most important line of discipline
inherited from `trans-docs-navigator/scripts/source-watch.ts` ("Fetch failures are
reported but never recorded as drift — a bot-block or outage is not a content change").
A 503, a TLS hiccup, a WAF that decides today we look like a bot: none of these are a
state changing its birth-certificate policy. :class:`FetchResult` therefore separates
`ok` from `changed` at the type level — a failed result carries no body to hash, so
there is no code path in which an outage can produce a change record.

**Politeness is structural, not aspirational.** `HttpFetcher` reads robots.txt before it
reads a page, declines to fetch what robots disallows, sends a descriptive User-Agent that
names the project and its contact URL, and is driven at a weekly cadence. These are
government servers funded by the people this tool serves; a watcher that hammers them is
taking from the commons it claims to protect.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

__all__ = [
    "USER_AGENT",
    "FetchResult",
    "Fetcher",
    "HttpFetcher",
]

USER_AGENT = (
    "id-churn-sentinel/0.1 (+https://github.com/ChelseaKR/id-churn-sentinel; "
    "weekly change detection over official ID-document pages)"
)

# Bounded, but not *tight*. This was 20s, and 20s was wrong: travel.state.gov — which
# serves the passport sex-marker page, the single highest-churn and highest-value source in
# the registry — was measured on 2026-07-13 answering in 3s, 17s and 44s on three
# consecutive requests. At a 20s timeout it therefore reported as `unreachable` perhaps half
# the time, purely because a government CDN is slow.
#
# That is not a harmless false alarm. An unreachable source holds its old baseline, so a
# real edit to that page could sit undetected behind a timeout — a wrong "no change", which
# is the safety failure this repo is organised around. And with the M3 escalation now in
# place, a chronically slow host would eventually escalate to `possibly_removed` on nothing
# but latency.
#
# The point of the bound is that an unattended weekly cron job must never hang forever
# against a server that accepts a connection and never answers. 45s satisfies that
# completely. Being *bounded* is the safety property; being impatient never was.
_TIMEOUT_SECONDS = 45.0
_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_ROBOTS_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class FetchResult:
    """The outcome of one fetch.

    `ok=True` means we hold bytes we can hash. `ok=False` means we hold *nothing* — the
    body is empty and `error` says why — and the caller's only correct response is to
    carry the previous hash forward. There is no third state, and no way to construct a
    "failed but here's a body anyway" result that could be mistaken for content.
    """

    url: str
    ok: bool
    status: int | None
    content_type: str | None
    body: bytes
    fetched_at: datetime
    error: str | None = None

    @classmethod
    def failure(cls, url: str, error: str, status: int | None = None) -> FetchResult:
        return cls(
            url=url,
            ok=False,
            status=status,
            content_type=None,
            body=b"",
            fetched_at=datetime.now(UTC),
            error=error,
        )


class Fetcher(Protocol):
    """Anything that can turn a URL into a :class:`FetchResult`. The injection point that
    makes the whole tool offline-testable."""

    def fetch(self, url: str) -> FetchResult: ...


class HttpFetcher:
    """The real one: stdlib urllib, robots-aware, descriptive UA, bounded body size.

    Nothing here runs at import time — constructing an `HttpFetcher` opens no sockets.
    The first network call this package can possibly make is a `.fetch()` invoked by
    `sentinel watch`, which is why `pytest` can run with the network unplugged.
    """

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = _TIMEOUT_SECONDS,
        respect_robots: bool = True,
        max_bytes: int = _MAX_BODY_BYTES,
    ) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        self._respect_robots = respect_robots
        self._max_bytes = max_bytes
        self._robots: dict[str, RobotFileParser | None] = {}

    def fetch(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            # Belt to the registry's braces. A watcher for a population that includes
            # people in hostile jurisdictions does not fetch over plaintext, ever.
            return FetchResult.failure(url, f"refusing non-https scheme: {parsed.scheme!r}")

        if self._respect_robots and not self._robots_allow(url):
            return FetchResult.failure(url, "robots.txt disallows this path for our user-agent")

        request = urllib.request.Request(  # noqa: S310 — scheme checked https above
            url,
            headers={"User-Agent": self._user_agent, "Accept": "*/*"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body = response.read(self._max_bytes + 1)
                if len(body) > self._max_bytes:
                    return FetchResult.failure(
                        url, f"body exceeds {self._max_bytes} bytes", status=response.status
                    )
                return FetchResult(
                    url=url,
                    ok=True,
                    status=response.status,
                    content_type=response.headers.get("Content-Type"),
                    body=body,
                    fetched_at=datetime.now(UTC),
                )
        except urllib.error.HTTPError as exc:
            # A 404 or a 403 is a *fetch failure*, not an empty page. Hashing an error
            # body would report "the page changed" the day a state stands up a WAF.
            #
            # Close it explicitly: urllib raises an HTTPError whose `fp` IS the live
            # response stream, so dropping it on the floor leaks the connection until the
            # GC gets round to it. This process is a long-lived weekly watcher against
            # servers that rate-limit, which is precisely where leaked sockets bite.
            with exc:
                return FetchResult.failure(url, f"HTTP {exc.code}", status=exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return FetchResult.failure(url, f"unreachable: {exc}")

    def _robots_allow(self, url: str) -> bool:
        """Check (and cache) robots.txt per host. A robots.txt we cannot read is treated as
        permissive — the same posture every mainstream crawler takes — but a robots.txt that
        *does* load and *does* disallow us is honoured without appeal."""
        parsed = urlparse(url)
        host = parsed.netloc
        if host not in self._robots:
            self._robots[host] = self._load_robots(parsed.scheme, host)

        parser = self._robots[host]
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)

    def _load_robots(self, scheme: str, host: str) -> RobotFileParser | None:
        """Fetch and parse one host's robots.txt, with a bounded timeout.

        We do the fetch ourselves rather than calling `RobotFileParser.read()`, which looks
        like the obvious choice and is a trap: `read()` calls `urlopen` with **no timeout**.
        A server that accepts the connection and then never responds would hang this process
        forever — and this process is an unattended weekly cron job against government
        servers, which is exactly where that happens and exactly where nobody is watching.
        Every socket this tool opens is bounded.
        """
        request = urllib.request.Request(  # noqa: S310 — scheme is https, checked by the caller
            f"{scheme}://{host}/robots.txt",
            headers={"User-Agent": self._user_agent},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                raw = response.read(_MAX_ROBOTS_BYTES)
        except urllib.error.HTTPError as exc:
            exc.close()  # the error carries the live response stream; do not leak it
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

        parser = RobotFileParser()
        parser.parse(raw.decode("utf-8", errors="replace").splitlines())
        return parser
