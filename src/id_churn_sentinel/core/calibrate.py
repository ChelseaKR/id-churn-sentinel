"""`sentinel calibrate` — measure whether two people mean the same thing by `substantive`.

**The problem this exists to solve (issue #79).** The human gate is the product, and it has a
bus factor of one (#64). Adding a second reviewer is not a matter of handing someone the
command: `significance` is a judgement, `substantive` is the classification that travels
outward into legal-aid guidance, and a new reviewer's first decisions are *live* decisions.
There has been no way to find out whether a candidate reviewer classifies the way the
maintainer does short of letting them classify something real.

Calibration replays changes that have **already** been reviewed. The candidate sees exactly
what `review` shows — the source, the jurisdiction and document class, the source's
verification status, and the passage diff — and never the recorded decision. They answer, the
answer is written, and only then is the recorded decision revealed. At the end the session
prints agreement as counts and a plain fraction with its denominator, plus the ids where the
two people diverged, so the two humans can go and talk about those specific changes.

**What this module refuses to do, and why each refusal is structural rather than a rule
somebody has to remember.**

* *It never produces a publishable record.* Calibration decisions live in their own table with
  **no `public_copy`, no `qualification_ref`, no `conflict_attestation_ref` and no `stage`
  column**. The absence is the mechanism: there is no column a publisher could read as
  publishable, so `publish` cannot be taught to read one by accident. `core/publish.py` does
  not import this module and never should.
* *It never scores.* Agreement is reported as `agreed / answered` with the denominator always
  printed, and the divergent ids listed. There is no grade, no percentage-as-a-verdict, no
  pass, no fail, and no threshold — because "did this candidate pass calibration?" is GOV-02's
  question, a governance decision with a named human behind it, and a number that looked like
  an answer would be taken as one.
* *It never reveals early.* The recorded decision is read out of the store only after the
  candidate's own decision is committed. Quitting mid-session reveals nothing about the
  changes not yet answered — see :func:`run_calibration`.
* *It never counts an absence as agreement.* A change whose supporting bytes have been pruned
  out of the snapshot store is reported as :data:`SKIPPED_EVIDENCE_PRUNED` and is **excluded
  from the denominator**, never silently treated as agreement or as disagreement. This is the
  one place the module could have published an absence as a measurement, so it is the one the
  tests hold hardest.

**Why pruned evidence is a skip rather than a degraded replay.** The store keeps the newest
`DEFAULT_SNAPSHOT_RETENTION` snapshots per source and drops the rest, so the bytes behind an
older observation go away while its immutable `diff_excerpt` remains. Replaying such a change
would ask the candidate to agree with a *summary* the original reviewer could still have
opened the evidence behind — the two humans would not have looked at the same thing, and an
agreement measured across different evidence is not a measurement of agreement. So the change
is named, counted, and left out of the fraction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from id_churn_sentinel.core.changes import (
    ChangeKind,
    ChangeRecord,
    ReviewStatus,
    Significance,
    canonical_actor,
)
from id_churn_sentinel.core.registry import Registry
from id_churn_sentinel.errors import SentinelError

__all__ = [
    "REPLAYABLE",
    "SKIPPED_EVIDENCE_PRUNED",
    "SKIPPED_NOT_REVIEWED",
    "Agreement",
    "CalibrationDecision",
    "CalibrationError",
    "CalibrationItem",
    "CalibrationOutcome",
    "agreement",
    "calibration_queue",
    "replay_card",
    "run_calibration",
]


class CalibrationError(SentinelError):
    """A calibration decision that must not be recorded."""


#: The closed vocabulary for *why a reviewed change is or is not replayable*. It is a closed
#: set for the same reason `probes.outcome` is (migration 10): the day "we could not show this
#: to the candidate" and "the candidate agreed" share a value is the day the agreement figure
#: stops meaning anything.
REPLAYABLE = "replayable"
SKIPPED_EVIDENCE_PRUNED = "evidence_pruned"
SKIPPED_NOT_REVIEWED = "not_reviewed"

_ELIGIBILITY = (REPLAYABLE, SKIPPED_EVIDENCE_PRUNED, SKIPPED_NOT_REVIEWED)

#: What a candidate may answer. Deliberately the *first-review* vocabulary and nothing wider:
#: calibration measures agreement with a recorded first review, so offering a verb the first
#: review cannot express would produce divergences that mean nothing.
_ANSWERS = {
    "c": (ReviewStatus.CONFIRMED, None),
    "confirm": (ReviewStatus.CONFIRMED, None),
    "d": (ReviewStatus.DISMISSED, Significance.EDITORIAL),
    "dismiss": (ReviewStatus.DISMISSED, Significance.EDITORIAL),
}

_SIGNIFICANCES = {
    "e": Significance.EDITORIAL,
    "editorial": Significance.EDITORIAL,
    "s": Significance.SUBSTANTIVE,
    "substantive": Significance.SUBSTANTIVE,
}


@dataclass(frozen=True, slots=True)
class CalibrationItem:
    """One reviewed change, and whether it can honestly be put in front of a candidate."""

    change: ChangeRecord
    eligibility: str
    verification_status: str

    def __post_init__(self) -> None:
        if self.eligibility not in _ELIGIBILITY:
            raise CalibrationError(
                f"calibration eligibility must be one of {_ELIGIBILITY}, got {self.eligibility!r}"
            )

    @property
    def replayable(self) -> bool:
        return self.eligibility == REPLAYABLE


@dataclass(frozen=True, slots=True)
class CalibrationDecision:
    """A candidate's answer. Carries a name and a date exactly as a review does, and carries
    none of the fields that would let it be published."""

    session_id: str
    change_id: str
    candidate: str
    decision: ReviewStatus
    significance: Significance
    decided_at: datetime

    def __post_init__(self) -> None:
        if not self.candidate.strip():
            raise CalibrationError(
                "a calibration decision requires a named human candidate — this records a "
                "person's judgement, and an anonymous one measures nothing"
            )
        if not canonical_actor(self.candidate):
            raise CalibrationError("calibration candidate is not a valid bounded name")
        if not self.session_id.strip():
            raise CalibrationError("a calibration decision requires a session id")
        if self.decision is ReviewStatus.UNREVIEWED:
            raise CalibrationError("a calibration answer cannot be 'unreviewed'")
        if self.decision is ReviewStatus.CONFIRMED and self.significance is (
            Significance.UNCLASSIFIED
        ):
            raise CalibrationError(
                "confirming requires classifying it as 'editorial' or 'substantive' — the "
                "same rule the first review is held to"
            )
        if self.decided_at.utcoffset() is None:
            raise CalibrationError("a calibration decision must carry an aware timestamp")


@dataclass(frozen=True, slots=True)
class Agreement:
    """Counts, and the ids to go and talk about. Never a grade.

    `answered` is the denominator and it is always printed beside the numerator, because a
    bare "4" and a bare "67%" are both readable as verdicts and neither says what was skipped.
    """

    answered: int
    agreed: int
    divergent: tuple[str, ...]
    skipped_evidence_pruned: tuple[str, ...] = ()
    skipped_not_reviewed: tuple[str, ...] = ()

    @property
    def fraction(self) -> str:
        """`agreed of answered`, with the denominator, always. No percentage."""

        return f"{self.agreed} of {self.answered}"

    def lines(self) -> tuple[str, ...]:
        """The end-of-session report. Every absence is named; none is folded into the ratio."""

        report = [
            f"calibrate: agreement {self.fraction} change(s) answered.",
            "  This is a count of where two named people agreed, not a score and not a",
            "  threshold. Whether it is enough is a governance decision (docs/13-BACKLOG.md",
            "  GOV-02), and nothing here makes it.",
        ]
        if self.divergent:
            report.append(f"  {len(self.divergent)} change(s) where the two decisions differ:")
            report.extend(f"    {change_id}" for change_id in self.divergent)
            report.append("    Read each with `sentinel diff <change-id>` and talk about it.")
        elif self.answered:
            report.append("  No divergences on the changes answered.")
        if self.skipped_evidence_pruned:
            report.append(
                f"  {len(self.skipped_evidence_pruned)} change(s) SKIPPED — the snapshot bytes "
                f"behind the observation have been pruned, so the candidate could not be shown "
                f"what the reviewer saw. NOT counted as agreement or as divergence:"
            )
            report.extend(f"    {change_id}" for change_id in self.skipped_evidence_pruned)
        if self.skipped_not_reviewed:
            report.append(
                f"  {len(self.skipped_not_reviewed)} change(s) SKIPPED — no recorded first "
                f"review to compare against. NOT counted:"
            )
            report.extend(f"    {change_id}" for change_id in self.skipped_not_reviewed)
        if not self.answered:
            report.append(
                "  Nothing was answered, so there is no agreement figure. An empty session is "
                "not a perfect one."
            )
        return tuple(report)


@dataclass(frozen=True, slots=True)
class CalibrationOutcome:
    """What a session produced. `revealed` is false when the candidate quit early, and it is
    reported so nobody reads a partial session as a complete one."""

    session_id: str
    candidate: str
    agreement: Agreement
    completed: bool


def _verification_status(registry: Registry | None, source_id: str) -> str:
    """The source's human-verification status, shown to the candidate exactly as `review`
    shows it. Unknown to this registry is reported as `unknown`, never as `unverified`:
    "we have no entry for this" and "nobody has confirmed this page" are different facts."""

    if registry is None:
        return "unknown"
    for source in registry.sources:
        if source.id == source_id:
            return str(source.verification.status)
    return "unknown"


def calibration_queue(
    changes: Iterable[ChangeRecord],
    *,
    retained_hashes: Callable[[str], frozenset[str]],
    registry: Registry | None = None,
    change_ids: Sequence[str] | None = None,
) -> tuple[CalibrationItem, ...]:
    """Build the replay queue, in a stable order, classifying each change's eligibility.

    `retained_hashes` answers "which content hashes does the store still hold for this
    source?". A change is replayable only when the bytes its passage diff was derived from are
    still there — both sides for a content drift, and the baseline side for a
    `possibly_removed` escalation, which never had an `after` to retain.

    `change_ids` is the `--set` selection: an explicit list, replayed in the order given, so a
    calibration set is reproducible rather than dependent on what the store happens to hold.
    """

    by_id = {change.id: change for change in changes}
    selected: list[ChangeRecord]
    if change_ids is None:
        selected = sorted(by_id.values(), key=lambda change: (change.observed_at, change.id))
    else:
        missing = [identifier for identifier in change_ids if identifier not in by_id]
        if missing:
            raise CalibrationError(
                "calibration set names change id(s) this store does not hold: "
                + ", ".join(sorted(missing))
            )
        selected = [by_id[identifier] for identifier in change_ids]

    items: list[CalibrationItem] = []
    for change in selected:
        status = _verification_status(registry, change.source_id)
        if change.review_status is ReviewStatus.UNREVIEWED or change.reviewer is None:
            items.append(
                CalibrationItem(
                    change=change,
                    eligibility=SKIPPED_NOT_REVIEWED,
                    verification_status=status,
                )
            )
            continue
        held = retained_hashes(change.source_id)
        required = {change.previous_hash}
        if change.kind is ChangeKind.CONTENT_DRIFT:
            required.add(change.new_hash)
        eligibility = REPLAYABLE if required <= held else SKIPPED_EVIDENCE_PRUNED
        items.append(
            CalibrationItem(change=change, eligibility=eligibility, verification_status=status)
        )
    return tuple(items)


def replay_card(item: CalibrationItem, *, position: int, total: int) -> str:
    """What the candidate sees. The same facts `review` and `diff` show, and **nothing about
    the recorded decision** — no reviewer name, no significance, no status, no public copy.

    The omission is the feature. A card that leaked "reviewed by X" would tell a candidate the
    change was worth confirming; a card that leaked the classification would end the exercise.
    """

    change = item.change
    lines = [
        f"\n[{position}/{total}] {change.id}",
        f"  jurisdiction:  {change.jurisdiction}",
        f"  document class: {change.document_class}",
        f"  source:        {change.source_id}",
        f"  url:           {change.url}",
        f"  source verification: {item.verification_status}  "
        f"(a machine fact about a socket is not a person confirming the page)",
        f"  observed:      {change.observed_at.isoformat()}",
        f"  kind:          {change.kind}",
    ]
    if change.kind is ChangeKind.POSSIBLY_REMOVED:
        lines.append("\n  --- source unreachable: escalation for human review ---")
    else:
        lines.append("\n  --- changed passages (unified diff of normalized text) ---")
    lines.extend(f"  {line}" for line in change.diff_excerpt.splitlines())
    return "\n".join(lines)


def _recorded(change: ChangeRecord) -> tuple[ReviewStatus, Significance]:
    return change.review_status, change.significance


def _agrees(candidate: CalibrationDecision, change: ChangeRecord) -> bool:
    """Two people agree when they reached the same status *and*, having confirmed, the same
    significance. A confirm/confirm pair split editorial/substantive is a divergence, and it
    is the most interesting kind — it is the exact disagreement this tool exists to surface.
    """

    recorded_status, recorded_significance = _recorded(change)
    if candidate.decision is not recorded_status:
        return False
    if candidate.decision is ReviewStatus.DISMISSED:
        return True
    return candidate.significance is recorded_significance


def agreement(
    decisions: Sequence[CalibrationDecision],
    items: Sequence[CalibrationItem],
) -> Agreement:
    """Compare a session's answers with the recorded reviews. Skips are named, not absorbed."""

    by_id = {item.change.id: item for item in items}
    answered = 0
    agreed = 0
    divergent: list[str] = []
    for decision in decisions:
        item = by_id.get(decision.change_id)
        if item is None or not item.replayable:
            # A decision against a change we did not offer cannot be scored against it.
            continue
        answered += 1
        if _agrees(decision, item.change):
            agreed += 1
        else:
            divergent.append(decision.change_id)
    return Agreement(
        answered=answered,
        agreed=agreed,
        divergent=tuple(divergent),
        skipped_evidence_pruned=tuple(
            item.change.id for item in items if item.eligibility == SKIPPED_EVIDENCE_PRUNED
        ),
        skipped_not_reviewed=tuple(
            item.change.id for item in items if item.eligibility == SKIPPED_NOT_REVIEWED
        ),
    )


def _ask_decision(
    item: CalibrationItem,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    *,
    session_id: str,
    candidate: str,
    now: Callable[[], datetime],
) -> CalibrationDecision | None:
    """One answer, or None for skip, or raise `_QuitError` for quit."""

    answer = ask("  confirm or dismiss? [c/d/s=skip/q=quit] ").strip().lower()
    if answer in {"q", "quit"}:
        raise _QuitError
    if answer in {"", "s", "skip"}:
        return None
    if answer not in _ANSWERS:
        say("  not an answer — skipping rather than guessing at what you meant.")
        return None
    status, significance = _ANSWERS[answer]
    if status is ReviewStatus.CONFIRMED:
        raw = ask("  editorial or substantive? [e/s] ").strip().lower()
        if raw not in _SIGNIFICANCES:
            say("  not a classification — skipping rather than guessing at what you meant.")
            return None
        significance = _SIGNIFICANCES[raw]
    assert significance is not None  # noqa: S101 — both branches above set it
    return CalibrationDecision(
        session_id=session_id,
        change_id=item.change.id,
        candidate=candidate,
        decision=status,
        significance=significance,
        decided_at=now(),
    )


class _QuitError(Exception):
    """The candidate asked to stop. Nothing unanswered is revealed."""


def run_calibration(
    items: Sequence[CalibrationItem],
    ask: Callable[[str], str],
    say: Callable[[str], None],
    record: Callable[[CalibrationDecision], None],
    *,
    candidate: str,
    session_id: str,
    now: Callable[[], datetime] | None = None,
) -> CalibrationOutcome:
    """Work the replay queue, one change at a time.

    The ordering inside the loop is the whole guarantee and it is worth stating: the card is
    shown, the answer is taken, the answer is **written**, and only then is the recorded
    decision revealed. `record` is called before the reveal, so a crash between the two loses
    the reveal rather than the answer, and there is no code path on which a candidate sees the
    recorded decision for a change they have not yet answered.

    Quitting reveals nothing about the remainder, and the outcome says the session was not
    completed so a partial agreement figure is never read as a whole one.
    """

    clock = now or (lambda: datetime.now(UTC))
    if not candidate.strip():
        raise CalibrationError(
            "calibration requires a named human candidate — this measures whether two named "
            "people mean the same thing, and an anonymous one measures nothing"
        )
    if not canonical_actor(candidate):
        raise CalibrationError("calibration candidate is not a valid bounded name")

    replayable = [item for item in items if item.replayable]
    say(
        f"calibrate: {len(replayable)} reviewed change(s) to replay for {candidate.strip()}.\n"
        f"You are answering the same question `review` asks: confirm or dismiss, and if you\n"
        f"confirm, is it editorial or substantive. The recorded decision is hidden until\n"
        f"yours is written, and quitting reveals nothing you have not answered.\n"
        f"Nothing you record here is publishable or read by `publish`."
    )

    decisions: list[CalibrationDecision] = []
    completed = True
    for position, item in enumerate(replayable, start=1):
        say(replay_card(item, position=position, total=len(replayable)))
        try:
            decision = _ask_decision(
                item, ask, say, session_id=session_id, candidate=candidate, now=clock
            )
        except _QuitError:
            say(
                "calibrate: stopping. The recorded decisions for the changes you did not "
                "answer are NOT shown — revealing them would spend the calibration set."
            )
            completed = False
            break
        if decision is None:
            continue
        record(decision)
        decisions.append(decision)
        recorded_status, recorded_significance = _recorded(item.change)
        verdict = "agrees" if _agrees(decision, item.change) else "differs"
        say(
            f"  recorded: you said {decision.decision}/{decision.significance}; "
            f"the review says {recorded_status}/{recorded_significance} ({verdict})"
        )

    result = agreement(decisions, items)
    for line in result.lines():
        say(line)
    if not completed:
        say(
            "  Session incomplete — the figure above covers only the changes answered before "
            "you stopped, and the rest of the set is unspent."
        )
    return CalibrationOutcome(
        session_id=session_id,
        candidate=canonical_actor(candidate),
        agreement=result,
        completed=completed,
    )
