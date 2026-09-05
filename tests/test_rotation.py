"""Tests for :mod:`id_churn_sentinel.core.rotation` — the slow-rotation detector.

Two families of test here, and the second matters more.

The first says the detector *finds* what it is for: a source whose most recent observations a
named human keeps dismissing as `editorial` is named, with its streak, its span, its change
ids and its reviewers.

The second says what it must never do. It must not suppress the source it names, it must not
touch the normalizer, and it must not classify anything — because each of those is a way to
turn a noise-reduction feature into the wrong "no change" the whole repository is organised
around. `test_a_flagged_source_is_still_watched_and_still_produces_change_records` is the
load-bearing one: if it ever fails, this module has started hiding pages.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from id_churn_sentinel.core.changes import ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.detect import _watch_authorized_sources as watch
from id_churn_sentinel.core.registry import Source
from id_churn_sentinel.core.rotation import ROTATION_THRESHOLD, rotation_report
from id_churn_sentinel.core.store import SnapshotStore

from .conftest import StubFetcher

MONDAY = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


def _observe(
    store: SnapshotStore,
    source: Source,
    *,
    week: int,
    previous: str,
    new: str,
) -> ChangeRecord:
    """Record one drift observation, a week apart from the last, the way `watch` would."""
    change = ChangeRecord.observed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        previous_hash=previous * 64,
        new_hash=new * 64,
        diff_excerpt=f"-state fish\n+state reptile ({week})",
        observed_at=MONDAY + timedelta(weeks=week),
    )
    store.record_change(change)
    return change


def _review(
    store: SnapshotStore,
    change: ChangeRecord,
    *,
    reviewer: str = "A Human",
    significance: Significance = Significance.EDITORIAL,
    status: ReviewStatus = ReviewStatus.DISMISSED,
) -> None:
    store.update_change(
        change.reviewed_by(
            reviewer=reviewer,
            significance=significance,
            status=status,
            reviewed_at=change.observed_at + timedelta(hours=2),
        )
    )


def _dismissed_weekly(store: SnapshotStore, source: Source, weeks: int, **review: object) -> None:
    for week in range(weeks):
        change = _observe(
            store, source, week=week, previous=str(week % 10), new=str((week + 1) % 10)
        )
        _review(store, change, **review)  # type: ignore[arg-type]


def test_repeated_editorial_dismissals_on_one_source_are_surfaced(
    store: SnapshotStore, source: Source
) -> None:
    """The issue, in one test. `--twice` cannot see a page that re-rolls a widget hourly, or
    one that starts rotating after registration; a reviewer closing the same source as
    `editorial` week after week can, and that signal used to live only in their memory."""
    _dismissed_weekly(store, source, 3)

    report = rotation_report(store)

    assert [suspect.source_id for suspect in report.suspects] == [source.id]
    suspect = report.suspects[0]
    assert suspect.streak == 3
    assert suspect.span == timedelta(weeks=2)
    assert suspect.url == source.url
    assert suspect.reviewers == ("A Human",)
    assert len(suspect.change_ids) == 3
    assert suspect.pending == 0
    assert "3 source(s) with any reviewed observation" not in report.summary()
    assert "1 source(s) with 2 or more consecutive editorial dismissals" in report.summary()


def test_a_single_editorial_dismissal_is_not_a_pattern(
    store: SnapshotStore, source: Source
) -> None:
    """One dismissal is a page being edited. The detector exists to find a *habit*, and
    naming a source on its first editorial dismissal would make the report the noise it is
    supposed to reduce."""
    _dismissed_weekly(store, source, 1)

    report = rotation_report(store)

    assert report.suspects == []
    assert report.reviewed_sources == 1
    assert "no source has 2 consecutive editorial dismissal(s)" in report.summary()


def test_a_confirmed_observation_breaks_the_streak(store: SnapshotStore, source: Source) -> None:
    """The most important thing that resets it. A human deciding the page really did move is
    the strongest evidence available that it is not merely churning, so the count starts
    again from there rather than accumulating across a genuine change."""
    _dismissed_weekly(store, source, 3)
    confirmed = _observe(store, source, week=3, previous="a", new="b")
    _review(
        store,
        confirmed,
        significance=Significance.SUBSTANTIVE,
        status=ReviewStatus.CONFIRMED,
    )

    assert rotation_report(store).suspects == []


def test_a_dismissal_left_unclassified_breaks_the_streak_and_the_blind_spot_is_stated(
    store: SnapshotStore, source: Source
) -> None:
    """A dismissal recorded as `unclassified` is a real decision, but it is not the decision
    this signal is made of. Counting it would widen a published safety vocabulary by
    inference. The cost is a stated blind spot: a reviewer who never classifies their
    dismissals never trips this detector."""
    _dismissed_weekly(store, source, 3, significance=Significance.UNCLASSIFIED)

    assert rotation_report(store).suspects == []


def test_an_unreviewed_observation_neither_extends_nor_breaks_the_streak(
    store: SnapshotStore, source: Source
) -> None:
    """An unreviewed record is not a decision. If a fresh alert reset the pattern, the signal
    would vanish exactly when the queue is backed up — which is when a reviewer most needs to
    be told they are rubber-stamping one source. It is counted separately and reported, so a
    streak read off an incomplete queue says that it is one."""
    _dismissed_weekly(store, source, 2)
    _observe(store, source, week=2, previous="c", new="d")  # left unreviewed

    suspect = rotation_report(store).suspects[0]

    assert suspect.streak == 2
    assert suspect.pending == 1


def test_distinct_reviewers_are_reported_because_they_are_the_stronger_signal(
    store: SnapshotStore, source: Source
) -> None:
    """ "Three dismissals by one reviewer" and "three dismissals by three reviewers" are
    different facts, and the second is harder to explain away as one tired person's habit."""
    for week, reviewer in enumerate(("Reviewer One", "Reviewer Two", "Reviewer One")):
        change = _observe(store, source, week=week, previous=str(week), new=str(week + 1))
        _review(store, change, reviewer=reviewer)

    suspect = rotation_report(store).suspects[0]

    assert suspect.reviewers == ("Reviewer One", "Reviewer Two")


def test_the_span_is_reported_so_a_count_is_never_mistaken_for_a_duration(
    store: SnapshotStore, source: Source
) -> None:
    """The lesson `REMOVAL_THRESHOLD` cost this repo, applied one constant over: three
    dismissals inside an afternoon of back-to-back runs and three over three weeks are
    different facts, and a report that shows only the count cannot tell them apart. The span
    is stated and deliberately NOT gated on — there is no evidence yet for what the right
    duration would be, and inventing one would repeat the mistake."""
    for week in range(3):
        change = ChangeRecord.observed(
            source_id=source.id,
            jurisdiction=source.jurisdiction,
            document_class=source.document_class,
            url=source.url,
            previous_hash=str(week) * 64,
            new_hash=str(week + 1) * 64,
            diff_excerpt="-a\n+b",
            observed_at=MONDAY + timedelta(minutes=20 * week),
        )
        store.record_change(change)
        _review(store, change)

    suspect = rotation_report(store).suspects[0]

    assert suspect.streak == 3
    assert suspect.span == timedelta(minutes=40)


def test_a_removal_escalation_is_not_folded_into_a_rotation_streak(
    store: SnapshotStore, source: Source
) -> None:
    """`possibly_removed` is an observation about silence, not about page content. Mixing it
    into a churn signal could only mislead in both directions at once."""
    escalation = ChangeRecord.possibly_removed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        last_known_hash="e" * 64,
        consecutive_failures=3,
        last_error="HTTP 403",
        observed_at=MONDAY,
    )
    store.record_change(escalation)
    _review(store, escalation)
    _dismissed_weekly(store, source, 1)

    assert rotation_report(store).suspects == []


def test_sources_are_reported_independently_and_ranked_by_streak(
    store: SnapshotStore, source: Source, arizona_source: Source
) -> None:
    _dismissed_weekly(store, source, 2)
    _dismissed_weekly(store, arizona_source, 4)

    report = rotation_report(store)

    assert [suspect.source_id for suspect in report.suspects] == [arizona_source.id, source.id]
    assert [suspect.streak for suspect in report.suspects] == [4, 2]


def test_the_threshold_is_settable_and_defaults_to_the_number_the_docs_commit_to(
    store: SnapshotStore, source: Source
) -> None:
    """The default is not invented here: RESPONSIBLE-TECH §A already says the other half of
    the false-drift signal is a reviewer dismissing the same source as editorial "twice
    running". An operator whose corpus says two is noisy can raise it, and the streak length
    is always printed, so the evidence outlives the constant either way."""
    assert ROTATION_THRESHOLD == 2
    _dismissed_weekly(store, source, 2)

    assert len(rotation_report(store).suspects) == 1
    assert rotation_report(store, threshold=3).suspects == []

    with pytest.raises(ValueError, match="below 1"):
        rotation_report(store, threshold=0)


def test_an_empty_store_reports_nothing_rather_than_failing(store: SnapshotStore) -> None:
    report = rotation_report(store)
    assert report.suspects == []
    assert report.reviewed_sources == 0


# ---------------------------------------------------------------------------------------
# What the detector must never do
# ---------------------------------------------------------------------------------------


def test_a_flagged_source_is_still_watched_and_still_produces_change_records(
    store: SnapshotStore, source: Source, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    """THE test in this file.

    The tempting next step from "this source keeps getting dismissed" is to stop alerting on
    it, or to weight it down, or to strip the passage that keeps moving. Every one of those
    hides a real policy change behind a heuristic — the wrong "no change" §9 of the roadmap
    puts first — and it would do so silently, on a page a reviewer has already been trained
    to ignore. So the detector reports and does nothing else: a flagged source is watched on
    exactly the same terms as any other, and the next real edit still reaches the queue."""
    _dismissed_weekly(store, source, 3)
    assert rotation_report(store).suspects  # the source is flagged

    watch([source], store, fetcher)  # baseline
    fetcher.set(source.url, fixture_after)
    report = watch([source], store, fetcher)

    assert len(report.changed) == 1
    assert "+a court order is required to change the sex field" in report.changed[0].diff_excerpt


def test_the_detector_writes_nothing(store: SnapshotStore, source: Source) -> None:
    """Read-only, asserted rather than asserted-in-a-docstring. A detector that could act on
    its own conclusion is a detector that could quietly stop watching a page; the correct
    response to a source that cannot be watched honestly is a *person* swapping the URL or
    recording a gap."""
    _dismissed_weekly(store, source, 3)
    before = [
        (change.id, change.review_status, change.significance, change.reviewer)
        for change in store.changes()
    ]

    rotation_report(store)

    assert [
        (change.id, change.review_status, change.significance, change.reviewer)
        for change in store.changes()
    ] == before


def test_the_detector_never_alters_a_records_significance_or_reviewer(
    store: SnapshotStore, source: Source
) -> None:
    """The `no-auto-classification` boundary, restated where this feature could erode it: the
    detector counts decisions humans already signed. It has no vocabulary to make one, and
    the records it read still carry their human's name and nothing else."""
    _dismissed_weekly(store, source, 2)

    rotation_report(store)

    for change in store.changes():
        assert change.reviewer == "A Human"
        assert change.significance is Significance.EDITORIAL
        assert change.review_status is ReviewStatus.DISMISSED
