"""Tests for the outage-vs-removal distinction (docs/ROADMAP.md M3).

The gap this closes: a fetch failure correctly carries the previous hash forward, and a
page that has been **taken down** produced *exactly the same behaviour as a brief outage,
forever*. The tool held a dead page's baseline indefinitely and said nothing. That is a
wrong "no change", which `docs/RESPONSIBLE-TECH-AUDITS.md` §A names as the primary safety
failure of this repo: a government page about trans identity documents disappearing is
itself a policy signal, and answering a long silence with silence is the wrong response.

What must remain true, and is asserted here at every turn:

* a failure is still never *drift* — no content change record, at any streak length;
* the baseline is still held, and still not overwritten;
* the escalation is still `unclassified` / `unreviewed` / unpublishable;
* the escalation carries the *literal* error, because a 404, a 403 and a fortnight of
  timeouts are three different worlds and the machine does not get to choose between them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from id_churn_sentinel.core.changes import ChangeKind, ReviewStatus, Significance
from id_churn_sentinel.core.detect import (
    REMOVAL_THRESHOLD,
)
from id_churn_sentinel.core.detect import (
    _watch_authorized_sources as watch,
)
from id_churn_sentinel.core.fetch import FetchResult
from id_churn_sentinel.core.registry import Source
from id_churn_sentinel.core.store import SnapshotStore

from .conftest import StubFetcher


class FailingFetcher:
    """A fetcher that always fails, with a caller-chosen error and status — so a test can
    model a 404 (a page that is gone) and a 403 (a page that is fine and hates us) as the
    genuinely different things they are."""

    def __init__(self, error: str = "HTTP 404", status: int | None = 404) -> None:
        self.error = error
        self.status = status

    def fetch(self, url: str) -> FetchResult:
        return FetchResult.failure(url, self.error, status=self.status)


def baseline(source: Source, store: SnapshotStore, fetcher: StubFetcher) -> None:
    """Give the source one successful fetch, so it has a baseline to lose."""
    watch([source], store, fetcher)


# -- the streak ------------------------------------------------------------------


def test_a_failure_streak_is_persisted_and_counted(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    baseline(source, store, fetcher)
    assert store.failure_streak(source.id) == 0

    dead = FailingFetcher()
    for expected in (1, 2):
        watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)
        assert store.failure_streak(source.id) == expected


def test_below_the_threshold_nothing_escalates(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """Two weeks of a flaky government server is a flaky government server."""
    baseline(source, store, fetcher)
    dead = FailingFetcher()

    for _ in range(REMOVAL_THRESHOLD - 1):
        report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    assert report.possibly_removed == []
    assert report.unreachable == [(source.id, "HTTP 404")]
    assert store.changes() == ()


def test_crossing_the_threshold_escalates_to_possibly_removed(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """THE M3 FIX. After N consecutive failures the tool stops answering silence with
    silence and puts the source in front of a human."""
    baseline(source, store, fetcher)
    dead = FailingFetcher()

    for _ in range(REMOVAL_THRESHOLD):
        report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    assert len(report.possibly_removed) == 1
    escalation = report.possibly_removed[0]
    assert escalation.kind is ChangeKind.POSSIBLY_REMOVED
    assert escalation.source_id == source.id
    assert escalation.jurisdiction == "TX"

    stored = store.changes()
    assert len(stored) == 1
    assert stored[0].kind is ChangeKind.POSSIBLY_REMOVED


def test_a_success_resets_the_streak(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """A source that is serving bytes again is not a source that was taken down. Old
    flakiness must never accumulate into an escalation of a healthy page."""
    baseline(source, store, fetcher)
    dead = FailingFetcher()

    for _ in range(REMOVAL_THRESHOLD - 1):
        watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)
    assert store.failure_streak(source.id) == REMOVAL_THRESHOLD - 1

    watch([source], store, fetcher)  # it answers again
    assert store.failure_streak(source.id) == 0

    # ...and the next failure starts from one, so it does not immediately escalate.
    report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)
    assert store.failure_streak(source.id) == 1
    assert report.possibly_removed == []


def test_the_streak_survives_a_restart(
    tmp_path: Path, source: Source, fetcher: StubFetcher
) -> None:
    """The store is the memory. A weekly cron job is a *new process every week* — a streak
    held only in RAM would reset on every run and could never reach any threshold at all,
    which would make the whole mechanism a no-op that still passed its unit tests."""
    db = tmp_path / "s.db"
    dead = FailingFetcher()

    with SnapshotStore(db) as store:
        baseline(source, store, fetcher)
        watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    with SnapshotStore(db) as reopened:
        assert reopened.failure_streak(source.id) == 1
        watch([source], store=reopened, fetcher=dead, removal_threshold=REMOVAL_THRESHOLD)
        assert reopened.failure_streak(source.id) == 2


# -- the rules that must survive the new behaviour --------------------------------


def test_an_escalation_is_never_a_content_change(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """THE RULE, undamaged. A fetch failure is never drift — not on the first failure, and
    not on the hundredth. No amount of unreachability may manufacture a content change."""
    baseline(source, store, fetcher)
    dead = FailingFetcher()

    for _ in range(REMOVAL_THRESHOLD * 3):
        report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)
        assert report.changed == []

    assert all(c.kind is ChangeKind.POSSIBLY_REMOVED for c in store.changes())


def test_the_baseline_is_still_held_through_an_escalation(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """Escalating must not disturb the snapshot store. The last good bytes stay the
    baseline, so if the source comes back changed, the diff is still against real content."""
    baseline(source, store, fetcher)
    before = store.latest_snapshot(source.id)
    assert before is not None

    dead = FailingFetcher()
    for _ in range(REMOVAL_THRESHOLD):
        watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    after = store.latest_snapshot(source.id)
    assert after is not None
    assert after.content_sha256 == before.content_sha256
    assert len(store.snapshots(source.id)) == 1  # no snapshot written for a failed fetch


def test_recovery_after_an_escalation_still_diffs_against_the_old_baseline(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    """The payoff of holding the baseline: a page that vanishes for a month and comes back
    *rewritten* still produces a real diff, not a 'new baseline' shrug that hides the edit."""
    baseline(source, store, fetcher)
    dead = FailingFetcher()
    for _ in range(REMOVAL_THRESHOLD):
        watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    fetcher.set(source.url, fixture_after)
    report = watch([source], store, fetcher, removal_threshold=REMOVAL_THRESHOLD)

    assert len(report.changed) == 1
    assert "+a court order is required to change the sex field" in report.changed[0].diff_excerpt
    assert store.failure_streak(source.id) == 0


def test_a_source_with_no_baseline_never_escalates(source: Source, store: SnapshotStore) -> None:
    """A URL that has NEVER been fetched has no baseline to have lost. Escalating it would
    claim a page 'possibly disappeared' when we never once saw it — that is a bad registry
    entry (or a host that blocks us), and it belongs in `sources check`, not in a change
    record asserting something vanished."""
    dead = FailingFetcher()

    for _ in range(REMOVAL_THRESHOLD * 2):
        report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    assert report.possibly_removed == []
    assert store.changes() == ()
    assert store.failure_streak(source.id) == REMOVAL_THRESHOLD * 2  # still counted


def test_a_persistent_outage_does_not_spam_the_review_queue(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """A source unreachable for a year must not mint fifty-two identical alarms. The id is
    deterministic in (source, last-known-hash, ''), so the same condition re-derives the
    same record — and `ON CONFLICT DO NOTHING` means a human's review of it survives."""
    baseline(source, store, fetcher)
    dead = FailingFetcher()

    for _ in range(REMOVAL_THRESHOLD + 10):
        watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    assert len(store.changes()) == 1


def test_a_reviewed_escalation_is_not_reopened_by_further_failures(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """A human who has already looked at a WAF block and dismissed it must not have it
    re-raised at them every single week. That is how a reviewer learns to ignore the queue."""
    baseline(source, store, fetcher)
    dead = FailingFetcher(error="HTTP 403", status=403)
    for _ in range(REMOVAL_THRESHOLD):
        report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    escalation = report.possibly_removed[0]
    store.update_change(
        escalation.reviewed_by(
            reviewer="A Human",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.DISMISSED,
            note="Known WAF block, not a removal. The page is fine.",
        )
    )

    watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    stored = store.get_change(escalation.id)
    assert stored.review_status is ReviewStatus.DISMISSED
    assert stored.reviewer == "A Human"


# -- the excerpt: observation, not conclusion -------------------------------------


@pytest.mark.parametrize(
    ("error", "status"),
    [("HTTP 404", 404), ("HTTP 403", 403), ("unreachable: The read operation timed out", None)],
)
def test_the_escalation_carries_the_literal_error_not_an_interpretation(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, error: str, status: int | None
) -> None:
    """The whole point of handing over the raw error. A 404 (the page is gone), a 403 (a WAF
    hates us — ssa.gov and nycourts.gov really do this) and a timeout (Illinois SOS really
    does this) are three different worlds, and the reviewer cannot tell them apart unless we
    hand them the string verbatim. So we do, and we refuse to interpret it for them."""
    baseline(source, store, fetcher)
    dead = FailingFetcher(error=error, status=status)

    for _ in range(REMOVAL_THRESHOLD):
        report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    excerpt = report.possibly_removed[0].diff_excerpt
    assert error in excerpt
    assert f"Consecutive failed fetches: {REMOVAL_THRESHOLD}" in excerpt
    assert source.url in excerpt

    # It names all three readings and picks none of them.
    assert "REMOVED" in excerpt
    assert "BLOCKED" in excerpt
    assert "DOWN" in excerpt
    assert "The tool will not decide for you." in excerpt

    # And it explicitly disclaims the two things a machine must not say.
    assert "NOT a detected content change" in excerpt
    assert "NOT an assertion that the page was taken down" in excerpt


def test_an_escalation_reports_no_new_hash_rather_than_inventing_one(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """There is no new content — that is the entire observation. Minting a hash for bytes we
    never received would be a lie the rest of the pipeline would faithfully propagate."""
    baseline(source, store, fetcher)
    previous = store.latest_snapshot(source.id)
    assert previous is not None

    dead = FailingFetcher()
    for _ in range(REMOVAL_THRESHOLD):
        report = watch([source], store, dead, removal_threshold=REMOVAL_THRESHOLD)

    escalation = report.possibly_removed[0]
    assert escalation.new_hash == ""
    assert escalation.previous_hash == previous.content_sha256


def test_the_threshold_is_configurable(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    baseline(source, store, fetcher)

    report = watch([source], store, FailingFetcher(), removal_threshold=1)

    assert len(report.possibly_removed) == 1
