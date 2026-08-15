"""Tests for :mod:`id_churn_sentinel.core.detect` — the watch loop.

Three of these tests encode disciplines, not behaviours:

* `test_a_fetch_failure_is_never_drift` — the rule inherited from source-watch.ts.
* `test_a_first_sighting_is_never_drift` — no baseline means nothing to compare.
* `test_cosmetic_churn_produces_no_change_record` — the normalizer, end to end.

And one encodes the differentiator: `test_drift_produces_the_passage_that_changed`.
"""

from __future__ import annotations

import hashlib
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
from id_churn_sentinel.core.normalize import (
    CURRENT_CONTRACT,
    EXTRACTOR_VERSION,
    NORMALIZER_VERSION,
    content_hash,
    normalize_html,
)
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
            normalizer_version=NORMALIZER_VERSION,
            extractor_version=EXTRACTOR_VERSION,
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


def test_a_watched_run_persists_complete_attempt_evidence(
    tmp_path: Path,
    source: Source,
    arizona_source: Source,
    fixture_before: bytes,
) -> None:
    """DATA-04, end to end through the production watcher: the attempt receipt records
    what the network did — distinct raw/normalized hashes, MIME, byte accounting, and the
    final URL for the success; a stable error class and *no fabricated hashes* for the
    outage. Read back through the store, not from the report in memory."""
    from datetime import date

    from id_churn_sentinel.core.detect import watch_registry
    from id_churn_sentinel.core.normalize import content_evidence
    from id_churn_sentinel.core.registry import Registry

    from .conftest import eligible_source

    registry = Registry(
        version="1.0",
        sources=(eligible_source(source), eligible_source(arizona_source)),
    )
    fetcher = StubFetcher({source.url: (fixture_before, "text/html")})  # Arizona fails
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    with SnapshotStore(tmp_path / "evidence.db") as store:
        report = watch_registry(
            registry,
            store,
            fetcher,
            as_of=date(2026, 7, 13),
            started_at=now,
            completed_at=now,
        )
        attempts = {attempt.source_id: attempt for attempt in store.fetch_attempts(report.run_id)}

    expected = content_evidence(fixture_before, "text/html")
    succeeded = attempts[source.id]
    assert succeeded.ok is True
    assert succeeded.raw_sha256 == expected.raw_sha256
    assert succeeded.normalized_sha256 == expected.normalized_sha256
    assert succeeded.raw_sha256 != succeeded.normalized_sha256
    assert succeeded.extraction_outcome == "text-normalized"
    assert succeeded.content_type == "text/html"
    assert succeeded.bytes_received == len(fixture_before)
    assert succeeded.final_url == source.url
    assert succeeded.redirect_chain == ()
    assert succeeded.truncated is False
    assert succeeded.error_class == ""

    failed = attempts[arizona_source.id]
    assert failed.ok is False
    assert failed.error_class == "unreachable"
    assert failed.raw_sha256 == ""
    assert failed.normalized_sha256 == ""
    assert failed.extraction_outcome == ""
    assert failed.bytes_received == 0


# -- the representation contract ---------------------------------------------------
#
# A hash means nothing except relative to the normalizer that produced it. These tests
# pin the three cases that matters for: a comparison inside one contract (must be
# untouched), a comparison across two (must not manufacture drift, and must not hide it),
# and the live v1→v2 transition an operator is walking into this week.

_V1_CONTRACT = "passage-text-v1/none-v1"

# The loosely-spelled tags `passage-text-v1` failed to match, as they appear in the fixture.
_V1_UNMATCHED_TAGS = ("<script >", "</script >")


def v1_normalized(body: bytes) -> str:
    """What `passage-text-v1` produced for these bytes.

    v1 is not in the tree — exactly one normalizer exists at a time, which is precisely why
    the retained *bytes*, and not an old code path, are what make an old baseline
    recoverable. So its defect is reproduced *by construction* rather than by keeping a
    second copy of the algorithm around to drift out of sync (or, worse, a second copy of
    the bad regex, which is a real finding wherever a scanner meets it).

    The construction is exact. v1's strip regex required the tight `</script>`, so on this
    page the element never matched and its body fell through to the generic tag-stripping
    step and became page text. Deleting only the unmatched tags and handing the rest to
    today's normalizer reaches the same place by the same route.
    """
    text = body.decode("utf-8", errors="replace")
    for tag in _V1_UNMATCHED_TAGS:
        text = text.replace(tag, " ")
    return normalize_html(text)


def record_v1_baseline(store: SnapshotStore, source: Source, body: bytes) -> str:
    """Write the snapshot row `passage-text-v1` would have written for `body`, and return
    the hash it recorded. The retained bytes are the real ones; only the label and the
    derived text/hash belong to the older contract, which is exactly the situation a store
    is in the week after a version bump."""
    text = v1_normalized(body)
    recorded = hashlib.sha256(text.encode("utf-8")).hexdigest()
    store.record_snapshot(
        source_id=source.id,
        url=source.url,
        fetched_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        http_status=200,
        content_sha256=recorded,
        raw_bytes=body,
        normalized_text=text,
        normalizer_version="passage-text-v1",
        extractor_version="none-v1",
    )
    return recorded


def test_the_fixture_really_does_normalize_differently_under_v1_and_v2(
    fixture_loose_end_tag: bytes,
) -> None:
    """Guard against a vacuous suite. Every cross-contract test below is only meaningful if
    v1 and v2 genuinely disagree about these bytes; if a future normalizer change made them
    agree, the tests would pass while testing nothing, and this is the assertion that fails
    instead."""
    v1_text = v1_normalized(fixture_loose_end_tag)
    v2_hash, v2_text = content_hash(fixture_loose_end_tag, "text/html")

    assert v1_text != v2_text
    assert hashlib.sha256(v1_text.encode("utf-8")).hexdigest() != v2_hash
    assert "__csrf__" in v1_text  # v1 hashed the script body as page text
    assert "__csrf__" not in v2_text  # v2 strips it


def test_a_same_contract_comparison_is_untouched_by_the_guard(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    """The overwhelmingly common path must be byte-identical to what it was. Nothing is
    re-derived, the recorded baseline hash is the one the change record carries, and the
    reviewer is told nothing about normalizers because nothing happened to them."""
    watch([source], store, fetcher)
    baseline = store.latest_snapshot(source.id)
    assert baseline is not None
    fetcher.set(source.url, fixture_after)

    report = watch([source], store, fetcher)

    assert report.renormalized == []
    assert report.unrenormalizable == []
    assert len(report.changed) == 1
    assert report.changed[0].previous_hash == baseline.content_sha256
    assert "re-derived" not in report.changed[0].diff_excerpt


def test_a_normalizer_version_bump_alone_is_never_reported_as_drift(
    source: Source, store: SnapshotStore, fixture_loose_end_tag: bytes
) -> None:
    """THE case this guard exists for. The page did not move; our normalizer did. The stored
    v1 hash does not equal today's hash for the very same bytes — which is exactly the trap:
    a hash comparison would report drift, and the diff handed to the reviewer would be an
    artifact the tool manufactured about itself."""
    recorded = record_v1_baseline(store, source, fixture_loose_end_tag)
    today, _ = content_hash(fixture_loose_end_tag, "text/html")
    assert recorded != today  # the trap, stated

    report = watch([source], store, StubFetcher({source.url: (fixture_loose_end_tag, "text/html")}))

    assert report.changed == []
    assert store.changes() == ()
    assert report.renormalized == [(source.id, _V1_CONTRACT, CURRENT_CONTRACT)]
    assert report.unchanged == []
    assert "normalizer version changed, not drift" in report.summary()


def test_the_pass_after_a_version_bump_is_an_ordinary_unchanged_pass(
    source: Source, store: SnapshotStore, fixture_loose_end_tag: bytes
) -> None:
    """The transition is a one-off, not a standing condition. The v1→v2 pass re-baselines
    onto v2, so the week after says `unchanged` with no explanation attached — an operator
    who reads the transition report once never has to read it again."""
    record_v1_baseline(store, source, fixture_loose_end_tag)
    fetcher = StubFetcher({source.url: (fixture_loose_end_tag, "text/html")})
    watch([source], store, fetcher)

    report = watch([source], store, fetcher)

    assert report.unchanged == [source.id]
    assert report.renormalized == []


def test_real_drift_during_a_version_bump_is_still_reported_and_diffed_like_for_like(
    source: Source, store: SnapshotStore, fixture_loose_end_tag: bytes
) -> None:
    """The failure mode of the rejected design. Refusing a cross-contract comparison would
    have swallowed this sentence for a whole pass — a wrong 'no change' about a government
    page, which is the one error this repo is organised around. It is reported, and the diff
    is a diff of *content*: the script body v1 leaked into its baseline text appears on
    neither side, because both sides came out of the same normalizer."""
    record_v1_baseline(store, source, fixture_loose_end_tag)
    changed_body = fixture_loose_end_tag.replace(
        b"<p>The fee for a corrected driver license is $11.</p>",
        b"<p>The fee for a corrected driver license is $11.</p>\n"
        b"<p>A court order is now required to change the name field.</p>",
    )
    expected_previous, _ = content_hash(fixture_loose_end_tag, "text/html")

    report = watch([source], store, StubFetcher({source.url: (changed_body, "text/html")}))

    assert report.renormalized == []
    assert len(report.changed) == 1
    change = report.changed[0]
    assert "+a court order is now required to change the name field." in change.diff_excerpt
    assert "__csrf__" not in change.diff_excerpt  # the v1 artifact is on neither side
    # The hash actually compared, not the one on the row — `previous_hash` and the diff have
    # to describe the same comparison.
    assert change.previous_hash == expected_previous
    assert "baseline re-derived from its retained bytes" in change.diff_excerpt
    assert _V1_CONTRACT in change.diff_excerpt


def test_a_whole_corpus_crosses_the_transition_in_one_pass_without_a_wall_of_alarms(
    source: Source,
    arizona_source: Source,
    federal_source: Source,
    store: SnapshotStore,
    fixture_loose_end_tag: bytes,
    fixture_after: bytes,
) -> None:
    """The v1→v2 pass over an existing corpus, which is the common case right now. Two pages
    are untouched and one really moved; the report says so in those terms rather than in
    three identical-looking alarms a tired reviewer has to triage one at a time."""
    sources = [source, arizona_source, federal_source]
    for entry in sources:
        record_v1_baseline(store, entry, fixture_loose_end_tag)

    report = watch(
        sources,
        store,
        StubFetcher(
            {
                source.url: (fixture_loose_end_tag, "text/html"),
                arizona_source.url: (fixture_loose_end_tag, "text/html"),
                federal_source.url: (fixture_after, "text/html"),
            }
        ),
    )

    assert [entry[0] for entry in report.renormalized] == [source.id, arizona_source.id]
    assert [change.source_id for change in report.changed] == [federal_source.id]
    assert "1 changed" in report.summary()
    assert "2 unchanged after re-deriving the baseline" in report.summary()


def test_a_baseline_that_cannot_be_re_derived_claims_no_drift_in_either_direction(
    source: Source, store: SnapshotStore, fixture_before: bytes
) -> None:
    """A snapshot that claims normalized text but retains no bytes to re-normalize cannot be
    restated under today's contract, and there is no honest hash to compare. Saying
    'unchanged' would be a wrong no-change; saying 'changed' would be drift invented out of
    a gap in our own evidence. It says neither, re-baselines, and names the contract."""
    store.record_snapshot(
        source_id=source.id,
        url=source.url,
        fetched_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        http_status=200,
        content_sha256="a" * 64,
        raw_bytes=b"",
        normalized_text="a passage we can no longer reproduce",
        normalizer_version="passage-text-v1",
        extractor_version="none-v1",
    )

    report = watch([source], store, StubFetcher({source.url: (fixture_before, "text/html")}))

    assert report.changed == []
    assert store.changes() == ()
    assert report.unchanged == []
    assert report.unrenormalizable == [(source.id, _V1_CONTRACT)]
    assert "no drift claimed" in report.summary()
    refreshed = store.latest_snapshot(source.id)
    assert refreshed is not None
    assert refreshed.normalizer_version == NORMALIZER_VERSION


def test_the_re_derivation_note_survives_a_truncated_diff() -> None:
    """The provenance of the left-hand side is not a detail that may be cut to fit a
    character budget: a reviewer who only reads the top of a long diff still learns that the
    baseline was re-derived."""
    excerpt = diff_excerpt(
        "\n".join(f"old passage {index}" for index in range(2000)),
        "\n".join(f"new passage {index}" for index in range(2000)),
        source_url="https://example.gov/x",
        renormalized_from=_V1_CONTRACT,
    )

    assert excerpt.startswith("(baseline re-derived from its retained bytes")
    assert "diff truncated at" in excerpt


# --- no extractable text is not a baseline, and never quietly "unchanged" (issue #19) --------
#
# The measured bug: a JS shell, an empty 200, and a bot-wall all normalize to zero passages,
# `sha256("")` is a real, stable hash, and the old detector had no concept that a comparison
# against "nothing" means nothing. A source that served no text was quietly baselined as `new`
# and then reported `unchanged` forever after — the loudest possible silence about a page
# nobody was actually watching.


def test_a_first_sighting_with_no_text_is_not_baselined(store: SnapshotStore) -> None:
    empty_source = Source(
        id="tx-js-shell",
        jurisdiction="TX",
        document_class="drivers_license",
        url="https://example.gov/js-shell",
        authority="Texas Department of Public Safety",
        verified=False,
        notes="test fixture",
    )
    body = (
        b'<html><head><script>var a = 1;</script></head><body><div id="root"></div></body></html>'
    )

    report = watch([empty_source], store, StubFetcher({empty_source.url: (body, "text/html")}))

    assert report.no_text == [(empty_source.id, empty_source.url)]
    assert report.new == []
    assert report.unchanged == []
    assert report.changed == []


def test_no_text_recurs_every_run_instead_of_settling_into_unchanged(
    source: Source, store: SnapshotStore
) -> None:
    """The heart of the bug. Two identical zero-passage fetches must not hash-match their way
    into `unchanged` — that silence is exactly what let a scrubbed government page sit
    unwatched for as long as it kept serving the same nothing."""
    fetcher = StubFetcher({source.url: (b"<html><body></body></html>", "text/html")})

    first = watch([source], store, fetcher)
    second = watch([source], store, fetcher)

    assert first.no_text == [(source.id, source.url)]
    assert second.no_text == [(source.id, source.url)]
    assert second.unchanged == []
    assert second.new == []


def test_a_transition_to_no_text_is_not_folded_into_a_change_record(
    source: Source, store: SnapshotStore, fixture_before: bytes
) -> None:
    """A page that had real content and now has none is not silently diffed away as one
    dismissible `changed` record either — it lands in the same loud, recurring bucket as a
    page that never had text, so a reviewer cannot review it once and lose it."""
    fetcher = StubFetcher({source.url: (fixture_before, "text/html")})
    watch([source], store, fetcher)
    fetcher.set(source.url, b"<html><body><script>x</script></body></html>")

    report = watch([source], store, fetcher)

    assert report.no_text == [(source.id, source.url)]
    assert report.changed == []


def test_two_different_no_text_sources_do_not_collide_into_one_bucket_entry(
    source: Source, arizona_source: Source, store: SnapshotStore
) -> None:
    """Both normalize to the same sha256("") hash; the bucket must still name each source."""
    fetcher = StubFetcher(
        {
            source.url: (b"<html><body></body></html>", "text/html"),
            arizona_source.url: (b"<html><head><script>x</script></head></html>", "text/html"),
        }
    )

    report = watch([source, arizona_source], store, fetcher)

    assert {source_id for source_id, _ in report.no_text} == {source.id, arizona_source.id}


def test_binary_content_with_no_normalized_text_is_not_flagged(
    source: Source, store: SnapshotStore
) -> None:
    """Opaque bytes normalize to an empty string by design (`extractor_version = "none-v1"`,
    no PDF extractor). That is documented, honest behaviour — content_hash covers the raw
    bytes — and must not trip the same guard as a page that promised text and had none."""
    report = watch(
        [source],
        store,
        StubFetcher({source.url: (b"%PDF-1.4 not real pdf bytes", "application/pdf")}),
    )

    assert report.no_text == []
    assert report.new == [source.id]


def test_no_text_is_reported_loudly_in_the_summary(source: Source, store: SnapshotStore) -> None:
    report = watch([source], store, StubFetcher({source.url: (b"<html></html>", "text/html")}))

    assert "1 served NO extractable text" in report.summary()
    assert "no drift claimed" in report.summary()


# --- and the same discipline in the STORE, not only in the report (issue #19) ----------------
#
# The bucket above was the first half of the fix and it was not the half that mattered. The
# empty fetch was still written to the snapshot table before being routed, so `sha256("")`
# became the source's latest snapshot — which is to say its baseline: the row
# `sentinel baseline write` commits, and the row next week's fetch is compared against. These
# tests assert the absence of that row, and the absence of everything it caused.


def test_a_no_text_fetch_writes_no_snapshot_at_all(source: Source, store: SnapshotStore) -> None:
    """A first sighting with no text leaves the store exactly as empty as it found it.

    The sibling test `test_a_first_sighting_is_never_drift` asserts a snapshot IS written for a
    real page; this one asserts the opposite for a page with nothing in it, because "not
    reported as a baseline" and "not stored as one" are different claims and only the second
    one survives contact with `baseline write`."""
    body = (
        b'<html><head><script>var a = 1;</script></head><body><div id="root"></div></body></html>'
    )

    report = watch([source], store, StubFetcher({source.url: (body, "text/html")}))

    assert report.no_text == [(source.id, source.url)]
    assert store.latest_snapshot(source.id) is None
    assert store.snapshots(source.id) == ()


def test_no_stored_snapshot_ever_holds_the_hash_of_nothing(
    source: Source, arizona_source: Source, store: SnapshotStore, fixture_before: bytes
) -> None:
    """The property, stated over the whole store rather than one source.

    `sha256("")` is what every blind page hashes to, so one such row is enough to make two
    unrelated sources look identical to each other and stable over time. No run, on any mix of
    sources, may leave one behind."""
    empty_hash = hashlib.sha256(b"").hexdigest()
    fetcher = StubFetcher(
        {
            source.url: (fixture_before, "text/html"),
            arizona_source.url: (b"<html><body><script>x</script></body></html>", "text/html"),
        }
    )

    watch([source, arizona_source], store, fetcher)
    watch([source, arizona_source], store, fetcher)

    stored = [
        snapshot.content_sha256
        for candidate in (source, arizona_source)
        for snapshot in store.snapshots(candidate.id)
    ]
    assert stored, "the readable source must still be baselined"
    assert empty_hash not in stored


def test_a_page_that_goes_blank_does_not_overwrite_its_real_baseline(
    source: Source, store: SnapshotStore, fixture_before: bytes
) -> None:
    """The destructive half of the bug. A page that had text and now serves none must not
    replace the last thing we could actually read — that snapshot is the evidence a diff is
    reproduced from, and five blind runs would otherwise evict it through retention."""
    fetcher = StubFetcher({source.url: (fixture_before, "text/html")})
    watch([source], store, fetcher)
    baseline = store.latest_snapshot(source.id)
    assert baseline is not None
    fetcher.set(source.url, b"<html><body><script>x</script></body></html>")

    for _ in range(6):  # more than DEFAULT_SNAPSHOT_RETENTION
        watch([source], store, fetcher)

    held = store.latest_snapshot(source.id)
    assert held is not None
    assert held.content_sha256 == baseline.content_sha256
    assert held.normalized_text == baseline.normalized_text
    assert held.raw_bytes == fixture_before


def test_a_page_that_recovers_unchanged_text_is_not_reported_as_drift(
    source: Source, store: SnapshotStore, fixture_before: bytes
) -> None:
    """The manufactured-drift half. Blind week, then the same page comes back exactly as it
    was: the honest answer is `unchanged`. Comparing against the hash of nothing instead
    reported a change record whose diff claimed the entire page had just been added — drift
    minted by the tool out of its own blindness, handed to a reviewer as a finding."""
    fetcher = StubFetcher({source.url: (fixture_before, "text/html")})
    watch([source], store, fetcher)
    fetcher.set(source.url, b"<html><body><script>x</script></body></html>")
    watch([source], store, fetcher)
    fetcher.set(source.url, fixture_before)

    report = watch([source], store, fetcher)

    assert report.changed == []
    assert store.changes() == ()
    assert report.unchanged == [source.id]


def test_a_no_text_fetch_does_not_clear_the_failure_streak(
    source: Source, store: SnapshotStore
) -> None:
    """`record_success` means "the source answered, so whatever was wrong is over". A bot-wall
    has not answered in any sense this tool cares about, and exonerating it would hand a
    permanently blind source a clean health record."""
    watch([source], store, StubFetcher())  # an outage: streak 1
    assert store.failure_streak(source.id) == 1

    report = watch(
        [source], store, StubFetcher({source.url: (b"<html><body></body></html>", "text/html")})
    )

    assert report.no_text == [(source.id, source.url)]
    assert store.failure_streak(source.id) == 1
