"""`sentinel calibrate` (#79) — including the three criteria the issue named as Done-when.

The three, and the test that holds each:

1. *Replaying a fixture set of six reviewed changes and answering four the same way prints
   4/6 with the two divergent ids.* —
   :func:`test_six_replayed_four_agreeing_reports_four_of_six_and_names_both_divergences`
2. *`publish` output is byte-identical before and after a calibration session.* —
   :func:`test_publish_bytes_are_identical_before_and_after_a_calibration_session`
3. *A calibration record with a blank reviewer name is refused at the same layer that refuses
   a blank review.* —
   :func:`test_a_blank_candidate_is_refused_in_python_and_in_sqlite`

Plus the property the issue's Scope names and which is the one that could quietly rot: an
absence must never be counted as agreement. That is
:func:`test_pruned_evidence_is_skipped_named_and_left_out_of_the_denominator`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.calibrate import (
    REPLAYABLE,
    SKIPPED_EVIDENCE_PRUNED,
    SKIPPED_NOT_REVIEWED,
    Agreement,
    CalibrationDecision,
    CalibrationError,
    CalibrationItem,
    agreement,
    calibration_queue,
    replay_card,
    run_calibration,
)
from id_churn_sentinel.core.changes import ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import DEFAULT_SNAPSHOT_RETENTION, SnapshotStore
from id_churn_sentinel.errors import StoreError

#: The publisher stamps `generated_at`, so two runs a second apart differ on that field
#: alone. Freezing it is what makes "byte-identical" a statement about calibration
#: rather than about the clock — and the assertion would be vacuous either way if the
#: session had not written anything, which is why the test checks that too.
_FROZEN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _hash(seed: int) -> str:
    """A distinct, well-formed 64-hex content hash per fixture change."""

    return f"{seed:064x}"


def _observed(index: int, source: Source, *, jurisdiction: str | None = None) -> ChangeRecord:
    return ChangeRecord.observed(
        source_id=source.id,
        jurisdiction=jurisdiction or source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        previous_hash=_hash(index * 2),
        new_hash=_hash(index * 2 + 1),
        diff_excerpt=f"-old passage {index}\n+new passage {index}",
        observed_at=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(minutes=index),
    )


def _reviewed(
    change: ChangeRecord,
    *,
    status: ReviewStatus,
    significance: Significance,
) -> ChangeRecord:
    return change.reviewed_by(
        reviewer="Chelsea Kelly-Reif",
        significance=significance,
        status=status,
        reviewed_at=change.observed_at + timedelta(minutes=1),
    )


def _store_with(
    tmp_path: Path, changes: list[ChangeRecord], *, retention: int = 32
) -> SnapshotStore:
    """A store holding each change, ready for snapshots to be attached selectively.

    `retention` defaults well above `DEFAULT_SNAPSHOT_RETENTION` so that *which* snapshots a
    test retains is decided by the test rather than by the store's pruning. The real pruning
    path — the one that produces `evidence_pruned` in production — is exercised deliberately
    in :func:`test_real_snapshot_retention_pruning_produces_evidence_pruned`.
    """

    store = SnapshotStore(tmp_path / "sentinel.db", retention=retention)
    for change in changes:
        base = ChangeRecord.observed(
            source_id=change.source_id,
            jurisdiction=change.jurisdiction,
            document_class=change.document_class,
            url=change.url,
            previous_hash=change.previous_hash,
            new_hash=change.new_hash,
            diff_excerpt=change.diff_excerpt,
            observed_at=change.observed_at,
        )
        store.record_change(base)
        if change.reviewer is not None:
            store.update_change(change)
    return store


def _retain(store: SnapshotStore, change: ChangeRecord, *, hashes: list[str]) -> None:
    for index, content in enumerate(hashes):
        store.record_snapshot(
            source_id=change.source_id,
            url=change.url,
            fetched_at=change.observed_at + timedelta(seconds=index),
            http_status=200,
            content_sha256=content,
            raw_bytes=b"<html>fixture</html>",
            normalized_text="fixture",
            normalizer_version="passage-text-v2",
            extractor_version="pdf-text-v1",
        )


def _all_retained(store: SnapshotStore, changes: list[ChangeRecord]) -> None:
    for change in changes:
        _retain(store, change, hashes=[change.previous_hash, change.new_hash])


# --------------------------------------------------------------------------------------
# Done-when 1: six replayed, four answered the same way -> 4 of 6, both divergences named
# --------------------------------------------------------------------------------------


def _six(source: Source) -> list[ChangeRecord]:
    """Six reviewed changes spanning every first-review shape a candidate can meet."""

    recorded = [
        (ReviewStatus.CONFIRMED, Significance.SUBSTANTIVE),
        (ReviewStatus.CONFIRMED, Significance.EDITORIAL),
        (ReviewStatus.DISMISSED, Significance.EDITORIAL),
        (ReviewStatus.CONFIRMED, Significance.SUBSTANTIVE),
        (ReviewStatus.CONFIRMED, Significance.EDITORIAL),
        (ReviewStatus.DISMISSED, Significance.EDITORIAL),
    ]
    return [
        _reviewed(_observed(index, source), status=status, significance=significance)
        for index, (status, significance) in enumerate(recorded, start=1)
    ]


def test_six_replayed_four_agreeing_reports_four_of_six_and_names_both_divergences(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        assert [item.eligibility for item in items] == [REPLAYABLE] * 6

        # Four the same, two different. The two divergences are deliberately of the two
        # different kinds: one a status flip, one a same-status significance split — which is
        # the disagreement this tool exists to surface and the easier one to lose.
        answers = iter(
            [
                "c",
                "s",  # 1: agrees (confirmed/substantive)
                "c",
                "e",  # 2: agrees (confirmed/editorial)
                "d",  # 3: agrees (dismissed)
                "c",
                "e",  # 4: DIFFERS — recorded substantive, candidate says editorial
                "c",
                "e",  # 5: agrees
                "c",
                "e",  # 6: DIFFERS — recorded dismissed, candidate confirms
            ]
        )
        printed: list[str] = []
        outcome = run_calibration(
            items,
            lambda _prompt: next(answers),
            printed.append,
            store.record_calibration_decision,
            candidate="Candidate Reviewer",
            session_id="fixture-session",
        )

    assert outcome.completed is True
    assert outcome.agreement.answered == 6
    assert outcome.agreement.agreed == 4
    assert outcome.agreement.fraction == "4 of 6"
    assert set(outcome.agreement.divergent) == {changes[3].id, changes[5].id}

    report = "\n".join(printed)
    assert "agreement 4 of 6 change(s) answered" in report
    assert changes[3].id in report
    assert changes[5].id in report


def test_the_agreement_report_states_a_denominator_and_never_grades(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """The Scope: "counts and a plain fraction with the denominator, never a grade or a
    pass/fail word". A percentage is a grade wearing a number's clothes."""

    result = Agreement(answered=6, agreed=4, divergent=("chg-a", "chg-b"))
    report = "\n".join(result.lines())

    # The numerator never appears without its denominator.
    assert "4 of 6" in report
    assert result.fraction == "4 of 6"

    # No percentage anywhere: `67%` is a grade wearing a number's clothes, and 4/6 and 400/600
    # are different evidence that a percentage renders identically.
    assert "%" not in report
    assert "percent" not in report.lower()

    # No verdict token. The words `score`/`threshold` DO appear — in the sentence that says
    # this is not one — so the check is for a verdict rendered *about the candidate*, not for
    # the vocabulary. A colon or a standalone capitalised verdict is what a grader emits.
    assert re.search(r"\b(PASS|FAIL|PASSED|FAILED|QUALIFIED)\b", report) is None
    assert re.search(r"(?i)\b(pass|fail)(ed|es)?\s*[:=]", report) is None
    assert "not a score and not a" in report
    assert "governance decision" in report


def test_an_empty_session_is_not_reported_as_perfect_agreement() -> None:
    """`0 of 0` is the shape most likely to render as 100%. It must say it measured nothing."""

    report = "\n".join(Agreement(answered=0, agreed=0, divergent=()).lines())
    assert "0 of 0" in report
    assert "An empty session is not a perfect one." in report


# --------------------------------------------------------------------------------------
# Done-when 2: publish output byte-identical before and after a session
# --------------------------------------------------------------------------------------


def test_publish_bytes_are_identical_before_and_after_a_calibration_session(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)

    before = tmp_path / "before"
    after = tmp_path / "after"

    with store:
        publish(
            store.changes(review_status=ReviewStatus.CONFIRMED),
            before,
            registry=registry,
            feed_url="https://example.invalid/",
            now=_FROZEN,
        )
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        answers = iter(["c", "s", "c", "e", "d", "c", "e", "c", "e", "c", "e"])
        run_calibration(
            items,
            lambda _prompt: next(answers),
            lambda _line: None,
            store.record_calibration_decision,
            candidate="Candidate Reviewer",
            session_id="publish-parity",
        )
        # The session really did write something — otherwise this test passes vacuously.
        assert len(store.calibration_decisions(session_id="publish-parity")) == 6
        publish(
            store.changes(review_status=ReviewStatus.CONFIRMED),
            after,
            registry=registry,
            feed_url="https://example.invalid/",
            now=_FROZEN,
        )

    names_before = sorted(path.name for path in before.iterdir())
    names_after = sorted(path.name for path in after.iterdir())
    assert names_before == names_after
    for name in names_before:
        assert (before / name).read_bytes() == (after / name).read_bytes(), (
            f"{name} changed across a calibration session — calibration must be invisible "
            f"to the published surface"
        )


def test_no_published_artifact_mentions_a_calibration_candidate(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """Belt and braces on Done-when 2: not merely identical bytes, but the candidate's name
    absent from every published byte, so a future publisher change cannot leak it quietly."""

    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    out = tmp_path / "out"
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        answers = iter(["c", "s", "c", "e", "d", "c", "e", "c", "e", "c", "e"])
        run_calibration(
            items,
            lambda _prompt: next(answers),
            lambda _line: None,
            store.record_calibration_decision,
            candidate="Distinctive Candidate Name",
            session_id="leak-check",
        )
        publish(
            store.changes(review_status=ReviewStatus.CONFIRMED),
            out,
            registry=registry,
            feed_url="https://example.invalid/",
        )
    for path in out.rglob("*"):
        if path.is_file():
            assert b"Distinctive Candidate Name" not in path.read_bytes(), path


# --------------------------------------------------------------------------------------
# Done-when 3: a blank candidate is refused at the same layer that refuses a blank review
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_a_blank_candidate_is_refused_in_python_and_in_sqlite(
    tmp_path: Path, source: Source, registry: Registry, blank: str
) -> None:
    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
    )
    store = _store_with(tmp_path, [change])
    _all_retained(store, [change])

    # Layer 1 — the dataclass, the twin of `ChangeRecord.reviewed_by`'s refusal.
    with pytest.raises(CalibrationError, match="named human candidate"):
        CalibrationDecision(
            session_id="s",
            change_id=change.id,
            candidate=blank,
            decision=ReviewStatus.CONFIRMED,
            significance=Significance.EDITORIAL,
            decided_at=datetime.now(UTC),
        )

    # Layer 2 — the session entry point, before a single card is shown.
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        with pytest.raises(CalibrationError, match="named human candidate"):
            run_calibration(
                items,
                lambda _prompt: "c",
                lambda _line: None,
                store.record_calibration_decision,
                candidate=blank,
                session_id="blank",
            )

        # Layer 3 — SQL, reached by bypassing the Python boundary entirely, exactly as
        # `test_store_enforces_distinct_actor_even_when_python_boundary_is_bypassed` does
        # for reviews.
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                "INSERT INTO calibration_decisions (calibration_id, session_id, change_id, "
                "candidate, decision, significance, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "f" * 64,
                    "blank",
                    change.id,
                    blank,
                    "confirmed",
                    "editorial",
                    datetime.now(UTC).isoformat(),
                ),
            )
        store._conn.rollback()


def test_confirming_without_classifying_is_refused_like_a_first_review(
    tmp_path: Path, source: Source
) -> None:
    with pytest.raises(CalibrationError, match=r"editorial.*substantive"):
        CalibrationDecision(
            session_id="s",
            change_id="chg-x",
            candidate="Candidate Reviewer",
            decision=ReviewStatus.CONFIRMED,
            significance=Significance.UNCLASSIFIED,
            decided_at=datetime.now(UTC),
        )


# --------------------------------------------------------------------------------------
# Absence is never agreement
# --------------------------------------------------------------------------------------


def test_pruned_evidence_is_skipped_named_and_left_out_of_the_denominator(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """The failure this feature could most easily have shipped: a change whose bytes are gone
    counted as agreement because nobody disagreed with it."""

    changes = _six(source)
    store = _store_with(tmp_path, changes)
    # Retain the bytes for four of the six. Two have aged out of the retention window.
    for change in changes[:4]:
        _retain(store, change, hashes=[change.previous_hash, change.new_hash])

    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        by_id = {item.change.id: item for item in items}
        assert by_id[changes[4].id].eligibility == SKIPPED_EVIDENCE_PRUNED
        assert by_id[changes[5].id].eligibility == SKIPPED_EVIDENCE_PRUNED
        assert sum(1 for item in items if item.replayable) == 4

        answers = iter(["c", "s", "c", "e", "d", "c", "s"])
        printed: list[str] = []
        outcome = run_calibration(
            items,
            lambda _prompt: next(answers),
            printed.append,
            store.record_calibration_decision,
            candidate="Candidate Reviewer",
            session_id="pruned",
        )

    # The denominator is what was answered, not what was selected.
    assert outcome.agreement.answered == 4
    assert outcome.agreement.fraction == "4 of 4"
    assert set(outcome.agreement.skipped_evidence_pruned) == {changes[4].id, changes[5].id}

    report = "\n".join(printed)
    assert "SKIPPED" in report
    assert "NOT counted as agreement or as divergence" in report
    assert changes[4].id in report and changes[5].id in report


def test_an_answer_about_a_no_longer_replayable_change_is_not_counted_as_agreement(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """The case a session-scoped test cannot reach, and the one that bites in production.

    Answers are keyed by `--session-id` and the store keeps only the newest snapshots per
    source. So a session resumed after a `watch` run holds answers about changes whose bytes
    have since been pruned: the decision exists, the evidence does not, and the tempting
    behaviour is to score it anyway because the answer is right there.

    Scoring it would compare the candidate against a review neither of them can now open,
    which is an agreement figure with an unmeasurable term in it. It is excluded from the
    numerator *and* the denominator, and named in the skip list instead.
    """

    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
    )
    pruned_item = CalibrationItem(
        change=change,
        eligibility=SKIPPED_EVIDENCE_PRUNED,
        verification_status="unverified",
    )
    matching_answer = CalibrationDecision(
        session_id="resumed",
        change_id=change.id,
        candidate="Candidate Reviewer",
        decision=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,  # identical to the recorded review
        decided_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    result = agreement([matching_answer], [pruned_item])

    # The answer agrees on its face. It is still not counted, because the evidence is gone.
    assert result.answered == 0
    assert result.agreed == 0
    assert result.divergent == ()
    assert result.skipped_evidence_pruned == (change.id,)
    assert "0 of 0" in "\n".join(result.lines())

    # And the same for a *disagreeing* answer: the exclusion must not be one-sided, or it
    # would quietly shift the figure in one direction.
    diverging_answer = CalibrationDecision(
        session_id="resumed",
        change_id=change.id,
        candidate="Candidate Reviewer",
        decision=ReviewStatus.DISMISSED,
        significance=Significance.EDITORIAL,
        decided_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert agreement([diverging_answer], [pruned_item]).answered == 0


def test_an_answer_about_an_unoffered_change_is_not_counted(source: Source) -> None:
    """A decision naming a change that is not in the replay set at all is not scored against
    it — it belongs to a different set and counting it would mix two measurements."""

    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
    )
    stray = CalibrationDecision(
        session_id="s",
        change_id="chg-from-another-set",
        candidate="Candidate Reviewer",
        decision=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
        decided_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    item = CalibrationItem(change=change, eligibility=REPLAYABLE, verification_status="unverified")
    result = agreement([stray], [item])
    assert result.answered == 0
    assert result.agreed == 0


def test_a_partially_pruned_change_is_not_replayable(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """Half the evidence is not the evidence. A change keeps its `before` snapshot and loses
    its `after`; a passage diff cannot be shown from one side."""

    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
    )
    store = _store_with(tmp_path, [change])
    _retain(store, change, hashes=[change.previous_hash])
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
    assert items[0].eligibility == SKIPPED_EVIDENCE_PRUNED


def test_real_snapshot_retention_pruning_produces_evidence_pruned(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """The production path, not a hand-withheld fixture.

    Six changes on one source is twelve snapshots; `DEFAULT_SNAPSHOT_RETENTION` is 5. The
    store prunes on its own and the oldest changes stop being replayable — which is exactly
    what will happen on the operator's box, and is why this feature needed a skip outcome at
    all rather than assuming the bytes are always there.
    """

    changes = _six(source)
    store = _store_with(tmp_path, changes, retention=DEFAULT_SNAPSHOT_RETENTION)
    _all_retained(store, changes)
    with store:
        held = store.retained_content_hashes(source.id)
        assert len(held) == DEFAULT_SNAPSHOT_RETENTION, (
            "the store's own retention window did not apply"
        )
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        pruned = [item for item in items if item.eligibility == SKIPPED_EVIDENCE_PRUNED]
        replayable = [item for item in items if item.replayable]
    assert pruned, "retention pruning must be visible to calibrate as evidence_pruned"
    assert len(pruned) + len(replayable) == 6
    result = agreement([], items)
    assert result.answered == 0
    assert len(result.skipped_evidence_pruned) == len(pruned)


def test_an_unreviewed_change_is_skipped_with_its_own_reason(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """`not_reviewed` and `evidence_pruned` are separate words because they need separate
    fixes: one needs a reviewer, the other needs a fresher store."""

    unreviewed = _observed(1, source)
    store = _store_with(tmp_path, [unreviewed])
    _all_retained(store, [unreviewed])
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
    assert items[0].eligibility == SKIPPED_NOT_REVIEWED
    assert not items[0].replayable

    result = agreement([], items)
    assert result.answered == 0
    assert result.skipped_not_reviewed == (unreviewed.id,)
    assert result.skipped_evidence_pruned == ()


def test_the_store_refuses_a_calibration_answer_about_an_unreviewed_change(
    tmp_path: Path, source: Source
) -> None:
    """Belt to the queue's braces: even if a caller built the decision by hand, SQL refuses."""

    unreviewed = _observed(1, source)
    store = _store_with(tmp_path, [unreviewed])
    with store, pytest.raises(StoreError, match="recorded first review"):
        store.record_calibration_decision(
            CalibrationDecision(
                session_id="forced",
                change_id=unreviewed.id,
                candidate="Candidate Reviewer",
                decision=ReviewStatus.CONFIRMED,
                significance=Significance.EDITORIAL,
                decided_at=datetime.now(UTC),
            )
        )


# --------------------------------------------------------------------------------------
# The recorded decision is not revealed early, and quitting reveals nothing further
# --------------------------------------------------------------------------------------


def test_the_card_shows_what_review_shows_and_nothing_about_the_recorded_decision(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
    )
    item = CalibrationItem(change=change, eligibility=REPLAYABLE, verification_status="unverified")
    card = replay_card(item, position=1, total=1)

    # What `review`/`diff` shows.
    assert change.id in card
    assert change.url in card
    assert change.jurisdiction in card
    assert change.diff_excerpt.splitlines()[0] in card
    assert "unverified" in card

    # What must never appear.
    assert "substantive" not in card.lower()
    assert "confirmed" not in card.lower()
    assert "Chelsea Kelly-Reif" not in card


def test_quitting_reveals_nothing_about_the_changes_not_yet_answered(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        answers = iter(["c", "s", "q"])
        printed: list[str] = []
        outcome = run_calibration(
            items,
            lambda _prompt: next(answers),
            printed.append,
            store.record_calibration_decision,
            candidate="Candidate Reviewer",
            session_id="quit",
        )

    assert outcome.completed is False
    assert outcome.agreement.answered == 1

    report = "\n".join(printed)
    assert "the rest of the set is unspent" in report
    # Changes 2..6 were never answered, so their ids must not be attached to any verdict.
    for change in changes[1:]:
        assert f"{change.id}\n" not in report or "differs" not in report.split(change.id)[-1][:80]
    assert "are NOT shown" in report


def test_a_second_answer_for_the_same_change_in_one_session_is_refused(
    tmp_path: Path, source: Source
) -> None:
    """Append-only, so a candidate cannot revise after seeing the recorded decision — which
    would make agreement a measure of persistence rather than judgement."""

    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
    )
    store = _store_with(tmp_path, [change])
    _all_retained(store, [change])
    decision = CalibrationDecision(
        session_id="once",
        change_id=change.id,
        candidate="Candidate Reviewer",
        decision=ReviewStatus.CONFIRMED,
        significance=Significance.EDITORIAL,
        decided_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    with store:
        store.record_calibration_decision(decision)
        with pytest.raises(StoreError):
            store.record_calibration_decision(
                CalibrationDecision(
                    session_id="once",
                    change_id=change.id,
                    candidate="Candidate Reviewer",
                    decision=ReviewStatus.CONFIRMED,
                    significance=Significance.SUBSTANTIVE,
                    decided_at=datetime(2026, 8, 2, tzinfo=UTC),
                )
            )


def test_calibration_rows_cannot_be_updated_or_deleted(tmp_path: Path, source: Source) -> None:
    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
    )
    store = _store_with(tmp_path, [change])
    with store:
        store.record_calibration_decision(
            CalibrationDecision(
                session_id="immutable",
                change_id=change.id,
                candidate="Candidate Reviewer",
                decision=ReviewStatus.CONFIRMED,
                significance=Significance.EDITORIAL,
                decided_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._conn.execute("UPDATE calibration_decisions SET significance = 'substantive'")
        store._conn.rollback()
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._conn.execute("DELETE FROM calibration_decisions")
        store._conn.rollback()


# --------------------------------------------------------------------------------------
# The table carries no column a publisher could read as publishable
# --------------------------------------------------------------------------------------


def test_the_calibration_table_has_no_publishable_shaped_column(
    tmp_path: Path, source: Source
) -> None:
    """The guarantee in migration 11 is an *absence*, so it needs a test that names it.

    A future change that adds `public_copy` to this table would make calibration answers
    publishable-shaped, and nothing else in the suite would notice.
    """

    store = SnapshotStore(tmp_path / "sentinel.db")
    with store:
        columns = {
            str(row["name"])
            for row in store._conn.execute("PRAGMA table_info(calibration_decisions)").fetchall()
        }
    assert columns == {
        "calibration_id",
        "session_id",
        "change_id",
        "candidate",
        "decision",
        "significance",
        "decided_at",
    }
    for forbidden in (
        "public_copy",
        "stage",
        "qualification_ref",
        "conflict_attestation_ref",
        "internal_rationale",
    ):
        assert forbidden not in columns, (
            f"calibration_decisions gained {forbidden!r} — a calibration answer must not be "
            f"shaped like a publishable review decision"
        )


def test_publish_does_not_import_the_calibration_module() -> None:
    """A structural guard: the publisher must have no path to this table at all."""

    source_text = (
        Path(__file__).resolve().parents[1] / "src" / "id_churn_sentinel" / "core" / "publish.py"
    ).read_text(encoding="utf-8")
    assert "calibrat" not in source_text.lower()


# --------------------------------------------------------------------------------------
# Set selection
# --------------------------------------------------------------------------------------


def test_a_named_set_is_replayed_in_the_order_given(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    wanted = [changes[3].id, changes[0].id, changes[5].id]
    with store:
        items = calibration_queue(
            store.changes(),
            retained_hashes=store.retained_content_hashes,
            registry=registry,
            change_ids=wanted,
        )
    assert [item.change.id for item in items] == wanted


def test_a_set_naming_an_unknown_change_is_refused_by_name(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    with store, pytest.raises(CalibrationError, match="does not hold"):
        calibration_queue(
            store.changes(),
            retained_hashes=store.retained_content_hashes,
            registry=registry,
            change_ids=[changes[0].id, "chg-not-in-this-store"],
        )


def test_the_queue_is_deterministic_across_repeat_calls(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    with store:
        first = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        second = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
    assert [item.change.id for item in first] == [item.change.id for item in second]


def test_an_unknown_source_reports_unknown_verification_not_unverified(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """ "We have no registry entry for this" and "nobody has confirmed this page" are different
    facts, and collapsing them would be this repository's own dominant defect."""

    orphan = _reviewed(
        ChangeRecord.observed(
            source_id="a-source-this-registry-does-not-carry",
            jurisdiction="US-ZZ",
            document_class="birth_certificate",
            url="https://example.invalid/orphan",
            previous_hash=_hash(900),
            new_hash=_hash(901),
            diff_excerpt="-before\n+after",
            observed_at=datetime(2026, 7, 1, tzinfo=UTC),
        ),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.EDITORIAL,
    )
    store = _store_with(tmp_path, [orphan])
    _all_retained(store, [orphan])
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
    assert items[0].verification_status == "unknown"


# --------------------------------------------------------------------------------------
# The command-line surface
# --------------------------------------------------------------------------------------


def _cli_registry(tmp_path: Path, source: Source) -> Path:
    """A registry file holding just the fixture source, for `main(["--registry", ...])`."""

    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "registry_version": "1.0",
                "gaps": [],
                "sources": [
                    {
                        "id": source.id,
                        "jurisdiction": source.jurisdiction,
                        "document_class": source.document_class,
                        "url": source.url,
                        "authority": source.authority,
                        "verified": False,
                        "notes": "synthetic fixture source for the calibration CLI tests",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_cli_list_names_every_change_and_reveals_no_recorded_decision(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    # Retain four; the other two must be listed as skipped rather than omitted.
    for change in changes[:4]:
        _retain(store, change, hashes=[change.previous_hash, change.new_hash])
    store.close()

    exit_code = main(
        [
            "--registry",
            str(_cli_registry(tmp_path, source)),
            "calibrate",
            "--db",
            str(tmp_path / "sentinel.db"),
            "--list",
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    # Every change is accounted for — a skipped one is named, never dropped.
    for change in changes:
        assert change.id in out
    assert "4 of 6 change(s) replayable" in out
    assert SKIPPED_EVIDENCE_PRUNED in out
    # And no recorded decision leaks: listing must not spend the calibration set.
    assert "substantive" not in out
    assert "Chelsea Kelly-Reif" not in out


def test_cli_runs_a_session_and_reports_the_fraction(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    store.close()

    answers = iter(["c", "s", "c", "e", "d", "c", "e", "c", "e", "c", "e"])
    exit_code = main(
        [
            "--registry",
            str(_cli_registry(tmp_path, source)),
            "calibrate",
            "--db",
            str(tmp_path / "sentinel.db"),
            "--reviewer",
            "Candidate Reviewer",
            "--session-id",
            "cli-session",
        ],
        ask=lambda _prompt: next(answers),
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "agreement 4 of 6 change(s) answered" in out
    assert "not read by `publish`" in out

    with SnapshotStore(tmp_path / "sentinel.db", retention=32) as store:
        assert len(store.calibration_decisions(session_id="cli-session")) == 6


def test_cli_refuses_a_blank_reviewer_without_showing_a_single_card(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    store.close()

    exit_code = main(
        [
            "--registry",
            str(_cli_registry(tmp_path, source)),
            "calibrate",
            "--db",
            str(tmp_path / "sentinel.db"),
        ],
        ask=lambda _prompt: pytest.fail("a card was shown before the name was checked"),
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "named human candidate" in captured.err


def test_cli_reports_nothing_replayable_rather_than_an_empty_agreement(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreviewed store must say why there is nothing to do, not print `0 of 0`."""

    unreviewed = _observed(1, source)
    store = _store_with(tmp_path, [unreviewed])
    _all_retained(store, [unreviewed])
    store.close()

    exit_code = main(
        [
            "--registry",
            str(_cli_registry(tmp_path, source)),
            "calibrate",
            "--db",
            str(tmp_path / "sentinel.db"),
            "--reviewer",
            "Candidate Reviewer",
        ],
        ask=lambda _prompt: pytest.fail("nothing was replayable, so nothing may be asked"),
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "nothing replayable" in out
    assert "0 of 0" not in out


def test_cli_replays_a_named_set_in_file_order(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    store.close()

    wanted = [changes[4].id, changes[1].id]
    set_path = tmp_path / "set.json"
    set_path.write_text(json.dumps({"change_ids": wanted}), encoding="utf-8")

    exit_code = main(
        [
            "--registry",
            str(_cli_registry(tmp_path, source)),
            "calibrate",
            "--db",
            str(tmp_path / "sentinel.db"),
            "--set",
            str(set_path),
            "--list",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.index(wanted[0]) < out.index(wanted[1])
    assert changes[0].id not in out
    assert "2 of 2 change(s) replayable" in out


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("[]", "must be a JSON object"),
        ("{}", "needs a 'change_ids' array"),
        ('{"change_ids": "chg-1"}', "needs a 'change_ids' array"),
        ('{"change_ids": [1, 2]}', "needs a 'change_ids' array"),
        ('{"change_ids": []}', "is empty"),
        ("not json at all", "not valid JSON"),
    ],
)
def test_cli_refuses_a_malformed_calibration_set_by_name(
    tmp_path: Path,
    source: Source,
    capsys: pytest.CaptureFixture[str],
    payload: str,
    expected: str,
) -> None:
    """A bad set file is refused with a sentence, not a traceback — and before any card."""

    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    store.close()

    set_path = tmp_path / "set.json"
    set_path.write_text(payload, encoding="utf-8")

    exit_code = main(
        [
            "--registry",
            str(_cli_registry(tmp_path, source)),
            "calibrate",
            "--db",
            str(tmp_path / "sentinel.db"),
            "--set",
            str(set_path),
            "--reviewer",
            "Candidate Reviewer",
        ],
        ask=lambda _prompt: pytest.fail("a malformed set must be refused before any prompt"),
    )
    assert exit_code == 1
    assert expected in capsys.readouterr().err


def test_cli_refuses_an_unreadable_calibration_set(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store_with(tmp_path, _six(source))
    store.close()
    exit_code = main(
        [
            "--registry",
            str(_cli_registry(tmp_path, source)),
            "calibrate",
            "--db",
            str(tmp_path / "sentinel.db"),
            "--set",
            str(tmp_path / "does-not-exist.json"),
        ]
    )
    assert exit_code == 1
    assert "could not read calibration set" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# Refusals at the edges — a garbled answer must skip, never be guessed at
# --------------------------------------------------------------------------------------


def test_an_unrecognised_answer_is_skipped_rather_than_interpreted(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    """The one place a calibration tool could invent a judgement.

    A candidate who types `y` (the verb `sentinel verify` uses) or fat-fingers `x` has not
    said confirm and has not said dismiss. Mapping either onto a decision would put a
    classification nobody made into a table whose entire purpose is measuring judgement.
    """

    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        answers = iter(
            [
                "y",  # 1: not a verb here -> skipped
                "x",  # 2: nonsense -> skipped
                "c",
                "q",  # 3: a confirm with a nonsense classification -> skipped
                "",  # 4: bare enter -> skipped
                "s",  # 5: explicit skip
                "c",
                "e",  # 6: the only real answer
            ]
        )
        printed: list[str] = []
        outcome = run_calibration(
            items,
            lambda _prompt: next(answers),
            printed.append,
            store.record_calibration_decision,
            candidate="Candidate Reviewer",
            session_id="garbled",
        )
        stored = store.calibration_decisions(session_id="garbled")

    assert outcome.agreement.answered == 1
    assert len(stored) == 1, "only the one real answer may be written"
    assert stored[0].change_id == changes[5].id
    report = "\n".join(printed)
    assert "skipping rather than guessing at what you meant" in report


def test_the_eligibility_vocabulary_is_closed(source: Source) -> None:
    change = _reviewed(
        _observed(1, source),
        status=ReviewStatus.CONFIRMED,
        significance=Significance.EDITORIAL,
    )
    with pytest.raises(CalibrationError, match="calibration eligibility must be one of"):
        CalibrationItem(
            change=change, eligibility="probably_fine", verification_status="unverified"
        )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"session_id": ""}, "requires a session id"),
        ({"decision": ReviewStatus.UNREVIEWED}, "cannot be 'unreviewed'"),
        ({"candidate": "\x00\x01"}, "not a valid bounded name"),
    ],
)
def test_a_calibration_decision_refuses_a_malformed_field(
    source: Source, kwargs: dict[str, object], expected: str
) -> None:
    base: dict[str, object] = {
        "session_id": "s",
        "change_id": "chg-1",
        "candidate": "Candidate Reviewer",
        "decision": ReviewStatus.CONFIRMED,
        "significance": Significance.EDITORIAL,
        "decided_at": datetime(2026, 8, 1, tzinfo=UTC),
    }
    with pytest.raises(CalibrationError, match=expected):
        CalibrationDecision(**{**base, **kwargs})  # type: ignore[arg-type]


def test_a_naive_timestamp_is_refused() -> None:
    """An aware timestamp, like every other dated decision in this store."""

    with pytest.raises(CalibrationError, match="aware timestamp"):
        CalibrationDecision(
            session_id="s",
            change_id="chg-1",
            candidate="Candidate Reviewer",
            decision=ReviewStatus.CONFIRMED,
            significance=Significance.EDITORIAL,
            decided_at=datetime(2026, 8, 1),
        )


def test_run_calibration_refuses_an_unbounded_candidate_name(
    tmp_path: Path, source: Source, registry: Registry
) -> None:
    changes = _six(source)
    store = _store_with(tmp_path, changes)
    _all_retained(store, changes)
    with store:
        items = calibration_queue(
            store.changes(), retained_hashes=store.retained_content_hashes, registry=registry
        )
        with pytest.raises(CalibrationError, match="not a valid bounded name"):
            run_calibration(
                items,
                lambda _prompt: "c",
                lambda _line: None,
                store.record_calibration_decision,
                candidate="\x00\x01",
                session_id="unbounded",
            )
