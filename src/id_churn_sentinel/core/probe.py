"""A HEAD-only availability channel, so an outage can be measured instead of guessed.

`REMOVAL_THRESHOLD` and `MIN_REMOVAL_SILENCE` are guesses, and `docs/THRESHOLD-EVIDENCE.md`
explains why the weekly watch can never stop them being guesses: every outage on record is
right-censored, and weekly sampling cannot resolve a sub-weekly outage *in principle*. You
cannot measure the length of something you look at once every seven days.

That document names the honest alternative — measure availability on a cheaper, higher
frequency, HEAD-only channel — and nothing implemented it. This is that channel.

**What a probe is, and what it is emphatically not.** One `HEAD` per eligible source per run.
No body, no hash, no snapshot, no baseline, no change record. A probe answers exactly one
question — did this URL answer, and how fast — and it is *not* a watch: a source that probed
fine is not a source that was read, and nothing here may ever be counted as an observation.
`tests/test_probe.py` asserts that a probe run leaves `snapshots`, `changes` and
`source_health` untouched, because the day this channel starts contributing to the watch
denominator is the day the feed starts claiming pages were checked that nobody read.

**Censoring is reported, never rounded away.** An episode whose start we did not see (the
source was already down at its first probe) or whose end we have not seen (it has not
recovered) has no measurable length, and its length is not estimated, not imputed, and not
counted in the distribution. `episodes_measured` and `episodes_censored` are printed side by
side with every duration figure, because a mean outage length over only the outages that
happened to end is exactly the shape of number this project exists not to publish.

**A 405 is not a failure.** A host that refuses `HEAD` is recorded as `head_unsupported` and
is never retried as `GET`: falling back to a body request would turn a cheap availability
channel into a second crawl, on hosts that have just told us no, and would make the probe
record incomparable with itself.
"""

from __future__ import annotations

import contextlib
import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from ..errors import SentinelError
from .eligibility import evaluate_source
from .fetch import ERROR_CLASS_ROBOTS_DISALLOWED, USER_AGENT, HttpFetcher
from .registry import Registry, Source

#: A HEAD probe is one small request; it does not need the 45s a slow CDN page needs. Bounded
#: well below the fetcher's timeout so a probe run over 156 sources stays cheap.
_PROBE_TIMEOUT_SECONDS = 15.0

#: Statuses that mean "this server will not answer HEAD", not "this URL is down".
_HEAD_UNSUPPORTED_STATUSES = frozenset({405, 501})

__all__ = [
    "PROBE_OUTCOMES",
    "HttpProber",
    "OutageEpisode",
    "ProbeError",
    "ProbeResult",
    "ProbeRun",
    "Prober",
    "SourceAvailability",
    "availability_block",
    "outage_episodes",
    "probe_report",
    "render_report",
    "run_probe",
]


class ProbeError(SentinelError):
    """The probe channel was asked for something it cannot honestly do."""


# ---- the closed outcome vocabulary ------------------------------------------------------

#: The URL answered. This is the ONLY outcome that counts as available.
PROBE_REACHABLE = "reachable"
#: A response arrived and it was an error status. The host is up; this URL is not serving.
PROBE_HTTP_ERROR = "http_error"
#: No HTTP exchange completed at all — DNS, connection, or timeout.
PROBE_UNREACHABLE = "unreachable"
#: The TLS handshake failed or the chain did not verify. Distinct from `unreachable` because
#: it is the host's defect and a different fix, and because weakening verification to
#: "resolve" it is the one thing this project will not do.
PROBE_TLS_ERROR = "tls_error"
#: robots.txt, or the recorded fetch-policy decision, forbids us. Counted, never attempted.
PROBE_ROBOTS_DISALLOWED = "robots_disallowed"
#: The host will not answer HEAD (405, or a status that only means something with a body).
#: Recorded and never retried as GET. It is an absence of measurement, not an outage.
PROBE_HEAD_UNSUPPORTED = "head_unsupported"
#: The source failed the shared eligibility predicate on this date. Counted, never attempted.
PROBE_NOT_ELIGIBLE = "not_eligible"

PROBE_OUTCOMES: frozenset[str] = frozenset(
    {
        PROBE_REACHABLE,
        PROBE_HTTP_ERROR,
        PROBE_UNREACHABLE,
        PROBE_TLS_ERROR,
        PROBE_ROBOTS_DISALLOWED,
        PROBE_HEAD_UNSUPPORTED,
        PROBE_NOT_ELIGIBLE,
    }
)

#: Outcomes that mean "this URL did not serve". `head_unsupported`, `robots_disallowed` and
#: `not_eligible` are deliberately absent: they are the absence of a measurement, and folding
#: them in would publish a policy decision or a 405 as an outage.
_DOWN_OUTCOMES: frozenset[str] = frozenset({PROBE_HTTP_ERROR, PROBE_UNREACHABLE, PROBE_TLS_ERROR})

#: Outcomes that carry no information about availability at all. A probe with one of these
#: does not open, extend or close an episode; it is skipped, and counted as skipped.
_UNMEASURED_OUTCOMES: frozenset[str] = frozenset(
    {PROBE_HEAD_UNSUPPORTED, PROBE_ROBOTS_DISALLOWED, PROBE_NOT_ELIGIBLE}
)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One HEAD attempt against one URL. No body, ever."""

    url: str
    outcome: str
    status: int | None = None
    latency_ms: int | None = None
    tls_ok: bool | None = None
    redirect_target: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in PROBE_OUTCOMES:
            raise ProbeError(f"unknown probe outcome: {self.outcome!r}")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ProbeError("latency_ms cannot be negative")


class Prober(Protocol):
    """Anything that can HEAD a URL. The injection point that keeps this suite offline.

    Deliberately NOT the `Fetcher` protocol. A prober that could be handed to `watch`, or a
    fetcher handed to `probe`, is one refactor away from a body request on this channel.
    """

    def probe(self, url: str) -> ProbeResult: ...


@dataclass(frozen=True, slots=True)
class ProbeRun:
    """One pass over the eligible registry, as it is recorded."""

    run_id: str
    started_at: datetime
    as_of: date
    results: tuple[tuple[str, ProbeResult], ...]

    @property
    def attempted(self) -> int:
        return sum(1 for _, r in self.results if r.outcome not in _UNMEASURED_OUTCOMES)

    @property
    def skipped(self) -> int:
        return sum(1 for _, r in self.results if r.outcome in _UNMEASURED_OUTCOMES)

    def counts(self) -> dict[str, int]:
        return {
            outcome: sum(1 for _, r in self.results if r.outcome == outcome)
            for outcome in sorted(PROBE_OUTCOMES)
        }


def run_probe(
    registry: Registry,
    prober: Prober,
    *,
    as_of: date,
    run_id: str,
    now: datetime | None = None,
    jurisdiction: str | None = None,
) -> ProbeRun:
    """One HEAD per eligible source. Every registry entry is accounted for, eligible or not.

    Ineligible sources are *recorded* as `not_eligible` rather than omitted. A source dropped
    from the record is a source whose availability history silently has a hole in it, and a
    hole in an outage history is indistinguishable from uptime.
    """
    started = _as_utc(now or datetime.now(UTC))
    sources: Sequence[Source] = (
        registry.for_jurisdiction(jurisdiction) if jurisdiction else registry.sources
    )
    results: list[tuple[str, ProbeResult]] = []
    for source in sorted(sources, key=lambda s: s.id):
        decision = evaluate_source(source, as_of=as_of)
        if not decision.eligible:
            results.append(
                (
                    source.id,
                    ProbeResult(
                        url=source.url,
                        outcome=PROBE_NOT_ELIGIBLE,
                        error=", ".join(decision.reasons),
                    ),
                )
            )
            continue
        results.append((source.id, prober.probe(source.url)))
    return ProbeRun(run_id=run_id, started_at=started, as_of=as_of, results=tuple(results))


# ---- outage episodes ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutageEpisode:
    """A maximal run of consecutive measured probes in which the URL did not serve.

    `length` is the number of consecutive failing probes — the unit the probe cadence
    defines, deliberately not converted into hours, because the cadence is a configuration
    the operator can change and a duration in hours would silently rewrite history if they
    did.

    `censored` means at least one end was never seen, and a censored episode's length is
    **not** a measurement of an outage. It is a lower bound on one, and it is excluded from
    every distribution this module reports.
    """

    source_id: str
    first_down_at: str
    last_down_at: str
    length: int
    censored: bool
    censoring: str  # "" | "left" | "right" | "both"
    recovered_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "first_down_at": self.first_down_at,
            "last_down_at": self.last_down_at,
            "length_probes": self.length,
            "censored": self.censored,
        }
        if self.censoring:
            payload["censoring"] = self.censoring
        if self.recovered_at:
            payload["recovered_at"] = self.recovered_at
        return payload


def outage_episodes(rows: Iterable[Mapping[str, Any]]) -> tuple[OutageEpisode, ...]:
    """Derive episodes from probe rows, ordered per source by probe time.

    Rows carrying an unmeasured outcome are skipped entirely — they neither open, extend nor
    close an episode. Skipping is not the same as treating them as up: a `head_unsupported`
    run in the middle of an outage leaves the episode open, which is correct, because nothing
    observed the source recover.
    """
    by_source: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row["source_id"]), []).append(row)

    episodes: list[OutageEpisode] = []
    for source_id in sorted(by_source):
        ordered = sorted(
            by_source[source_id], key=lambda r: (str(r["probed_at"]), str(r.get("run_id", "")))
        )
        measured = [r for r in ordered if str(r["outcome"]) not in _UNMEASURED_OUTCOMES]
        if not measured:
            continue
        episodes.extend(_episodes_for_source(source_id, measured))
    return tuple(episodes)


def _episodes_for_source(
    source_id: str, measured: Sequence[Mapping[str, Any]]
) -> list[OutageEpisode]:
    episodes: list[OutageEpisode] = []
    open_from: int | None = None
    for index, row in enumerate(measured):
        down = str(row["outcome"]) in _DOWN_OUTCOMES
        if down and open_from is None:
            open_from = index
        elif not down and open_from is not None:
            episodes.append(_episode(source_id, measured, open_from, index - 1, recovered=index))
            open_from = None
    if open_from is not None:
        episodes.append(_episode(source_id, measured, open_from, len(measured) - 1, recovered=None))
    return episodes


def _episode(
    source_id: str,
    measured: Sequence[Mapping[str, Any]],
    start: int,
    end: int,
    *,
    recovered: int | None,
) -> OutageEpisode:
    left = start == 0
    right = recovered is None
    censoring = "both" if left and right else "left" if left else "right" if right else ""
    return OutageEpisode(
        source_id=source_id,
        first_down_at=str(measured[start]["probed_at"]),
        last_down_at=str(measured[end]["probed_at"]),
        length=end - start + 1,
        censored=bool(censoring),
        censoring=censoring,
        recovered_at=str(measured[recovered]["probed_at"]) if recovered is not None else "",
    )


# ---- the report -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceAvailability:
    """Per-source availability over the probe record, with its denominator attached."""

    source_id: str
    probes: int
    measured: int
    reachable: int
    unmeasured: int
    episodes: tuple[OutageEpisode, ...]

    @property
    def measured_episodes(self) -> tuple[OutageEpisode, ...]:
        return tuple(e for e in self.episodes if not e.censored)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "probes": self.probes,
            # `measured` is the denominator any rate must be taken over, and it is published
            # beside every count for the same reason the watch publishes its attempt
            # denominator: a percentage without one is a number nobody can check.
            "measured": self.measured,
            "reachable": self.reachable,
            "unmeasured": self.unmeasured,
            "episodes": [e.to_dict() for e in self.episodes],
            "episodes_measured": len(self.measured_episodes),
            "episodes_censored": len(self.episodes) - len(self.measured_episodes),
        }


def probe_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Everything the probe table supports, and nothing it does not.

    Deterministic over the rows: no clock, no network. Two identical tables produce identical
    bytes, so a report committed today can be re-derived and byte-compared tomorrow.
    """
    episodes = outage_episodes(rows)
    by_source_episodes: dict[str, list[OutageEpisode]] = {}
    for episode in episodes:
        by_source_episodes.setdefault(episode.source_id, []).append(episode)

    by_source: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row["source_id"]), []).append(row)

    availability: list[SourceAvailability] = []
    for source_id in sorted(by_source):
        source_rows = by_source[source_id]
        unmeasured = sum(1 for r in source_rows if str(r["outcome"]) in _UNMEASURED_OUTCOMES)
        availability.append(
            SourceAvailability(
                source_id=source_id,
                probes=len(source_rows),
                measured=len(source_rows) - unmeasured,
                reachable=sum(1 for r in source_rows if str(r["outcome"]) == PROBE_REACHABLE),
                unmeasured=unmeasured,
                episodes=tuple(by_source_episodes.get(source_id, ())),
            )
        )

    measured_lengths = sorted(e.length for e in episodes if not e.censored)
    censored = [e for e in episodes if e.censored]
    return {
        "schema_version": "1.0",
        "statement": _STATEMENT,
        "probes": len(rows),
        "sources": len(by_source),
        "episodes_total": len(episodes),
        "episodes_measured": len(measured_lengths),
        "episodes_censored": len(censored),
        "censoring_by_end": {
            end: sum(1 for e in censored if e.censoring == end) for end in ("left", "right", "both")
        },
        "episode_length_probes": _distribution(measured_lengths),
        "sources_detail": [entry.to_dict() for entry in availability],
    }


def _distribution(lengths: Sequence[int]) -> dict[str, Any]:
    """The distribution over episodes whose BOTH ends were observed, or an explicit absence.

    With no measured episode there is no distribution, and the honest representation of that
    is `null` fields with a reason — not a zero, which reads as "no outages lasted any time".
    """
    if not lengths:
        return {
            "n": 0,
            "min": None,
            "median": None,
            "max": None,
            "reason": (
                "no outage episode has been observed to both start and end on this channel, "
                "so no length has been measured. This is not a finding that outages are short."
            ),
        }
    middle = len(lengths) // 2
    median = lengths[middle] if len(lengths) % 2 else (lengths[middle - 1] + lengths[middle]) / 2
    return {
        "n": len(lengths),
        "min": lengths[0],
        "median": median,
        "max": lengths[-1],
        "counts": {str(length): lengths.count(length) for length in sorted(set(lengths))},
    }


_STATEMENT = (
    "Derived from a HEAD-only availability channel. A probe records whether a URL answered; "
    "it never reads a body, never produces a snapshot, and is never counted as a watch "
    "observation. Episode lengths are counted in PROBES, not hours, because the probe cadence "
    "is operator configuration. A censored episode — one whose start or end this channel "
    "never saw — has no measured length and is excluded from every distribution here."
)


def availability_block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The optional `availability` block for `status.json`, or `None` when nothing was probed.

    `None` — and therefore an absent key — is the correct representation of "this channel has
    not run". Emitting a block full of zeroes would publish "no outages" over the top of "no
    measurements", which is the defect this whole channel exists to remove from the removal
    thresholds.
    """
    if not rows:
        return None
    report = probe_report(rows)
    latest = max(str(row["probed_at"]) for row in rows)
    return {
        "channel": "head-probe",
        "statement": _STATEMENT,
        "last_probe_at": latest,
        "probes": report["probes"],
        "sources": report["sources"],
        "episodes_total": report["episodes_total"],
        "episodes_measured": report["episodes_measured"],
        "episodes_censored": report["episodes_censored"],
        "episode_length_probes": report["episode_length_probes"],
    }


def render_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"probes recorded:      {report['probes']} over {report['sources']} source(s)",
        f"outage episodes:      {report['episodes_total']}",
        f"  both ends observed: {report['episodes_measured']}",
        f"  censored:           {report['episodes_censored']} "
        f"(left {report['censoring_by_end']['left']}, "
        f"right {report['censoring_by_end']['right']}, "
        f"both {report['censoring_by_end']['both']})",
        "",
    ]
    distribution = report["episode_length_probes"]
    if distribution["n"]:
        lines.append(
            f"episode length in probes (n={distribution['n']} measured, "
            f"{report['episodes_censored']} censored and excluded): "
            f"min {distribution['min']}, median {distribution['median']}, "
            f"max {distribution['max']}"
        )
        for length, count in distribution["counts"].items():
            lines.append(f"    {length} probe(s): {count} episode(s)")
    else:
        lines.append(f"episode length: {distribution['reason']}")
    lines.extend(["", report["statement"], ""])
    for entry in report["sources_detail"]:
        lines.append(
            f"  {entry['source_id']:<48} reachable {entry['reachable']}/{entry['measured']} "
            f"measured ({entry['unmeasured']} unmeasured), "
            f"{entry['episodes_measured']} measured / {entry['episodes_censored']} censored "
            f"episode(s)"
        )
    return "\n".join(lines) + "\n"


def dumps_report(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=False) + "\n"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# ---- the real prober ------------------------------------------------------------------------


class HttpProber:
    """The real one: a single stdlib `HEAD`, under the fetcher's own politeness posture.

    It composes an :class:`~id_churn_sentinel.core.fetch.HttpFetcher` rather than
    reimplementing robots.txt, per-host crawl spacing or the descriptive User-Agent. Two
    crawlers in one repository that each decided robots for themselves would eventually decide
    it differently, and this project's entire gap vocabulary rests on the two agreeing.

    Constructing one opens no sockets, exactly as `HttpFetcher` does not.
    """

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
        timeout: float = _PROBE_TIMEOUT_SECONDS,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._fetcher = fetcher if fetcher is not None else HttpFetcher()
        self._timeout = timeout
        self._opener = opener if opener is not None else _open_head
        self._monotonic = monotonic if monotonic is not None else time.monotonic

    def probe(self, url: str) -> ProbeResult:
        refusal = self._fetcher.may_request(url)
        if refusal == ERROR_CLASS_ROBOTS_DISALLOWED:
            return ProbeResult(
                url=url, outcome=PROBE_ROBOTS_DISALLOWED, error="robots.txt disallows this URL"
            )
        if refusal is not None:
            # The only other refusal the shared guard produces is a non-https scheme, which
            # the registry already rejects. It is unreachable-by-policy, not an outage, and it
            # is recorded as a disallowed probe rather than as downtime.
            return ProbeResult(url=url, outcome=PROBE_ROBOTS_DISALLOWED, error=refusal)

        parsed = urlparse(url)
        self._fetcher.space_before_request(parsed.netloc)
        request = urllib.request.Request(  # noqa: S310 — https enforced by `may_request`
            url, method="HEAD", headers={"User-Agent": USER_AGENT}
        )
        started = self._monotonic()
        try:
            response = self._opener(request, self._timeout)
        except urllib.error.HTTPError as exc:
            elapsed = self._elapsed(started)
            if exc.code in _HEAD_UNSUPPORTED_STATUSES:
                # A host that will not answer HEAD has told us something about its server, not
                # about its availability. Retrying as GET would turn a cheap channel into a
                # second crawl on a host that has just said no, and would make this record
                # incomparable with itself.
                return ProbeResult(
                    url=url,
                    outcome=PROBE_HEAD_UNSUPPORTED,
                    status=exc.code,
                    latency_ms=elapsed,
                    tls_ok=True,
                    error=f"HEAD not supported (HTTP {exc.code})",
                )
            return ProbeResult(
                url=url,
                outcome=PROBE_HTTP_ERROR,
                status=exc.code,
                latency_ms=elapsed,
                tls_ok=True,
                error=f"HTTP {exc.code}",
            )
        except urllib.error.URLError as exc:
            elapsed = self._elapsed(started)
            if isinstance(exc.reason, ssl.SSLError | ssl.SSLCertVerificationError):
                # Named separately from `unreachable` because it is the host's defect and a
                # different fix — and because the two-line change that would "resolve" it is
                # disabling verification, which this project does not do.
                return ProbeResult(
                    url=url,
                    outcome=PROBE_TLS_ERROR,
                    latency_ms=elapsed,
                    tls_ok=False,
                    error=str(exc.reason),
                )
            return ProbeResult(
                url=url, outcome=PROBE_UNREACHABLE, latency_ms=elapsed, error=str(exc.reason)
            )
        except (TimeoutError, OSError) as exc:  # pragma: no cover - transport-level
            return ProbeResult(
                url=url,
                outcome=PROBE_UNREACHABLE,
                latency_ms=self._elapsed(started),
                error=str(exc),
            )

        elapsed = self._elapsed(started)
        status = int(getattr(response, "status", 0) or 0)
        final_url = str(getattr(response, "url", url) or url)
        with contextlib.suppress(Exception):
            response.close()
        if status in _HEAD_UNSUPPORTED_STATUSES:
            return ProbeResult(
                url=url,
                outcome=PROBE_HEAD_UNSUPPORTED,
                status=status,
                latency_ms=elapsed,
                tls_ok=True,
                error=f"HEAD not supported (HTTP {status})",
            )
        return ProbeResult(
            url=url,
            outcome=PROBE_REACHABLE,
            status=status,
            latency_ms=elapsed,
            tls_ok=True,
            redirect_target="" if final_url == url else final_url,
        )

    def _elapsed(self, started: float) -> int:
        return max(0, round((self._monotonic() - started) * 1000))


def _open_head(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — https enforced above
