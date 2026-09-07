"""The archive-coverage measurement must not turn an outage into a finding.

The whole reason this module exists as code rather than as a one-off script is the
distinction between *"the Archive holds nothing for this page"* and *"we could not ask
the Archive"*. Those two produce the same number in any naive count, and one of them
is a fact about a government page while the other is a fact about a bad afternoon.
The first collection run made that concrete: five concurrent workers, and the Archive
refused 136 of 156 connections.

So the assertions below are mostly about what the report refuses to say.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from id_churn_sentinel.core.archive_coverage import (
    CAPTURED,
    NO_CAPTURE,
    OUTCOMES,
    QUERY_FAILED,
    ArchiveCoverageReport,
    measure,
    render_markdown,
    summarize,
)
from id_churn_sentinel.core.registry import load_registry

HEADER = ["timestamp", "statuscode", "digest", "mimetype"]


def _rows(*captures: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "ok": True,
        "limit": 2000,
        "rows": [HEADER, *[[t, s, d, "text/html"] for t, s, d in captures]],
    }


@pytest.fixture(scope="module")
def registry():  # type: ignore[no-untyped-def]
    return load_registry()


def _report(registry, answers: dict[str, Any]) -> ArchiveCoverageReport:  # type: ignore[no-untyped-def]
    return measure(registry, answers, measured_on="2026-09-07", row_limit=2000)


def test_a_failed_query_is_never_reported_as_zero_captures(registry) -> None:  # type: ignore[no-untyped-def]
    """The assertion this whole module is for.

    A source whose CDX query failed must come back as `query_failed` with `None`
    counts, so no later arithmetic can average an outage in as a zero. If this ever
    becomes `0`, the report starts claiming the Internet Archive has nothing for
    pages nobody managed to ask about.
    """
    first = registry.sources[0]
    report = _report(registry, {first.id: {"ok": False, "error": "Connection refused"}})
    row = next(r for r in report.per_source if r.source_id == first.id)

    assert row.outcome == QUERY_FAILED
    assert row.total_captures is None
    assert row.usable_captures is None
    assert row.distinct_usable_digests is None
    assert row.error == "Connection refused"


def test_a_source_never_asked_about_is_a_failure_not_an_empty_archive(registry) -> None:  # type: ignore[no-untyped-def]
    """A partial collection must not read as a complete one.

    `measure` is given answers for zero sources here. Every source must land in
    `query_failed`, not in `no_capture` — "we did not ask" and "there is nothing"
    are different claims, and only one of them is about the Archive.
    """
    report = _report(registry, {})
    assert report.counts_by_outcome[QUERY_FAILED] == len(registry.sources)
    assert report.counts_by_outcome[NO_CAPTURE] == 0
    assert report.counts_by_outcome[CAPTURED] == 0
    assert report.n_captured_but_none_usable == 0  # nothing was answered, so nothing is known


def test_captures_that_are_all_error_pages_are_kept_apart_from_having_none(registry) -> None:  # type: ignore[no-untyped-def]
    """ "Nothing was archived" and "everything archived is a bot wall" are different facts.

    Merging them is the same conflation this module exists to refuse, one level up: a
    page the Archive has fifty 403 captures of is a page a witness feature must not
    use, and it is *not* a page the Archive has never seen. The headline count is
    therefore about the second state only; the first is `no_capture` in the outcome
    table.
    """
    first, second = registry.sources[0], registry.sources[1]
    report = _report(
        registry,
        {
            first.id: _rows(("20260101000000", "403", "AAA"), ("20260201000000", "403", "AAA")),
            second.id: {"ok": True, "limit": 2000, "rows": [HEADER]},
        },
    )
    all_errors = next(r for r in report.per_source if r.source_id == first.id)
    nothing = next(r for r in report.per_source if r.source_id == second.id)

    assert all_errors.outcome == CAPTURED
    assert all_errors.total_captures == 2
    assert all_errors.usable_captures == 0
    assert nothing.outcome == NO_CAPTURE

    # Only the first is counted, and the second is visible as `no_capture` instead.
    assert report.n_captured_but_none_usable == 1


def test_an_answered_empty_index_is_no_capture(registry) -> None:  # type: ignore[no-untyped-def]
    """The other side of the same coin: an answer of "none" is a real measurement."""
    first = registry.sources[0]
    report = _report(registry, {first.id: {"ok": True, "limit": 2000, "rows": [HEADER]}})
    row = next(r for r in report.per_source if r.source_id == first.id)

    assert row.outcome == NO_CAPTURE
    assert row.total_captures == 0
    assert row.usable_captures == 0


def test_an_archived_error_page_is_not_a_usable_witness(registry) -> None:  # type: ignore[no-untyped-def]
    """403 challenge pages are in the index, and they are not bytes about the page.

    Several registry hosts serve a bot wall to crawlers, and the Archive faithfully
    stored it. Counting those as captures would size #78 against witnesses that,
    compared hash-for-hash, would report a confident disagreement where the truth is
    that no second witness exists.
    """
    first = registry.sources[0]
    answer = _rows(
        ("20260527184940", "200", "AAA"),
        ("20260816001410", "403", "BBB"),
        ("20260817001410", "302", "CCC"),
    )
    report = _report(registry, {first.id: answer})
    row = next(r for r in report.per_source if r.source_id == first.id)

    assert row.total_captures == 3
    assert row.usable_captures == 1
    assert row.distinct_usable_digests == 1
    assert row.first_usable == "2026-05-27"
    assert row.last_usable == "2026-05-27"


def test_distinct_digests_count_content_changes_not_visits(registry) -> None:  # type: ignore[no-untyped-def]
    first = registry.sources[0]
    answer = _rows(
        ("20240101000000", "200", "AAA"),
        ("20240201000000", "200", "AAA"),
        ("20240301000000", "200", "BBB"),
    )
    report = _report(registry, {first.id: answer})
    row = next(r for r in report.per_source if r.source_id == first.id)

    assert row.usable_captures == 3
    assert row.distinct_usable_digests == 2
    assert row.first_usable == "2024-01-01"
    assert row.last_usable == "2024-03-01"


def test_a_capped_read_is_flagged_and_rendered_as_a_lower_bound(registry) -> None:  # type: ignore[no-untyped-def]
    """Hitting the row limit means "at least this many", and the doc must say so.

    A capped count printed as a total is the same defect as a failed query printed as
    a zero, pointing the other way.
    """
    first = registry.sources[0]
    answer = {
        "ok": True,
        "limit": 3,
        "rows": [HEADER, *[[f"2024010{i}000000", "200", f"D{i}", "text/html"] for i in range(3)]],
    }
    report = _report(registry, {first.id: answer})
    row = next(r for r in report.per_source if r.source_id == first.id)

    assert row.truncated is True
    assert report.n_truncated == 1
    assert f"| `{first.id}` " in render_markdown(report)
    assert "≥3" in render_markdown(report)


def test_the_outcome_vocabulary_is_closed(registry) -> None:  # type: ignore[no-untyped-def]
    """Every row carries one of exactly three outcomes.

    A fourth would be a new fact about the world and should arrive with a test, not
    as a free-text label that quietly stops matching the aggregate counts.
    """
    assert OUTCOMES == (CAPTURED, NO_CAPTURE, QUERY_FAILED)
    report = _report(registry, {})
    assert {r.outcome for r in report.per_source} <= set(OUTCOMES)
    assert sum(report.counts_by_outcome.values()) == report.n_sources


def test_the_unfetchable_cross_tab_partitions_exactly(registry) -> None:  # type: ignore[no-untyped-def]
    """The number #78 turns on must add up, or it is decoration.

    Every source our own crawler cannot fetch falls into exactly one of: the Archive
    has usable captures, the Archive has none, or we could not ask.
    """
    report = _report(registry, {})
    tab = report.unfetchable_by_us
    assert tab["n_sources"] == sum(1 for s in registry.sources if not s.reachable)
    assert (
        tab["with_usable_captures"] + tab["no_usable_capture"] + tab["query_failed"]
        == tab["n_sources"]
    )


def test_the_report_round_trips_through_json(registry) -> None:  # type: ignore[no-untyped-def]
    first = registry.sources[0]
    report = _report(registry, {first.id: _rows(("20240101000000", "200", "AAA"))})
    payload = json.loads(report.to_json())

    assert payload["measured_on"] == "2026-09-07"
    assert payload["n_sources"] == len(registry.sources)
    assert len(payload["per_source"]) == len(registry.sources)
    failed = [r for r in payload["per_source"] if r["outcome"] == QUERY_FAILED]
    assert all(r["total_captures"] is None for r in failed)


def test_summarize_over_no_rows_reports_nothing_rather_than_zero() -> None:
    """An empty measurement must not render as a measured absence."""
    report = summarize([], measured_on="2026-09-07", row_limit=2000)
    assert report.n_sources == 0
    assert report.counts_by_outcome == dict.fromkeys(OUTCOMES, 0)
    assert report.unfetchable_by_us["n_sources"] == 0


def test_a_malformed_capture_timestamp_is_passed_through_not_mangled(registry) -> None:  # type: ignore[no-untyped-def]
    """A stamp the index did not format as expected must not become a plausible date.

    `20260527184940` -> `2026-05-27` is a slice, and a slice of anything else yields a
    date-shaped string that is not a date. Returning the raw value keeps a
    malformed input visible in the evidence file instead of laundering it into
    something a reader would trust.
    """
    first = registry.sources[0]
    report = _report(registry, {first.id: _rows(("-", "200", "AAA"))})
    row = next(r for r in report.per_source if r.source_id == first.id)

    assert row.first_usable == "-"
    assert row.last_usable == "-"


def test_churn_over_no_sources_is_undefined_not_zero() -> None:
    """A median over an empty set is not 0.0.

    0.0 here would read as "the archived bytes never change" — the exact opposite of
    "we have no sources to measure". It is NaN in the data and "not measurable" in
    the document, so neither can be mistaken for a finding.
    """
    report = summarize([], measured_on="2026-09-07", row_limit=2000)
    assert math.isnan(report.digest_churn["median_ratio"])
    assert report.digest_churn["n_sources"] == 0.0
    assert "not measurable" in render_markdown(report)


def test_thinly_captured_sources_are_excluded_from_the_churn_distribution(registry) -> None:  # type: ignore[no-untyped-def]
    """A ratio over three captures is noise, and diluting the median with it is worse
    than reporting a smaller sample. What was excluded is reported beside the median.
    """
    first, second = registry.sources[0], registry.sources[1]
    report = _report(
        registry,
        {
            # 3 captures, 3 digests: ratio 1.0, but far too few to mean anything.
            first.id: _rows(*[(f"2024010{i}000000", "200", f"D{i}") for i in range(3)]),
            # 12 captures, 6 digests: ratio 0.5, and enough to count.
            second.id: _rows(
                *[(f"20240{i:03d}000000", "200", f"E{(i - 101) // 2}") for i in range(101, 113)]
            ),
        },
    )
    assert report.digest_churn["min_usable_captures"] == 10.0
    assert report.digest_churn["n_sources"] == 1.0
    assert report.digest_churn["median_ratio"] == 0.5
    assert report.digest_churn["n_at_or_above_high_churn"] == 0.0


def test_a_page_that_rehashes_on_every_capture_is_counted_as_high_churn(registry) -> None:  # type: ignore[no-untyped-def]
    """The number the witness design turns on.

    A source whose archived bytes differ on every capture would make a raw-hash
    witness report `disagrees` almost always — noise, not corroboration.
    """
    first = registry.sources[0]
    report = _report(
        registry,
        {first.id: _rows(*[(f"20240{i:03d}000000", "200", f"D{i}") for i in range(101, 113)])},
    )
    assert report.digest_churn["n_sources"] == 1.0
    assert report.digest_churn["median_ratio"] == 1.0
    assert report.digest_churn["n_at_or_above_high_churn"] == 1.0
