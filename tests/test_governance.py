"""Adversarial V1 review, language-boundary, and correction-history tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from id_churn_sentinel.core.changes import (
    ChangeRecord,
    IndependentReviewStatus,
    PublicationStatus,
    ReviewStatus,
    Significance,
    canonical_actor,
    governance_reference_is_safe,
    observation_fields_are_valid,
    private_text_is_safe,
    public_copy_is_safe,
)
from id_churn_sentinel.core.publish import changes_json, publish
from id_churn_sentinel.core.registry import REJECTED, Registry, Verification
from id_churn_sentinel.core.store import SnapshotStore
from id_churn_sentinel.errors import PublishError, ReviewError, StoreError


def _first_substantive(observed: ChangeRecord, actor: str = "First Reviewer") -> ChangeRecord:
    return observed.reviewed_by(
        reviewer=actor,
        significance=Significance.SUBSTANTIVE,
        status=ReviewStatus.CONFIRMED,
        note="private first-review rationale",
    )


def _independently_confirm(first: ChangeRecord) -> ChangeRecord:
    return first.independently_reviewed_by(
        reviewer="Independent Reviewer",
        status=IndependentReviewStatus.CONFIRMED,
        qualification_ref="governance/qualifications/independent-reviewer.json",
        conflict_attestation_ref="governance/conflicts/change-review.json",
        rationale="private second-review rationale",
    )


def _replacement(observed: ChangeRecord) -> ChangeRecord:
    replacement = ChangeRecord.observed(
        source_id=observed.source_id,
        jurisdiction=observed.jurisdiction,
        document_class=observed.document_class,
        url=observed.url,
        observed_at=observed.observed_at + timedelta(seconds=1),
        previous_hash=observed.new_hash,
        new_hash="d" * 64,
        diff_excerpt="-incorrect public passage\n+corrected public passage",
    )
    return replacement.reviewed_by(
        reviewer="Correction Reviewer",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.CONFIRMED,
        note="private correction rationale",
        reviewed_at=replacement.observed_at + timedelta(seconds=1),
    )


def _persist_substantive(
    store: SnapshotStore, observed: ChangeRecord, reviewed: ChangeRecord
) -> None:
    store.record_change(observed)
    store.update_change(reviewed)
    store.record_independent_review(reviewed)


def test_substantive_first_review_is_not_publishable_without_independent_decision(
    observed_change: ChangeRecord,
) -> None:
    first = _first_substantive(observed_change)

    assert not first.publishable
    with pytest.raises(ReviewError, match="must be different"):
        first.independently_reviewed_by(
            reviewer=" first reviewer ",
            status=IndependentReviewStatus.CONFIRMED,
            qualification_ref="governance/qualification.json",
            conflict_attestation_ref="governance/conflict.json",
        )


def test_directly_forged_independent_decision_without_governance_refs_is_not_publishable(
    observed_change: ChangeRecord,
) -> None:
    confirmed = _independently_confirm(_first_substantive(observed_change))

    assert not replace(confirmed, independent_qualification_ref="").publishable
    assert not replace(confirmed, independent_conflict_attestation_ref="").publishable


def test_returned_independent_decision_preserves_both_decisions_and_blocks_publication(
    observed_change: ChangeRecord,
) -> None:
    first = _first_substantive(observed_change)
    returned = first.independently_reviewed_by(
        reviewer="Independent Reviewer",
        status=IndependentReviewStatus.RETURNED,
        qualification_ref="governance/qualification.json",
        conflict_attestation_ref="governance/conflict.json",
        rationale="The visible observation copy is not supported by the cited passage.",
    )

    assert returned.reviewer == "First Reviewer"
    assert returned.independent_reviewer == "Independent Reviewer"
    assert returned.independent_review_status is IndependentReviewStatus.RETURNED
    assert not returned.publishable


def test_independent_review_rejects_wrong_state_duplicate_missing_reason_and_bad_metadata(
    observed_change: ChangeRecord,
) -> None:
    with pytest.raises(ReviewError, match="first-confirmed substantive"):
        _independently_confirm(observed_change)

    first = _first_substantive(observed_change)
    confirmed = _independently_confirm(first)
    with pytest.raises(ReviewError, match="already recorded"):
        _independently_confirm(confirmed)
    with pytest.raises(ReviewError, match="requires an internal rationale"):
        first.independently_reviewed_by(
            reviewer="Independent Reviewer",
            status=IndependentReviewStatus.RETURNED,
            qualification_ref="governance/qualification.json",
            conflict_attestation_ref="governance/conflict.json",
        )
    for invalid_ref in ("", "bad\nreference", "x" * 513):
        with pytest.raises(ReviewError, match="bounded reference"):
            first.independently_reviewed_by(
                reviewer="Independent Reviewer",
                status=IndependentReviewStatus.CONFIRMED,
                qualification_ref=invalid_ref,
                conflict_attestation_ref="governance/conflict.json",
            )


@pytest.mark.parametrize("actor", ["", "bad\nactor", "bad\u202eactor", "x" * 201, "é" * 101])
def test_named_actors_are_bounded(observed_change: ChangeRecord, actor: str) -> None:
    first = _first_substantive(observed_change)
    with pytest.raises(ReviewError, match=r"named human actor|bounded name"):
        first.independently_reviewed_by(
            reviewer=actor,
            status=IndependentReviewStatus.CONFIRMED,
            qualification_ref="governance/qualification.json",
            conflict_attestation_ref="governance/conflict.json",
        )


def test_first_reviewer_uses_the_same_public_actor_boundary(
    observed_change: ChangeRecord,
) -> None:
    with pytest.raises(ReviewError, match="bounded name"):
        observed_change.reviewed_by(
            reviewer="spoofed\u202ename",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.CONFIRMED,
        )


@pytest.mark.parametrize("rationale", ["contains\x00null", "x" * 4097])
def test_private_rationale_is_bounded(observed_change: ChangeRecord, rationale: str) -> None:
    with pytest.raises(ReviewError, match="private-text safety limit"):
        observed_change.reviewed_by(
            reviewer="First Reviewer",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.CONFIRMED,
            note=rationale,
        )


def test_store_enforces_distinct_actor_even_when_python_boundary_is_bypassed(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    first = _first_substantive(observed_change)
    store.record_change(observed_change)
    store.update_change(first)

    with pytest.raises(sqlite3.IntegrityError, match="must differ"):
        store._conn.execute(
            "INSERT INTO review_decisions "
            "(decision_id, change_id, stage, decision, significance, actor, decided_at, "
            " qualification_ref, conflict_attestation_ref) "
            "VALUES (?, ?, 'independent', 'confirmed', 'substantive', ?, ?, ?, ?)",
            (
                "attack-same-actor",
                observed_change.id,
                "FIRST REVIEWER",
                datetime.now(UTC).isoformat(),
                "governance/qualification.json",
                "governance/conflict.json",
            ),
        )


def test_unicode_equivalent_reviewers_are_one_identity_in_python_and_sqlite(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    first = _first_substantive(observed_change, actor="Jose\u0301 Reviewer")
    assert first.reviewer == "José Reviewer"

    with pytest.raises(ReviewError, match="must be different"):
        first.independently_reviewed_by(
            reviewer="José Reviewer",
            status=IndependentReviewStatus.CONFIRMED,
            qualification_ref="governance/qualification.json",
            conflict_attestation_ref="governance/conflict.json",
        )

    store.record_change(observed_change)
    store.update_change(first)
    with pytest.raises(sqlite3.IntegrityError, match="must differ"):
        store._conn.execute(
            "INSERT INTO review_decisions "
            "(decision_id, change_id, stage, decision, significance, actor, decided_at, "
            " qualification_ref, conflict_attestation_ref) "
            "VALUES (?, ?, 'independent', 'confirmed', 'substantive', ?, ?, ?, ?)",
            (
                "attack-unicode-equivalent-actor",
                observed_change.id,
                "JOSÉ REVIEWER",
                datetime.now(UTC).isoformat(),
                "governance/qualification.json",
                "governance/conflict.json",
            ),
        )


def test_malformed_unicode_is_rejected_by_every_safety_boundary_without_raising(
    store: SnapshotStore,
    observed_change: ChangeRecord,
    registry: Registry,
) -> None:
    malformed = json.loads(r'"\ud800"')
    assert isinstance(malformed, str)

    assert canonical_actor(malformed) == ""
    assert not public_copy_is_safe(malformed)
    assert not private_text_is_safe(malformed)
    assert not governance_reference_is_safe(malformed)
    assert not observation_fields_are_valid(
        observed_change.id,
        malformed,
        observed_change.observed_at,
        observed_change.previous_hash,
        observed_change.new_hash,
        observed_change.diff_excerpt,
        observed_change.kind,
    )

    with pytest.raises(ReviewError, match="bounded name"):
        observed_change.reviewed_by(
            reviewer=malformed,
            significance=Significance.EDITORIAL,
            status=ReviewStatus.CONFIRMED,
        )
    with pytest.raises(ReviewError, match="public copy must be"):
        observed_change.reviewed_by(
            reviewer="First Reviewer",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.CONFIRMED,
            public_copy=malformed,
        )
    with pytest.raises(ReviewError, match="private-text safety limit"):
        observed_change.reviewed_by(
            reviewer="First Reviewer",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.CONFIRMED,
            note=malformed,
        )

    first = _first_substantive(observed_change)
    with pytest.raises(ReviewError, match="bounded reference"):
        first.independently_reviewed_by(
            reviewer="Independent Reviewer",
            status=IndependentReviewStatus.CONFIRMED,
            qualification_ref=malformed,
            conflict_attestation_ref="governance/conflict.json",
        )

    forged = replace(
        _independently_confirm(first),
        independent_qualification_ref=malformed,
    )
    assert not forged.independent_review_valid
    assert not forged.publishable
    store.record_change(observed_change)
    store.update_change(first)
    with pytest.raises(StoreError, match="incomplete"):
        store.record_independent_review(forged)
    assert not store._conn.in_transaction

    poisoned_observation = replace(observed_change, diff_excerpt=malformed)
    with pytest.raises(PublishError, match="malformed observation state"):
        changes_json(
            [poisoned_observation],
            feed_url="https://example.org",
            generated_at=datetime.now(UTC),
            registry=registry,
        )


def test_review_events_cannot_be_updated_or_deleted(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    first = _first_substantive(observed_change)
    confirmed = _independently_confirm(first)
    _persist_substantive(store, observed_change, confirmed)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute(
            "UPDATE review_decisions SET actor = 'replacement' WHERE change_id = ?",
            (observed_change.id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute(
            "DELETE FROM review_decisions WHERE change_id = ?", (observed_change.id,)
        )


def test_observation_rows_cannot_be_updated_or_deleted(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    store.record_change(observed_change)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute(
            "UPDATE changes SET previous_hash = ? WHERE change_id = ?",
            ("c" * 64, observed_change.id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("DELETE FROM changes WHERE change_id = ?", (observed_change.id,))


def test_store_rejects_incomplete_unknown_and_duplicate_review_events(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    with pytest.raises(StoreError, match="incomplete"):
        store.update_change(observed_change)
    unknown_observation = ChangeRecord.observed(
        source_id=observed_change.source_id,
        jurisdiction=observed_change.jurisdiction,
        document_class=observed_change.document_class,
        url=observed_change.url,
        previous_hash="c" * 64,
        new_hash="d" * 64,
        diff_excerpt="-unknown old passage\n+unknown new passage",
    )
    unknown = _first_substantive(unknown_observation)
    with pytest.raises(StoreError, match="unknown change id"):
        store.update_change(unknown)

    first = _first_substantive(observed_change)
    store.record_change(observed_change)
    store.update_change(first)
    with pytest.raises(StoreError, match="integrity rules"):
        store.update_change(first)
    with pytest.raises(StoreError, match="incomplete"):
        store.record_independent_review(first)

    confirmed = _independently_confirm(first)
    store.record_independent_review(confirmed)
    with pytest.raises(StoreError, match="integrity rules"):
        store.record_independent_review(confirmed)


def test_free_form_rationale_and_registry_notes_never_reach_public_bytes(
    tmp_path: Path,
    observed_change: ChangeRecord,
    registry: Registry,
) -> None:
    internal_text = "PRIVATE-RATIONALE operative and requires legal analysis"
    first = observed_change.reviewed_by(
        reviewer="First Reviewer",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.CONFIRMED,
        note=internal_text,
    )

    publish([first], tmp_path, registry=registry)
    public_bytes = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "changes.json", tmp_path / "feed.xml", tmp_path / "index.html")
    )
    payload = json.loads((tmp_path / "changes.json").read_text(encoding="utf-8"))

    assert internal_text not in public_bytes
    assert all(source["notes"] == "" for source in payload["sources"])
    assert "internal_rationale" not in payload["changes"][0]


def test_rejected_source_private_verification_note_never_reaches_public_bytes(
    tmp_path: Path,
    registry: Registry,
) -> None:
    private_marker = "PRIVATE-REGISTRY-REJECTION-RATIONALE-91f8"
    subject = registry.sources[0]
    rejected = replace(
        subject,
        verified=False,
        verification=Verification(
            status=REJECTED,
            verifier="Registry Reviewer",
            at="2026-07-14",
            note=private_marker,
        ),
    )
    private_registry = Registry(
        version=registry.version,
        sources=(rejected, *registry.sources[1:]),
        gaps=registry.gaps,
    )

    publish([], tmp_path, registry=private_registry)
    public_bytes = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.iterdir()
        if path.suffix in {".json", ".xml", ".html"}
    )

    assert private_marker not in public_bytes
    source = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))["sources"][0]
    assert source["notes"] == ""


@pytest.mark.parametrize("claim", ["operative", "requires", "permits", "effective"])
def test_prohibited_legal_claim_terms_fail_closed_in_public_copy(
    observed_change: ChangeRecord, claim: str
) -> None:
    with pytest.raises(ReviewError, match="prohibited legal-claim term"):
        observed_change.reviewed_by(
            reviewer="First Reviewer",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.CONFIRMED,
            public_copy=f"The cited source {claim} a court order.",
        )


@pytest.mark.parametrize(
    "copy",
    ["", "x" * 1001, "contains\x00null", "contains\nnewline", "spoofed\u202ecopy"],
)
def test_public_copy_is_nonempty_bounded_and_control_free(
    observed_change: ChangeRecord, copy: str
) -> None:
    with pytest.raises(ReviewError, match="public copy must be"):
        observed_change.reviewed_by(
            reviewer="First Reviewer",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.CONFIRMED,
            public_copy=copy,
        )


def test_legacy_mutable_review_note_is_quarantined_until_v1_audit(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    store.record_change(observed_change)
    store._conn.execute(
        "UPDATE changes SET significance = 'editorial', review_status = 'confirmed', "
        "reviewer = 'Legacy Reviewer', reviewed_at = ?, review_note = ? WHERE change_id = ?",
        (datetime.now(UTC).isoformat(), "legacy arbitrary public note", observed_change.id),
    )
    store._conn.commit()

    loaded = store.get_change(observed_change.id)

    assert loaded.internal_rationale == "legacy arbitrary public note"
    assert loaded.review_note == ""
    assert not loaded.publishable


def test_correction_history_is_visible_immutable_and_cycle_safe(
    tmp_path: Path,
    store: SnapshotStore,
    observed_change: ChangeRecord,
    registry: Registry,
) -> None:
    confirmed = _independently_confirm(_first_substantive(observed_change))
    _persist_substantive(store, observed_change, confirmed)
    replacement = _replacement(observed_change)
    replacement_observed = replace(
        replacement,
        significance=Significance.UNCLASSIFIED,
        review_status=ReviewStatus.UNREVIEWED,
        reviewer=None,
        reviewed_at=None,
        review_note="",
        internal_rationale="",
    )
    store.record_change(replacement_observed)
    store.update_change(replacement)

    subject = store.get_change(observed_change.id)
    assert replacement.reviewed_at is not None
    corrected = subject.corrected_by(
        replacement_id=replacement.id,
        actor="Correction Decision Maker",
        reason="source_evidence_error",
        decided_at=replacement.reviewed_at + timedelta(seconds=1),
    )
    store.record_lifecycle_event(corrected)

    projected = store.get_change(observed_change.id)
    assert projected.publication_status is PublicationStatus.CORRECTED
    assert projected.superseded_by == replacement.id
    assert projected.lifecycle_reason == "source_evidence_error"
    assert projected.publishable

    with pytest.raises(StoreError, match=r"append-only|UNIQUE"):
        store.record_lifecycle_event(corrected)

    assert corrected.lifecycle_at is not None
    reverse = replacement.corrected_by(
        replacement_id=observed_change.id,
        actor="Correction Decision Maker",
        reason="superseded_observation",
        decided_at=corrected.lifecycle_at + timedelta(seconds=1),
    )
    with pytest.raises(StoreError, match="cycle"):
        store.record_lifecycle_event(reverse)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute(
            "DELETE FROM change_lifecycle_events WHERE change_id = ?", (observed_change.id,)
        )

    records = store.changes(review_status=ReviewStatus.CONFIRMED)
    publish(records, tmp_path, registry=registry)
    public = json.loads((tmp_path / "changes.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in public["changes"]}
    assert by_id[observed_change.id]["publication_status"] == "corrected"
    assert by_id[observed_change.id]["superseded_by"] == replacement.id
    assert by_id[replacement.id]["publication_status"] == "active"


def test_correction_and_withdrawal_domain_boundaries(
    observed_change: ChangeRecord, confirmed_change: ChangeRecord
) -> None:
    with pytest.raises(ReviewError, match="active publishable"):
        observed_change.corrected_by(
            replacement_id="c" * 16,
            actor="Decision Maker",
            reason="review_error",
        )
    for replacement_id in (confirmed_change.id, "not-a-change-id"):
        with pytest.raises(ReviewError, match="different valid replacement"):
            confirmed_change.corrected_by(
                replacement_id=replacement_id,
                actor="Decision Maker",
                reason="review_error",
            )
    with pytest.raises(ReviewError, match="controlled reason vocabulary"):
        confirmed_change.corrected_by(
            replacement_id="c" * 16,
            actor="Decision Maker",
            reason="free form legal conclusion",
        )

    review_completed_at = confirmed_change.independent_reviewed_at or confirmed_change.reviewed_at
    assert review_completed_at is not None
    decided_at = review_completed_at + timedelta(seconds=1)
    withdrawn = confirmed_change.withdrawn_by(
        actor="Decision Maker",
        reason="privacy_or_safety",
        decided_at=decided_at,
    )
    assert withdrawn.publishable
    assert withdrawn.publication_status is PublicationStatus.WITHDRAWN
    assert withdrawn.to_dict()["lifecycle_at"] == decided_at.isoformat()
    with pytest.raises(ReviewError, match="active publishable"):
        withdrawn.withdrawn_by(actor="Decision Maker", reason="privacy_or_safety")


def test_privacy_withdrawal_redacts_prior_public_copy_across_every_artifact(
    tmp_path: Path,
    confirmed_change: ChangeRecord,
    registry: Registry,
) -> None:
    sensitive_copy = "SENSITIVE-PUBLIC-COPY-4de2"
    sensitive_diff = "-SENSITIVE-DIFF-BEFORE-7aa1\n+SENSITIVE-DIFF-AFTER-7aa1"
    private_rationale = "PRIVATE-INTERNAL-RATIONALE-c531"
    sensitive = replace(
        confirmed_change,
        review_note=sensitive_copy,
        diff_excerpt=sensitive_diff,
        internal_rationale=private_rationale,
    )
    withdrawn = sensitive.withdrawn_by(
        actor="Privacy Decision Maker",
        reason="privacy_or_safety",
    )

    publish([withdrawn], tmp_path, registry=registry)
    public_bytes = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.iterdir()
        if path.suffix in {".json", ".xml", ".html"}
    )

    assert sensitive_copy not in public_bytes
    assert "SENSITIVE-DIFF" not in public_bytes
    assert private_rationale not in public_bytes
    payload = json.loads((tmp_path / "changes.json").read_text(encoding="utf-8"))["changes"][0]
    assert payload["publication_status"] == "withdrawn"
    assert "withdrawn for privacy or safety" in payload["review_note"].lower()


def test_withdrawal_round_trips_and_incomplete_lifecycle_is_rejected(
    store: SnapshotStore,
    observed_change: ChangeRecord,
    confirmed_change: ChangeRecord,
) -> None:
    _persist_substantive(store, observed_change, confirmed_change)
    with pytest.raises(StoreError, match="incomplete"):
        store.record_lifecycle_event(confirmed_change)

    withdrawn = store.get_change(observed_change.id).withdrawn_by(
        actor="Decision Maker", reason="privacy_or_safety"
    )
    store.record_lifecycle_event(withdrawn)
    projected = store.get_change(observed_change.id)

    assert projected.publication_status is PublicationStatus.WITHDRAWN
    assert projected.superseded_by is None
    assert projected.lifecycle_actor == "Decision Maker"
    assert projected.publishable


def test_publisher_fails_loudly_on_poisoned_lifecycle_projection(
    tmp_path: Path,
    confirmed_change: ChangeRecord,
    registry: Registry,
) -> None:
    poisoned = replace(
        confirmed_change,
        publication_status=PublicationStatus.WITHDRAWN,
        lifecycle_reason="",
        lifecycle_actor=None,
        lifecycle_at=None,
    )

    with pytest.raises(PublishError, match="malformed lifecycle state"):
        publish([poisoned], tmp_path, registry=registry)


def test_publisher_rejects_a_corrected_record_without_its_replacement(
    tmp_path: Path,
    confirmed_change: ChangeRecord,
    registry: Registry,
) -> None:
    corrected = confirmed_change.corrected_by(
        replacement_id="c" * 16,
        actor="Correction Decision Maker",
        reason="review_error",
    )

    with pytest.raises(PublishError, match="replacement is absent"):
        publish([corrected], tmp_path, registry=registry)
