"""MERGE-BLOCKING GATE: the tool never classifies a change as substantive. A human does.

`make no-auto-classification` runs exactly this file.

This is the gate that matters most in this repo. Everything else here is a plumbing bug if
it breaks; *this* is a safety failure. A machine that announces "Texas substantively changed
its gender-marker policy" on the strength of a sha256 comparison will be believed — by legal
aid orgs, by A4TE, by a person deciding whether it is safe to travel — and it will sometimes
be wrong, because a hash comparison cannot read law. The tool's job ends at "these bytes
changed, here are the passages." A person takes it from there.

The invariant is enforced in four independent places, and this file proves each one:

    1. detection has no vocabulary to classify (`ChangeRecord.observed` takes no
       significance argument)
    2. `reviewed_by` refuses an unnamed reviewer
    3. the SQL schema REJECTS a classified row with no reviewer
    4. the CLI requires `--reviewer`
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from id_churn_sentinel.cli import build_parser, main
from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.detect import REMOVAL_THRESHOLD, watch
from id_churn_sentinel.core.fetch import FetchResult
from id_churn_sentinel.core.registry import Source
from id_churn_sentinel.core.store import SnapshotStore
from id_churn_sentinel.errors import ReviewError, StoreError

from .conftest import StubFetcher

pytestmark = pytest.mark.no_auto_classification


def test_detection_cannot_express_a_classification() -> None:
    """Layer 1, at the type level. `ChangeRecord.observed` — the *only* constructor the
    detector uses — does not accept `significance` or `review_status` at all. "The tool
    auto-flagged it as substantive" is not a bug a careless caller can introduce; it is a
    sentence that cannot be typed."""
    parameters = inspect.signature(ChangeRecord.observed).parameters
    assert "significance" not in parameters
    assert "review_status" not in parameters
    assert "reviewer" not in parameters


def test_the_removal_escalation_cannot_express_a_classification_either() -> None:
    """Layer 1, extended to the M3 escalation path.

    `possibly_removed` is the one new way this tool can mint a change record, and it is the
    most tempting place in the codebase to sneak a judgment in: a page 404ing for a month
    really does *look* substantive. It is not the tool's call. Like `observed`, the
    constructor is given no vocabulary to classify — so "the tool decided Texas took its
    gender-marker page down" remains a sentence that cannot be typed.
    """
    parameters = inspect.signature(ChangeRecord.possibly_removed).parameters
    assert "significance" not in parameters
    assert "review_status" not in parameters
    assert "reviewer" not in parameters


def test_watch_never_classifies_an_unreachable_source_however_long_it_stays_down(
    source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    """Layer 1, end to end, on the escalation path. Run the real detector against a source
    that 404s forever. It escalates — and everything it produces is still unclassified,
    unreviewed, unsigned and unpublishable."""

    class Gone:
        def fetch(self, url: str) -> FetchResult:
            return FetchResult.failure(url, "HTTP 404", status=404)

    watch([source], store, fetcher)  # a baseline to lose
    for _ in range(REMOVAL_THRESHOLD * 2):
        report = watch([source], store, Gone(), removal_threshold=REMOVAL_THRESHOLD)

    assert report.possibly_removed, "the source must actually escalate, or this proves nothing"
    for change in report.possibly_removed:
        assert change.kind is ChangeKind.POSSIBLY_REMOVED
        assert change.significance is Significance.UNCLASSIFIED
        assert change.review_status is ReviewStatus.UNREVIEWED
        assert change.reviewer is None
        assert not change.publishable

    for stored in store.changes():
        assert stored.significance is Significance.UNCLASSIFIED
        assert stored.reviewer is None


def test_watch_never_emits_a_classified_change(
    source: Source, store: SnapshotStore, fetcher: StubFetcher, fixture_after: bytes
) -> None:
    """Layer 1, end to end. Run the real detector over real drift; everything it produces
    is unclassified and unreviewed."""
    watch([source], store, fetcher)
    fetcher.set(source.url, fixture_after)
    report = watch([source], store, fetcher)

    assert report.changed, "the fixture must actually drift, or this test proves nothing"
    for change in report.changed:
        assert change.significance is Significance.UNCLASSIFIED
        assert change.review_status is ReviewStatus.UNREVIEWED
        assert change.reviewer is None
        assert not change.publishable

    for stored in store.changes():
        assert stored.significance is Significance.UNCLASSIFIED
        assert stored.reviewer is None


def test_classification_requires_a_named_human(observed_change: ChangeRecord) -> None:
    """Layer 2. An anonymous classification is indistinguishable from an automated one to
    the org consuming the feed, so it is refused."""
    for anonymous in ("", "   ", "\t\n"):
        with pytest.raises(ReviewError, match="named human reviewer"):
            observed_change.reviewed_by(
                reviewer=anonymous,
                significance=Significance.SUBSTANTIVE,
                status=ReviewStatus.CONFIRMED,
            )


def test_the_database_rejects_a_classification_with_no_reviewer(tmp_path: Path) -> None:
    """Layer 3, and the one that survives someone bypassing the Python types entirely.

    This writes raw SQL — no dataclass, no validation, straight at the table — and the
    schema's CHECK constraint refuses it. If a future contributor writes a migration script
    or a bulk-import that skirts `changes.py`, they still cannot store a machine-asserted
    legal classification.
    """
    db = tmp_path / "raw.db"
    with SnapshotStore(db):
        pass

    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO changes (change_id, source_id, jurisdiction, document_class,"
                " url, observed_at, previous_hash, new_hash, diff_excerpt, significance,"
                " review_status, reviewer, reviewed_at, review_note)"
                " VALUES ('x', 's', 'TX', 'drivers_license', 'https://e.gov', '2026-07-13',"
                " 'a', 'b', 'd', 'substantive', 'confirmed', NULL, NULL, '')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO changes (change_id, source_id, jurisdiction, document_class,"
                " url, observed_at, previous_hash, new_hash, diff_excerpt, significance,"
                " review_status, reviewer, reviewed_at, review_note)"
                " VALUES ('y', 's', 'TX', 'drivers_license', 'https://e.gov', '2026-07-13',"
                " 'a', 'b', 'd', 'substantive', 'confirmed', '', NULL, '')"
            )
    finally:
        conn.close()


def test_the_store_surfaces_the_schema_rejection_as_a_store_error(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    """Layer 3, via the normal API: hand-build the illegal record the types forbid (by
    going around the constructor) and confirm the store still refuses it."""
    from dataclasses import replace

    smuggled = replace(
        observed_change,
        significance=Significance.SUBSTANTIVE,
        review_status=ReviewStatus.CONFIRMED,
        reviewer=None,
    )
    with pytest.raises(StoreError, match="integrity rules"):
        store.record_change(smuggled)


def test_confirming_without_classifying_is_refused(observed_change: ChangeRecord) -> None:
    """`confirmed` + `unclassified` would sail through the publisher's status filter
    carrying no human judgment at all. Both the type and the schema refuse it."""
    with pytest.raises(ReviewError, match="requires classifying it"):
        observed_change.reviewed_by(
            reviewer="A Human",
            significance=Significance.UNCLASSIFIED,
            status=ReviewStatus.CONFIRMED,
        )


def test_a_review_cannot_reset_the_status_to_unreviewed(observed_change: ChangeRecord) -> None:
    with pytest.raises(ReviewError, match="back to 'unreviewed'"):
        observed_change.reviewed_by(
            reviewer="A Human",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.UNREVIEWED,
        )


def test_the_cli_cannot_review_without_a_reviewer() -> None:
    """Layer 4. `--reviewer` is `required=True`, so argparse exits 2 rather than recording
    an anonymous classification."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(
            ["review", "abc123", "--significance", "substantive", "--status", "confirmed"]
        )
    assert exit_info.value.code == 2


def test_the_cli_watch_command_leaves_everything_unreviewed(
    tmp_path: Path, source: Source, fixture_before: bytes, fixture_after: bytes
) -> None:
    """Layer 4, end to end: the real command, over real drift, classifies nothing."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        '{"registry_version": "1.0", "sources": [{"id": "' + source.id + '",'
        ' "jurisdiction": "TX", "document_class": "drivers_license", "url": "' + source.url + '",'
        ' "authority": "TX DPS", "verified": false, "notes": ""}]}',
        encoding="utf-8",
    )
    db = tmp_path / "cli.db"
    stub = StubFetcher({source.url: (fixture_before, "text/html")})
    argv = ["--registry", str(registry_path), "--db", str(db), "watch"]

    assert main(argv, fetcher=stub) == 0
    stub.set(source.url, fixture_after)
    assert main(argv, fetcher=stub) == 0

    with SnapshotStore(db) as store:
        recorded = store.changes()
        assert len(recorded) == 1
        assert recorded[0].significance is Significance.UNCLASSIFIED
        assert recorded[0].review_status is ReviewStatus.UNREVIEWED
