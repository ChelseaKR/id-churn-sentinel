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
names the project and its contact URL, spaces consecutive requests to the same host so a
run over many pages on one server arrives as a trickle rather than a burst, and is driven
at a weekly cadence. These are government servers funded by the people this tool serves; a
watcher that hammers them is taking from the commons it claims to protect. The spacing
lives in the fetcher, not the caller, so no code path that reaches the network can skip it
— the same reasoning that puts robots and the User-Agent here (threat model 06 names
"per-host limits" as the denial-of-service mitigation).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPMessage
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

__all__ = [
    "ERROR_CLASSES",
    "ERROR_CLASS_BODY_TOO_LARGE",
    "ERROR_CLASS_HTTP_ERROR",
    "ERROR_CLASS_NON_HTTPS",
    "ERROR_CLASS_ROBOTS_DISALLOWED",
    "ERROR_CLASS_UNREACHABLE",
    "USER_AGENT",
    "FetchResult",
    "Fetcher",
    "HttpFetcher",
    "RedirectHop",
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

# Minimum gap between two page requests to the *same* host. The registry clusters onto a
# handful of servers (travel.state.gov, ssa.gov, a few state DMV domains carry many of the
# sources each), so an unspaced weekly run would fire a burst at one host and look exactly
# like the denial-of-service pattern the threat model warns against. 2s is generous for a
# job that runs once a week over a few hundred pages — the whole run gains only seconds —
# while keeping this tool a well-behaved guest on infrastructure the people it serves pay
# for. It bounds the *rate*, not the total, so a slow host is never penalised: the gap is
# measured from when each request goes out, and a server that took 3s to answer has already
# satisfied the interval, so the next request to it is not delayed further.
_MIN_HOST_INTERVAL_SECONDS = 2.0

# The stable error classes (docs/04-ARCHITECTURE §component obligations). The free-text
# `error` string is for a human reading a run receipt; it embeds server messages and byte
# counts, so it is useless as a query key. The class is the queryable, closed-vocabulary
# fact — "how many failures last quarter were robots refusals?" must not require parsing
# prose. `sources/registry.json` gap reasons and `source_health.last_error` remain prose;
# this vocabulary belongs to fetch attempts.
ERROR_CLASS_NON_HTTPS = "non-https-scheme"
ERROR_CLASS_ROBOTS_DISALLOWED = "robots-disallowed"
ERROR_CLASS_BODY_TOO_LARGE = "body-too-large"
ERROR_CLASS_HTTP_ERROR = "http-error"
ERROR_CLASS_UNREACHABLE = "unreachable"
ERROR_CLASSES = frozenset(
    {
        ERROR_CLASS_NON_HTTPS,
        ERROR_CLASS_ROBOTS_DISALLOWED,
        ERROR_CLASS_BODY_TOO_LARGE,
        ERROR_CLASS_HTTP_ERROR,
        ERROR_CLASS_UNREACHABLE,
    }
)


@dataclass(frozen=True, slots=True)
class RedirectHop:
    """One followed redirect: the 3xx status that redirected us and the URL it sent us to.

    The chain is evidence, not decoration. A registry URL that silently 301s to a marketing
    page is one of the named registry-rot risks, and "the page changed" versus "which page
    we were shown changed" are different claims — the chain is what lets a later reader tell
    them apart from the retained record rather than from a re-fetch that may no longer
    reproduce it.
    """

    status: int
    url: str


@dataclass(frozen=True, slots=True)
class FetchResult:
    """The outcome of one fetch.

    `ok=True` means we hold bytes we can hash. `ok=False` means we hold *nothing* — the
    body is empty and `error` says why — and the caller's only correct response is to
    carry the previous hash forward. There is no third state, and no way to construct a
    "failed but here's a body anyway" result that could be mistaken for content.

    The evidence fields (`final_url`, `redirect_chain`, `bytes_received`, `byte_limit`,
    `truncated`, `error_class`) exist so the fetch can be *persisted* completely
    (`DATA-04`/`DET-01`): a diff you cannot reproduce is a claim, and a fetch whose
    redirect path and byte bound were discarded is a fetch you cannot audit.
    """

    url: str
    ok: bool
    status: int | None
    content_type: str | None
    body: bytes
    fetched_at: datetime
    error: str | None = None
    # Evidence. `final_url` is the URL that actually answered (== `url` when no redirect
    # was followed). `byte_limit` is None when no bounded request was made — a refusal
    # that never opened a socket, or an injected fetcher that applies no bound; the real
    # `HttpFetcher` always records the bound it read under.
    final_url: str = ""
    redirect_chain: tuple[RedirectHop, ...] = ()
    bytes_received: int = 0
    byte_limit: int | None = None
    truncated: bool = False
    error_class: str = ""

    @classmethod
    def failure(
        cls,
        url: str,
        error: str,
        status: int | None = None,
        *,
        error_class: str = "",
        final_url: str = "",
        redirect_chain: tuple[RedirectHop, ...] = (),
        bytes_received: int = 0,
        byte_limit: int | None = None,
        truncated: bool = False,
    ) -> FetchResult:
        # Derive the class when the caller did not name one, so a stub or an older caller
        # still produces a classified failure: a failure with an HTTP status is an HTTP
        # error; anything else is transport-level unreachability.
        if not error_class:
            error_class = ERROR_CLASS_HTTP_ERROR if status is not None else ERROR_CLASS_UNREACHABLE
        return cls(
            url=url,
            ok=False,
            status=status,
            content_type=None,
            body=b"",
            fetched_at=datetime.now(UTC),
            error=error,
            final_url=final_url or url,
            redirect_chain=redirect_chain,
            bytes_received=bytes_received,
            byte_limit=byte_limit,
            truncated=truncated,
            error_class=error_class,
        )


class Fetcher(Protocol):
    """Anything that can turn a URL into a :class:`FetchResult`. The injection point that
    makes the whole tool offline-testable."""

    def fetch(self, url: str) -> FetchResult: ...


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    """urllib's own redirect handler, with a memory.

    Plain `urlopen` follows redirects invisibly: the caller sees the final body and, at
    best, the final URL — every intermediate hop is discarded. This subclass changes no
    redirect *behaviour* (same hop limit, same status handling, delegated to the stdlib);
    it only writes down each hop as it is followed, so the chain can be persisted as fetch
    evidence. One recorder serves one `fetch()` call and is never reused — a recorder
    shared across calls would splice two pages' journeys into one lie.
    """

    def __init__(self) -> None:
        self.hops: list[RedirectHop] = []

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is not None:
            self.hops.append(RedirectHop(status=code, url=new_request.full_url))
        return new_request


def _open_page(
    request: urllib.request.Request, *, timeout: float, recorder: _RedirectRecorder
) -> Any:
    """Open one page request through an opener that records redirect hops.

    A module-level seam (rather than an inline `build_opener` call) so the tests can route
    page fetches through a fake exactly as they route robots.txt fetches through a fake
    `urlopen` — nothing in the suite resolves a hostname.
    """
    opener = urllib.request.build_opener(recorder)
    return opener.open(request, timeout=timeout)


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
        min_host_interval: float = _MIN_HOST_INTERVAL_SECONDS,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        self._respect_robots = respect_robots
        self._max_bytes = max_bytes
        self._robots: dict[str, RobotFileParser | None] = {}
        # Per-host crawl spacing. The clock and sleep are injectable so the whole seam stays
        # offline-testable — a fake clock asserts the spacing maths with no wall-clock wait.
        self._min_host_interval = min_host_interval
        self._sleep = sleep if sleep is not None else time.sleep
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._last_request_at: dict[str, float] = {}

    def fetch(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            # Belt to the registry's braces. A watcher for a population that includes
            # people in hostile jurisdictions does not fetch over plaintext, ever.
            return FetchResult.failure(
                url,
                f"refusing non-https scheme: {parsed.scheme!r}",
                error_class=ERROR_CLASS_NON_HTTPS,
            )

        if self._respect_robots and not self._robots_allow(url):
            return FetchResult.failure(
                url,
                "robots.txt disallows this path for our user-agent",
                error_class=ERROR_CLASS_ROBOTS_DISALLOWED,
            )

        # Space page requests to this host. Placed after the guards above so a refusal
        # (non-https, robots-disallowed) never sleeps — we only pace requests that will
        # actually go out. The robots.txt fetch is not paced: it is cached once per host and
        # is part of the same initial visit as the first page, not a second crawl of it.
        self._space_before_request(parsed.netloc)

        request = urllib.request.Request(  # noqa: S310 — scheme checked https above
            url,
            headers={"User-Agent": self._user_agent, "Accept": "*/*"},
            method="GET",
        )
        # One recorder per call: its hops are this fetch's redirect chain, kept even when
        # the fetch ends in an error partway down the chain — a failure's journey is
        # evidence too (the hop that 404s is the hop a maintainer has to look at).
        recorder = _RedirectRecorder()
        try:
            with _open_page(request, timeout=self._timeout, recorder=recorder) as response:
                chain = tuple(recorder.hops)
                final_url = chain[-1].url if chain else url
                body = response.read(self._max_bytes + 1)
                if len(body) > self._max_bytes:
                    return FetchResult.failure(
                        url,
                        f"body exceeds {self._max_bytes} bytes",
                        status=response.status,
                        error_class=ERROR_CLASS_BODY_TOO_LARGE,
                        final_url=final_url,
                        redirect_chain=chain,
                        bytes_received=len(body),
                        byte_limit=self._max_bytes,
                        truncated=True,
                    )
                return FetchResult(
                    url=url,
                    ok=True,
                    status=response.status,
                    content_type=response.headers.get("Content-Type"),
                    body=body,
                    fetched_at=datetime.now(UTC),
                    final_url=final_url,
                    redirect_chain=chain,
                    bytes_received=len(body),
                    byte_limit=self._max_bytes,
                    truncated=False,
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
                chain = tuple(recorder.hops)
                return FetchResult.failure(
                    url,
                    f"HTTP {exc.code}",
                    status=exc.code,
                    error_class=ERROR_CLASS_HTTP_ERROR,
                    final_url=chain[-1].url if chain else url,
                    redirect_chain=chain,
                    byte_limit=self._max_bytes,
                )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            chain = tuple(recorder.hops)
            return FetchResult.failure(
                url,
                f"unreachable: {exc}",
                error_class=ERROR_CLASS_UNREACHABLE,
                final_url=chain[-1].url if chain else url,
                redirect_chain=chain,
                byte_limit=self._max_bytes,
            )

    def _space_before_request(self, host: str) -> None:
        """Sleep so consecutive page requests to ``host`` are at least
        ``min_host_interval`` apart. The gap is measured between request *starts*, so a slow
        response shrinks the added wait rather than stacking on top of it — the interval
        bounds the rate we hit a host, never the time a host takes to answer. The first
        request to a host never waits; only the second and later ones do."""
        if self._min_host_interval <= 0:
            return
        now = self._monotonic()
        last = self._last_request_at.get(host)
        if last is not None:
            remaining = self._min_host_interval - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_at[host] = now

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
