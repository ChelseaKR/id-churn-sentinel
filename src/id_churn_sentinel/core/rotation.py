"""Slow-rotation detection — the half of the false-drift signal `--twice` cannot see.

`sentinel sources check --twice` fetches a candidate twice and names anything whose hash
moves between the two. It works, and it found three sources that would otherwise have
alerted every week forever: `dpbh.nv.gov` re-rolling a "Nevada state symbol" trivia block on
every request, `azdot.gov/mvd` sampling a links widget, `nebraskajudicial.gov` shuffling its
rules list.

**And the roadmap states its limit plainly: `--twice` is necessary and not sufficient.** It
catches per-*request* rotation. A widget that re-rolls hourly, daily, or per cache generation
hashes identically across two back-to-back fetches and still drifts week over week —
`azdot.gov/mvd` did exactly that and was caught by the weekly run, not by the check. And a
page that *starts* rotating after it is registered is not covered by a registration-time
check at all. Until this module, the only thing that noticed either case was a reviewer
dismissing the same source, week after week, as `editorial` — a signal that existed entirely
inside one person's memory and died with their attention.

So this reads that signal out of the record instead. `docs/RESPONSIBLE-TECH-AUDITS.md` §A
already names it: *"the other half of the signal is a reviewer dismissing the same source as
`editorial` twice running"*.

**Four things this module does not do, and each one is the point.**

1. **It does not suppress anything.** A flagged source keeps being watched and keeps
   producing change records, every run, unchanged. Auto-muting a "rotating" source is the
   one response that could hide a real policy change behind a heuristic — the wrong "no
   change" §9 puts first — and the whole reason the fix for a rotating page is *stop
   watching it honestly*, decided by a person, rather than *keep watching it quietly*.
2. **It does not normalize anything.** The rotating text is real, visible page text. A
   normalizer that guesses which visible text does not count is one that can hide a real
   change (RESPONSIBLE-TECH §A). Nothing here touches the normalizer, the hash, or the diff.
3. **It does not classify.** Every number below is a count of decisions *humans* already
   made and signed. The tool is reporting its own review record back to the operator, not
   forming an opinion about a page — and emphatically not about what a change means.
   `make no-auto-classification` covers this explicitly.
4. **It does not decide.** Repeated editorial dismissals are what a rotating page looks like
   from the review queue. They are also what a page that is genuinely edited every week
   looks like. Both readings are stated; neither is chosen. Telling them apart means reading
   the diffs, and that is a person's job.

Nothing here reaches a published artifact either. This is registry health — a fact about
*us*, about which pages we have proven able to watch honestly — and a consumer polling
`changes.json` is owed observations about government pages, not our internal misgivings
about our own source list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.store import SnapshotStore

__all__ = [
    "ROTATION_THRESHOLD",
    "RotationReport",
    "RotationSuspect",
    "rotation_report",
]

# How many consecutive editorial dismissals on one source make it worth a person's attention.
#
# This is a policy in units of *consecutive reviewed observations*, and it is deliberately
# not dressed up as a measurement. `REMOVAL_THRESHOLD` taught this repo the cost of the
# alternative: a constant that counted runs while its own comment described weeks, defended
# rather than measured, and unmeasurable afterwards because no history had been retained
# (`docs/THRESHOLD-EVIDENCE.md`).
#
# Two, rather than a number invented here, because two is the number the project already
# committed to in writing: RESPONSIBLE-TECH §A says the other half of the false-drift signal
# is "a reviewer dismissing the same source as `editorial` twice running". Picking three to
# feel safer would have replaced a stated commitment with an unmeasured preference. It is
# also cheap to be wrong in this direction: crossing it prints a paragraph asking a person to
# look, and nothing else happens — no source is muted, no record is suppressed, no build
# fails. `--threshold` raises it for an operator whose corpus says two is noisy, and the
# streak length is always printed, so the raw evidence outlives the constant.
ROTATION_THRESHOLD = 2


@dataclass(frozen=True, slots=True)
class RotationSuspect:
    """One source whose review record looks like rotation — with the evidence, not a verdict.

    `streak` is the number of consecutive most-recent *reviewed* content-drift observations
    on this source that a named human dismissed as `editorial`. `span` is how much real time
    those observations cover, and it is reported for the reason the removal escalation
    learned the hard way: a count of observations is not a length of time, and three
    dismissals inside one afternoon of back-to-back runs is a different fact from three
    dismissals over three weeks. It is stated rather than gated on — there is no evidence yet
    for what the right duration would be, and inventing one would repeat the mistake
    `docs/THRESHOLD-EVIDENCE.md` documents.

    `pending` counts observations on this source that no one has reviewed. They are not part
    of the streak — an unreviewed record is not a decision, and treating a fresh alert as
    though it broke a reviewer's pattern would erase a real signal — but they are reported,
    because a streak computed over an incomplete queue is a partial view and should say so.
    """

    source_id: str
    url: str
    jurisdiction: str
    document_class: str
    streak: int
    span: timedelta
    first_dismissed_at: datetime
    last_dismissed_at: datetime
    change_ids: tuple[str, ...]
    reviewers: tuple[str, ...]
    pending: int


@dataclass(slots=True)
class RotationReport:
    """Every source whose trailing editorial-dismissal streak has reached the threshold."""

    threshold: int = ROTATION_THRESHOLD
    suspects: list[RotationSuspect] = field(default_factory=list)
    reviewed_sources: int = 0

    def summary(self) -> str:
        if not self.suspects:
            return (
                f"rotation: no source has {self.threshold} consecutive editorial dismissal(s) "
                f"({self.reviewed_sources} source(s) with any reviewed observation)"
            )
        return (
            f"rotation: {len(self.suspects)} source(s) with {self.threshold} or more "
            f"consecutive editorial dismissals, out of {self.reviewed_sources} with any "
            f"reviewed observation"
        )


def rotation_report(store: SnapshotStore, *, threshold: int = ROTATION_THRESHOLD) -> RotationReport:
    """Read the review record and name the sources a reviewer keeps dismissing as editorial.

    Read-only, by construction: this function is handed a store and calls nothing on it that
    writes. That is not incidental tidiness — a detector that could act on its own conclusion
    is a detector that could quietly stop watching a page, and the correct response to a
    source that cannot be watched honestly is a *person* swapping the URL or recording a gap.
    """
    if threshold < 1:
        raise ValueError("a rotation threshold below 1 would flag every reviewed source")

    by_source: dict[str, list[ChangeRecord]] = {}
    for change in store.changes():
        if change.kind is not ChangeKind.CONTENT_DRIFT:
            # A `possibly_removed` escalation is not an observation about page content; it is
            # an observation about silence. Folding one into a rotation streak would mix two
            # different facts and could only ever mislead.
            continue
        by_source.setdefault(change.source_id, []).append(change)

    report = RotationReport(threshold=threshold)
    for source_id, changes in sorted(by_source.items()):
        # `store.changes()` yields newest first, and the streak is defined from the newest
        # decision backwards, so the list is already in the order this walk needs.
        streak = _trailing_editorial_dismissals(changes)
        if any(change.review_status is not ReviewStatus.UNREVIEWED for change in changes):
            report.reviewed_sources += 1
        if len(streak) < threshold:
            continue
        report.suspects.append(_suspect(source_id, streak, changes))

    report.suspects.sort(key=lambda suspect: (-suspect.streak, suspect.source_id))
    return report


def _trailing_editorial_dismissals(newest_first: list[ChangeRecord]) -> list[ChangeRecord]:
    """The unbroken run of most-recent editorial dismissals, newest first.

    What breaks the run is as load-bearing as what extends it:

    * a **confirmed** observation breaks it, and that is the important one — a human deciding
      the page really did move is the strongest available evidence that it is not merely
      churning, so the streak restarts from there;
    * a dismissal recorded as `unclassified` breaks it too. It is a real decision, but it is
      not the decision this signal is made of, and quietly counting it would widen the
      vocabulary of a published safety claim by inference. The cost is a blind spot, stated
      here rather than hidden: a reviewer who never classifies their dismissals never trips
      this detector, and `sentinel review --significance editorial` is what closes it;
    * an **unreviewed** observation neither extends nor breaks it, and is skipped. It is not a
      decision at all, and letting this week's fresh alert reset a real pattern would delete
      the signal exactly when the queue is backed up — which is when it matters most.
    """
    streak: list[ChangeRecord] = []
    for change in newest_first:
        if change.review_status is ReviewStatus.UNREVIEWED:
            continue
        if (
            change.review_status is ReviewStatus.DISMISSED
            and change.significance is Significance.EDITORIAL
        ):
            streak.append(change)
            continue
        break
    return streak


def _suspect(
    source_id: str, streak: list[ChangeRecord], changes: list[ChangeRecord]
) -> RotationSuspect:
    oldest, newest = streak[-1], streak[0]
    return RotationSuspect(
        source_id=source_id,
        url=newest.url,
        jurisdiction=newest.jurisdiction,
        document_class=newest.document_class,
        streak=len(streak),
        span=_span(oldest, newest),
        first_dismissed_at=_decided_at(oldest),
        last_dismissed_at=_decided_at(newest),
        change_ids=tuple(change.id for change in reversed(streak)),
        # De-duplicated, order preserved oldest-first: "three dismissals by one reviewer" and
        # "three dismissals by three reviewers" are different facts, and the second is the
        # stronger signal — it is not one tired person's habit.
        reviewers=tuple(dict.fromkeys(_reviewer(change) for change in reversed(streak))),
        pending=sum(1 for change in changes if change.review_status is ReviewStatus.UNREVIEWED),
    )


def _span(oldest: ChangeRecord, newest: ChangeRecord) -> timedelta:
    """Elapsed time across the streak, measured on the *observations*, not the decisions.

    Deliberately: the question the operator is asking is how long the page has been behaving
    this way, and a reviewer working a fortnight's backlog in one sitting would otherwise
    make three weeks of drift look like an afternoon of it.
    """
    return newest.observed_at - oldest.observed_at


def _decided_at(change: ChangeRecord) -> datetime:
    """When the dismissal was recorded, falling back to the observation it decided.

    A projected record that reached `dismissed` always carries `reviewed_at`; the fallback
    keeps a hand-repaired or partially-migrated store from raising here rather than reporting
    a source that is genuinely worth someone's attention.
    """
    return change.reviewed_at or change.observed_at


def _reviewer(change: ChangeRecord) -> str:
    return change.reviewer or "(unnamed — a repaired or legacy record)"
