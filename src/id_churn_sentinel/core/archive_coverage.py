"""How much of this registry the Internet Archive has actually captured.

This is the measurement #78 should have before anything is built, not after.

#78 proposes `sentinel witness`: fetch the Internet Archive's own dated captures of a
source URL either side of a change, and record whether a second party's bytes agree
with ours. It rests on a sentence in the proposal — *"The Internet Archive holds
independent, dated captures of most of these government pages"* — which is plausible,
was never measured, and decides whether the feature is worth a store migration and a
`witness` block on `changes-v2`.

It matters most for the twelve registry sources this tool's own crawler cannot fetch
(seven HTTP 403s, three TLS failures, a timeout, a 500). Those are exactly the hosts
where a second witness is worth the most, and exactly the hosts whose crawler-hostile
posture makes archive coverage least predictable. If the unfetchable twelve are also
the thinly-captured ones, the `backfill` half of #78 — the part meant to approximate
the outage history `docs/THRESHOLD-EVIDENCE.md` says does not exist — is worth much
less than it looks.

## The one discipline this module exists to hold

**A query that failed is not zero captures.** The distinction is the whole point, and
it is not hypothetical: the first collection run of this measurement used five
concurrent workers and the Internet Archive refused **136 of 156** connections. Folded
into a "captures found" count, that run would have reported an archive that has almost
nothing — a confident, precisely wrong number, published from an outage. So the
outcome vocabulary is closed and separates the three states that a naive count merges:

``captured``       the index answered and returned rows
``no_capture``     the index answered and returned nothing
``query_failed``   the index could not be read — **an absence of knowledge, not of captures**

`total_captures` is ``None`` under ``query_failed``, not ``0``, so arithmetic cannot
quietly average a failure in as a zero.

## Two more ways this measurement could lie, and what is done about each

**A capped read reported as a total.** The CDX index is queried with a row limit, and a
URL that hits the limit has *at least* that many captures rather than exactly that
many. Those records carry ``truncated=True``, :func:`render_markdown` prints their
counts with a ``≥``, and no average is computed over them anywhere — an average whose
inputs are silently capped is a number about the query limit, not about the Archive.

**An archived error page counted as a witness.** The Archive stores what it received,
including the 403 challenge pages that some of these hosts serve to crawlers, which
the measured sample does contain. A capture whose own status was not ``200`` is
bytes about a refusal, not bytes about the page, and comparing a hash against one
would produce a confident `disagrees_after` when the truth is that no second witness
exists. So ``usable_captures`` counts only HTTP 200 rows, and it is the number #78
should be sized against — ``total_captures`` is context.

Nothing here fetches anything. The network lives in ``tools/measure_archive_coverage.py``;
this module takes rows and produces a report, so it is testable without the Archive.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from id_churn_sentinel.core.registry import Registry, Source

__all__ = [
    "CAPTURED",
    "NO_CAPTURE",
    "OUTCOMES",
    "QUERY_FAILED",
    "USABLE_STATUS",
    "ArchiveCoverageReport",
    "CdxRows",
    "SourceCoverage",
    "measure",
    "render_markdown",
    "summarize",
]

#: The index answered and returned at least one capture row.
CAPTURED = "captured"
#: The index answered and returned no rows at all.
NO_CAPTURE = "no_capture"
#: The index could not be read. NOT zero captures — see the module docstring.
QUERY_FAILED = "query_failed"

#: Closed set. A fourth outcome means a new fact about the world, not a new label.
OUTCOMES: tuple[str, ...] = (CAPTURED, NO_CAPTURE, QUERY_FAILED)

#: The only capture status that is bytes about the page rather than about a refusal.
USABLE_STATUS = "200"

#: One raw CDX answer: either ``{"ok": True, "rows": [...], "limit": n}`` or
#: ``{"ok": False, "error": "..."}``. Deliberately the shape the collector writes, so
#: the stored evidence and the parsed evidence cannot drift apart.
CdxRows = Mapping[str, Any]


@dataclass(frozen=True)
class SourceCoverage:
    """What the Archive holds for one registry source."""

    source_id: str
    jurisdiction: str
    document_class: str
    url: str
    #: One of :data:`OUTCOMES`.
    outcome: str
    #: Whether this tool's own crawler could reach the URL when the registry was checked.
    fetchable_by_us: bool
    #: ``None`` under ``query_failed``. Never 0 for an unread index.
    total_captures: int | None
    #: Captures whose own HTTP status was 200. ``None`` under ``query_failed``.
    usable_captures: int | None
    #: Distinct content digests among the usable captures — a floor on how many times
    #: the Archive observed the page change. ``None`` under ``query_failed``.
    distinct_usable_digests: int | None
    #: ``YYYY-MM-DD`` of the first/last usable capture, or ``None``.
    first_usable: str | None
    last_usable: str | None
    #: True when the row limit was reached, so the counts above are lower bounds.
    truncated: bool
    #: Present only under ``query_failed``.
    error: str | None


def _capture_date(timestamp: str) -> str:
    """``20260527184940`` -> ``2026-05-27``. Returns the input if it is not a stamp."""
    if len(timestamp) < 8 or not timestamp[:8].isdigit():
        return timestamp
    return f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]}"


def _coverage_for(source: Source, answer: CdxRows) -> SourceCoverage:
    if not answer.get("ok"):
        # The distinction this module exists for. Everything numeric stays None.
        return SourceCoverage(
            source_id=source.id,
            jurisdiction=source.jurisdiction,
            document_class=source.document_class,
            url=source.url,
            fetchable_by_us=source.reachable,
            outcome=QUERY_FAILED,
            total_captures=None,
            usable_captures=None,
            distinct_usable_digests=None,
            first_usable=None,
            last_usable=None,
            truncated=False,
            error=str(answer.get("error", "unknown")),
        )

    raw_rows: Sequence[Sequence[str]] = answer.get("rows") or []
    # The CDX JSON output leads with a header row naming the requested fields.
    rows = [row for row in raw_rows[1:] if len(row) >= 3]
    limit = answer.get("limit")
    truncated = bool(isinstance(limit, int) and len(rows) >= limit)

    usable = [row for row in rows if row[1] == USABLE_STATUS]
    dates = sorted(_capture_date(row[0]) for row in usable)

    return SourceCoverage(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        fetchable_by_us=source.reachable,
        outcome=CAPTURED if rows else NO_CAPTURE,
        total_captures=len(rows),
        usable_captures=len(usable),
        distinct_usable_digests=len({row[2] for row in usable}),
        first_usable=dates[0] if dates else None,
        last_usable=dates[-1] if dates else None,
        truncated=truncated,
        error=None,
    )


@dataclass(frozen=True)
class ArchiveCoverageReport:
    """The whole measurement, with the aggregates #78 needs to be sized against."""

    measured_on: str
    row_limit: int
    n_sources: int
    per_source: tuple[SourceCoverage, ...]
    counts_by_outcome: Mapping[str, int]
    #: Sources the index answered for that hold no usable (HTTP 200) capture at all.
    n_no_usable_capture: int
    #: Sources whose counts are lower bounds because the row limit was reached.
    n_truncated: int
    #: The cross-tab #78 turns on: what the Archive holds for the sources our own
    #: crawler cannot fetch.
    unfetchable_by_us: Mapping[str, int]

    def to_json(self) -> str:
        payload = {
            "measured_on": self.measured_on,
            "row_limit": self.row_limit,
            "n_sources": self.n_sources,
            "counts_by_outcome": dict(self.counts_by_outcome),
            "n_no_usable_capture": self.n_no_usable_capture,
            "n_truncated": self.n_truncated,
            "unfetchable_by_us": dict(self.unfetchable_by_us),
            "per_source": [asdict(row) for row in self.per_source],
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def measure(
    registry: Registry, answers: Mapping[str, CdxRows], *, measured_on: str, row_limit: int
) -> ArchiveCoverageReport:
    """Turn raw CDX answers into a report. Pure; no network."""
    per_source = tuple(
        _coverage_for(source, answers.get(source.id, {"ok": False, "error": "not collected"}))
        for source in registry.sources
    )
    return summarize(per_source, measured_on=measured_on, row_limit=row_limit)


def summarize(
    per_source: Iterable[SourceCoverage], *, measured_on: str, row_limit: int
) -> ArchiveCoverageReport:
    rows = tuple(per_source)
    counts = {outcome: sum(1 for r in rows if r.outcome == outcome) for outcome in OUTCOMES}

    answered = [r for r in rows if r.outcome != QUERY_FAILED]
    unfetchable = [r for r in rows if not r.fetchable_by_us]

    return ArchiveCoverageReport(
        measured_on=measured_on,
        row_limit=row_limit,
        n_sources=len(rows),
        per_source=rows,
        counts_by_outcome=counts,
        n_no_usable_capture=sum(1 for r in answered if not r.usable_captures),
        n_truncated=sum(1 for r in rows if r.truncated),
        unfetchable_by_us={
            "n_sources": len(unfetchable),
            "with_usable_captures": sum(
                1 for r in unfetchable if r.usable_captures is not None and r.usable_captures > 0
            ),
            "no_usable_capture": sum(1 for r in unfetchable if r.usable_captures == 0),
            "query_failed": sum(1 for r in unfetchable if r.outcome == QUERY_FAILED),
        },
    )


def _lower_bound(value: int | None, truncated: bool) -> str:
    """A capped count is rendered as a bound, never as a total."""
    if value is None:
        return "—"
    return f"≥{value}" if truncated else str(value)


def render_markdown(report: ArchiveCoverageReport) -> str:
    """The committed evidence document, derived from the report rather than typed."""
    counts = report.counts_by_outcome
    unfetchable = report.unfetchable_by_us
    lines = [
        "# Internet Archive coverage of this registry",
        "",
        f"**Measured {report.measured_on}** against all {report.n_sources} registered sources,",
        "by querying the Internet Archive's CDX index once per source URL.",
        "",
        "This exists to answer one question before #78 is built: does the Archive",
        "actually hold what a second-witness feature would need? Regenerate with",
        "`python3 tools/measure_archive_coverage.py`.",
        "",
        "## The number that is not a number",
        "",
        "A query that failed is recorded as `query_failed`, never as zero captures.",
        "That is not a hypothetical distinction. The first collection run used five",
        "concurrent workers and the Archive refused **136 of 156** connections; folded",
        "into a count, that run would have reported an archive holding almost nothing.",
        "Counts are `null` under `query_failed`, so no average can absorb an outage as",
        "a zero.",
        "",
        "## What the index holds",
        "",
        "| Outcome | Sources |",
        "| --- | ---: |",
        f"| `captured` — the index answered with rows | {counts.get(CAPTURED, 0)} |",
        f"| `no_capture` — the index answered with none | {counts.get(NO_CAPTURE, 0)} |",
        f"| `query_failed` — the index could not be read | {counts.get(QUERY_FAILED, 0)} |",
        "",
        f"Of the sources the index answered for, **{report.n_no_usable_capture}** hold no",
        "capture whose own HTTP status was `200`. Those are the ones a witness feature",
        "could not use: the Archive stores what it received, 403 challenge pages",
        "included, and a hash comparison against an archived refusal would report a",
        "confident disagreement where no second witness exists.",
        "",
        f"**{report.n_truncated}** source(s) reached the {report.row_limit}-row query limit,",
        "so their counts below are lower bounds and are shown with `≥`.",
        "",
        "## The cross-tab #78 turns on",
        "",
        "Twelve registry sources cannot be fetched by this tool's own crawler. They are",
        "where a second witness is worth the most, and where a crawler-hostile posture",
        "makes archive coverage least predictable.",
        "",
        "| | Sources |",
        "| --- | ---: |",
        f"| Unfetchable by our crawler | {unfetchable.get('n_sources', 0)} |",
        f"| …of which the Archive has usable captures for | {unfetchable.get('with_usable_captures', 0)} |",
        f"| …of which the Archive has none | {unfetchable.get('no_usable_capture', 0)} |",
        f"| …of which the index could not be read | {unfetchable.get('query_failed', 0)} |",
        "",
        "## Per source",
        "",
        "`usable` counts only HTTP 200 captures. `digests` is the number of distinct",
        "content hashes among them — a floor on how many times the Archive saw the page",
        "change, and the closest thing to the history `docs/THRESHOLD-EVIDENCE.md`",
        "records as missing.",
        "",
        "| Source | Ours? | Outcome | Total | Usable | Digests | First | Last |",
        "| --- | :---: | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in sorted(report.per_source, key=lambda r: r.source_id):
        lines.append(
            f"| `{row.source_id}` "
            f"| {'yes' if row.fetchable_by_us else '**no**'} "
            f"| `{row.outcome}` "
            f"| {_lower_bound(row.total_captures, row.truncated)} "
            f"| {_lower_bound(row.usable_captures, row.truncated)} "
            f"| {_lower_bound(row.distinct_usable_digests, row.truncated)} "
            f"| {row.first_usable or '—'} "
            f"| {row.last_usable or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)
