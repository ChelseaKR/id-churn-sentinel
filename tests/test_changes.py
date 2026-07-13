"""Tests for :mod:`id_churn_sentinel.core.changes` — the record and the review transition.

The classification-gate tests live in `test_no_auto_classification.py` (the merge gate).
This file covers the rest of the type's behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime

from id_churn_sentinel.core.changes import (
    ChangeRecord,
    ReviewStatus,
    Significance,
    change_id,
)


def test_change_id_is_deterministic_in_source_and_hashes() -> None:
    """A re-run over the same drift must produce the same id, so it cannot duplicate a
    change a human already reviewed — and a change id cited in an email six months ago
    still resolves."""
    first = change_id("tx-dps", "aaa", "bbb")
    second = change_id("tx-dps", "aaa", "bbb")

    assert first == second
    assert len(first) == 16
    assert change_id("tx-dps", "aaa", "ccc") != first  # a different transition, different id
    assert change_id("ca-dmv", "aaa", "bbb") != first  # a different source, different id


def test_observed_defaults_to_unclassified_and_unreviewed(observed_change: ChangeRecord) -> None:
    assert observed_change.significance is Significance.UNCLASSIFIED
    assert observed_change.review_status is ReviewStatus.UNREVIEWED
    assert observed_change.reviewer is None
    assert observed_change.reviewed_at is None
    assert observed_change.review_note == ""
    assert not observed_change.publishable


def test_review_produces_a_new_record_and_leaves_the_observation_intact(
    observed_change: ChangeRecord,
) -> None:
    """Frozen: a review does not edit the observation. What the machine saw and what the
    human concluded stay separable, which is what makes the record auditable."""
    reviewed = observed_change.reviewed_by(
        reviewer="A Human",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.DISMISSED,
        note="typo fix",
    )

    assert observed_change.significance is Significance.UNCLASSIFIED  # untouched
    assert reviewed.significance is Significance.EDITORIAL
    assert reviewed.id == observed_change.id  # same change, new judgment
    assert reviewed.diff_excerpt == observed_change.diff_excerpt


def test_reviewer_name_is_stripped(observed_change: ChangeRecord) -> None:
    reviewed = observed_change.reviewed_by(
        reviewer="  A Human  ",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.CONFIRMED,
    )
    assert reviewed.reviewer == "A Human"


def test_only_a_confirmed_and_classified_record_is_publishable(
    observed_change: ChangeRecord,
) -> None:
    editorial = observed_change.reviewed_by(
        reviewer="A Human",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.CONFIRMED,
    )
    dismissed = observed_change.reviewed_by(
        reviewer="A Human",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.DISMISSED,
    )

    assert editorial.publishable
    assert not dismissed.publishable
    assert not observed_change.publishable


def test_explicit_reviewed_at_is_honoured(observed_change: ChangeRecord) -> None:
    when = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
    reviewed = observed_change.reviewed_by(
        reviewer="A Human",
        significance=Significance.SUBSTANTIVE,
        status=ReviewStatus.CONFIRMED,
        reviewed_at=when,
    )
    assert reviewed.reviewed_at == when


def test_to_dict_is_the_published_shape(observed_change: ChangeRecord) -> None:
    payload = observed_change.to_dict()

    assert payload["id"] == observed_change.id
    assert payload["jurisdiction"] == "TX"
    assert payload["kind"] == "content_drift"
    assert payload["significance"] == "unclassified"
    assert payload["review_status"] == "unreviewed"
    assert payload["reviewer"] is None
    assert payload["reviewed_at"] is None
    assert payload["observed_at"].startswith("20")
    assert set(payload) == {
        "id",
        "source_id",
        "jurisdiction",
        "document_class",
        "url",
        "observed_at",
        "previous_hash",
        "new_hash",
        "diff_excerpt",
        "kind",
        "significance",
        "review_status",
        "reviewer",
        "reviewed_at",
        "review_note",
    }


def test_to_dict_serializes_review_timestamps(confirmed_change: ChangeRecord) -> None:
    payload = confirmed_change.to_dict()
    assert payload["reviewed_at"] is not None
    assert payload["reviewer"] == "Chelsea Kelly-Reif"


def test_enums_render_as_their_wire_values() -> None:
    assert str(Significance.SUBSTANTIVE) == "substantive"
    assert str(ReviewStatus.UNREVIEWED) == "unreviewed"
