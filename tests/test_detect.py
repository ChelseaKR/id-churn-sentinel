"""Tests for :mod:`id_churn_sentinel.core.detect` — the watch loop.

Three of these tests encode disciplines, not behaviours:

* `test_a_fetch_failure_is_never_drift` — the rule inherited from source-watch.ts.
* `test_a_first_sighting_is_never_drift` — no baseline means nothing to compare.
* `test_cosmetic_churn_produces_no_change_record` — the normalizer, end to end.

And one encodes the differentiator: `test_drift_produces_the_passage_that_changed`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from id_churn_sentinel.core.changes import ReviewStatus, Significance
from id_churn_sentinel.core.detect import (
    MAX_DIFF_EXCERPT_CHARS,
    check_stability,
    diff_excerpt,
)
from id_churn_sentinel.core.detect import (
    _watch_authorized_sources as watch,
)
from id_churn_sentinel.core.fetch import FetchResult
from id_churn_sentinel.core.registry import Source
from id_churn_sentinel.core.store import SnapshotStore

from .conftest import StubFetcher


def test_a_first_sighting_is_never_drift(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    report = watch([source], store, fetcher)

    assert report.new == [source.id]
    assert report.changed == []
    assert store.changes() == ()
    assert store.latest_snapshot(source.id) is not None


def test_unchanged_content_produces_no_change_record(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    watch([source], store, fetcher)
    report = watch([source], store, fetcher)

    assert report.unchanged == [source.id]
    assert report.changed == []
    assert store.changes() == ()


def test_cosmetic_churn_produces_no_change_record(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_cosmetic: bytes
) -> None:
    """End-to-end proof that a re-minified stylesheet and a rotated token do not wake a
    human at 2am. This is what separates a watcher people keep from one they mute."""
    watch([source], store, fetcher)
    fetcher.set(source.url, fixture_cosmetic)

    report = watch([source], store, fetcher)

    assert report.unchanged == [source.id]
    assert store.changes() == ()


def test_drift_produces_the_passage_that_changed(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    """THE differentiator. The prior art says 'something changed at this URL'. This says
    *what*, in a form a reviewer can act on in thirty seconds."""
    watch([source], store, fetcher)
    fetcher.set(source.url, fixture_after)

    report = watch([source], store, fetcher)

    assert len(report.changed) == 1
    change = report.changed[0]
    assert change.jurisdiction == "TX"
    assert change.document_class == "drivers_license"
    assert change.url == source.url
    assert change.previous_hash != change.new_hash

    # The changed passage, and only the changed passage, is marked as an addition.
    assert "+a court order is required to change the sex field" in change.diff_excerpt
    assert "bring a certified copy of your court order" in change.diff_excerpt  # context
    assert "+applications are processed" not in change.diff_excerpt  # unchanged text


def test_a_fetch_failure_is_never_drift(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """THE RULE, inherited verbatim from trans-docs-navigator/scripts/source-watch.ts:
    "keep the old baseline; an outage is not a content change."

    A 503, a WAF block, a timeout — a state's website falling over is not a state changing
    its policy. If this test ever fails, the tool starts manufacturing legal changes out of
    server outages, and the people who trust it get hurt.
    """
    watch([source], store, fetcher)
    baseline = store.latest_snapshot(source.id)
    assert baseline is not None

    outage = StubFetcher({})  # every URL fails
    report = watch([source], store, outage)

    assert report.unreachable == [(source.id, "stubbed outage: no response configured")]
    assert report.changed == []
    assert store.changes() == ()

    # The baseline is untouched: no snapshot was written, so the previous hash still stands.
    after = store.latest_snapshot(source.id)
    assert after is not None
    assert after.content_sha256 == baseline.content_sha256
    assert len(store.snapshots(source.id)) == 1


def test_recovery_after_an_outage_diffs_against_the_pre_outage_baseline(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    """The corollary of the rule: because the outage wrote nothing, the *next successful*
    fetch compares against the last real content — so a change that happened during the
    outage is still caught, not swallowed."""
    watch([source], store, fetcher)
    watch([source], store, StubFetcher({}))  # outage
    fetcher.set(source.url, fixture_after)

    report = watch([source], store, fetcher)

    assert len(report.changed) == 1
    assert "+a court order is required" in report.changed[0].diff_excerpt


def test_detected_changes_are_always_unclassified_and_unreviewed(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    watch([source], store, fetcher)
    fetcher.set(source.url, fixture_after)
    report = watch([source], store, fetcher)

    change = report.changed[0]
    assert change.significance is Significance.UNCLASSIFIED
    assert change.review_status is ReviewStatus.UNREVIEWED
    assert change.reviewer is None
    assert not change.publishable


def test_rewatching_the_same_drift_does_not_duplicate_or_un_review_it(
    tmp_path: Path, source: Source, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    """Change ids are deterministic in (source, before, after). A re-run must not duplicate
    a change a human already reviewed — and must never overwrite the review with a fresh
    `unreviewed` record. A watcher that silently un-reviews its own queue teaches its
    reviewer to distrust it."""
    db = tmp_path / "s.db"
    with SnapshotStore(db) as store:
        watch([source], store, fetcher)
        fetcher.set(source.url, fixture_after)
        change = watch([source], store, fetcher).changed[0]
        store.update_change(
            change.reviewed_by(
                reviewer="A Human",
                significance=Significance.SUBSTANTIVE,
                status=ReviewStatus.CONFIRMED,
            )
        )

    # Re-detect the identical transition by rolling the store's view back to `before`.
    with SnapshotStore(db) as store:
        store.record_snapshot(
            source_id=source.id,
            url=source.url,
            fetched_at=change.observed_at,
            http_status=200,
            content_sha256=change.previous_hash,
            raw_bytes=b"",
            normalized_text="",
        )
        report = watch([source], store, fetcher)

        assert len(report.changed) == 1
        assert report.changed[0].id == change.id  # same id: deterministic
        assert len(store.changes()) == 1  # not duplicated
        stored = store.get_change(change.id)
        assert stored.review_status is ReviewStatus.CONFIRMED  # review survived
        assert stored.reviewer == "A Human"


def test_snapshots_are_retained_so_a_diff_is_reproducible(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    watch([source], store, fetcher)
    fetcher.set(source.url, fixture_after)
    watch([source], store, fetcher)

    snapshots = store.snapshots(source.id)
    assert len(snapshots) == 2
    assert snapshots[0].raw_bytes == fixture_after  # the bytes, not just the hash
    assert snapshots[0].http_status == 200


def test_binary_drift_is_reported_honestly_as_undiffable(
    source: Source, store: SnapshotStore
) -> None:
    """A PDF changed. We say so, and we say we cannot diff it — rather than emitting an
    empty diff a reviewer might read as 'nothing important changed'."""
    pdf = StubFetcher({source.url: (b"%PDF-1.7 v1", "application/pdf")})
    watch([source], store, pdf)
    pdf.set(source.url, b"%PDF-1.7 v2", "application/pdf")

    report = watch([source], store, pdf)

    assert len(report.changed) == 1
    assert "binary document" in report.changed[0].diff_excerpt
    assert source.url in report.changed[0].diff_excerpt


def test_diff_excerpt_is_truncated_but_says_so() -> None:
    before = "\n".join(f"line {i}" for i in range(2000))
    after = "\n".join(f"changed {i}" for i in range(2000))

    excerpt = diff_excerpt(before, after, source_url="https://ex.gov/p")

    assert len(excerpt) < MAX_DIFF_EXCERPT_CHARS + 200
    assert "truncated" in excerpt


def test_hash_change_with_no_text_change_is_reported_honestly() -> None:
    """Defensive: if a hash moves but the normalized text is identical, say that plainly
    instead of publishing an empty diff."""
    excerpt = diff_excerpt("same text", "same text", source_url="https://ex.gov/p")
    assert "markup or in non-text bytes" in excerpt


def test_watch_report_summary_counts_every_bucket(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    report = watch([source], store, fetcher)
    assert report.total == 1
    assert "1 source(s)" in report.summary()
    assert "unreachable (not drift)" in report.summary()


class RotatingFetcher:
    """A source that re-rolls a widget on every request — a real pattern, not a hypothetical.

    `dpbh.nv.gov` renders a rotating "Nevada state symbol" fun fact into its footer and
    re-rolls it on every single fetch, so its normalized hash is different every time it is
    asked. Modelled here so the false-drift detector is tested against the shape of the
    thing that actually caught us.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.calls = 0

    def fetch(self, url: str) -> FetchResult:
        self.calls += 1
        body = f"<p>Apply for a licence.</p><aside>State fish #{self.calls}</aside>".encode()
        return FetchResult(
            url=url,
            ok=True,
            status=200,
            content_type="text/html",
            body=body,
            fetched_at=datetime.now(UTC),
        )


def test_check_stability_catches_a_page_that_rotates_on_every_request(source: Source) -> None:
    """The finding that made this function exist: a page whose visible text re-rolls per
    request would mint a change record every week forever, and the normalizer cannot save
    us — the rotating text is real text."""
    report = check_stability([source], RotatingFetcher(source.url))

    assert report.stable == []
    assert len(report.unstable) == 1
    source_id, first, second = report.unstable[0]
    assert source_id == source.id
    assert first != second
    assert "UNSTABLE" in report.summary()


def test_check_stability_passes_a_stable_page(source: Source, fetcher: StubFetcher) -> None:
    report = check_stability([source], fetcher)

    assert report.stable == [source.id]
    assert report.unstable == []
    assert fetcher.calls == [source.url, source.url]  # twice, deliberately


def test_check_stability_reports_an_unreachable_source_without_calling_it_unstable(
    source: Source,
) -> None:
    """An outage is not instability, exactly as an outage is not drift. A source we could
    not fetch has told us nothing about whether it rotates."""
    report = check_stability([source], StubFetcher())

    assert report.unreachable == [(source.id, "stubbed outage: no response configured")]
    assert report.unstable == []
    assert report.stable == []


def test_a_corrected_registry_url_re_baselines_rather_than_manufacturing_drift(
    source: Source, store: SnapshotStore, fixture_before: bytes, fixture_after: bytes
) -> None:
    """A maintainer swapping a landing page for a deep link must not produce a change
    record. The stored baseline belongs to a *different page*; subtracting one document
    from an unrelated one is not drift detection, and the resulting diff would be
    unreviewable noise asserting that the source changed when what changed is which page
    we watch."""
    watch([source], store, StubFetcher({source.url: (fixture_before, "text/html")}))

    corrected = replace(source, url="https://www.dps.texas.gov/section/driver-license/deeper")
    report = watch([corrected], store, StubFetcher({corrected.url: (fixture_after, "text/html")}))

    assert report.changed == []
    assert store.changes() == ()
    assert report.rebaselined == [(source.id, source.url, corrected.url)]
    assert "re-baselined (registry URL changed)" in report.summary()
    assert store.latest_snapshot(source.id) is not None
    assert store.latest_snapshot(source.id).url == corrected.url  # type: ignore[union-attr]
