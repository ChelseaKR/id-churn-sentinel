"""Change detection — fetch, normalize, hash, compare, and *diff*.

The diff is the whole point. The prior art (`trans-docs-navigator/scripts/source-watch.ts`)
reports "this URL changed, re-verify these records", which is genuinely useful and is
where this design came from — but it hands a human a URL and a shrug. The human then has
to eyeball a 12,000-word DMV page against their memory of it. In practice that means the
alert gets acknowledged and the page does not actually get re-read, which is the same
failure the incumbents have, just automated.

So this module carries the observation one step further: on drift, it computes a unified
diff of the *normalized* text between the previous snapshot and the current one, and hands
the reviewer the changed passages. "Texas added a sentence about a court order" is a thing
a person can review in thirty seconds. "texas.gov changed" is not.

Three disciplines, inherited and non-negotiable:

1. **A fetch failure is never drift.** An unreachable source carries its previous hash
   forward untouched and is reported as `unreachable`. No *content* change record is ever
   minted from a failure, at any streak length. (`source-watch.ts`: *"keep the old baseline;
   an outage is not a content change"*.)
2. **A first sighting is never drift.** With no previous snapshot there is no baseline to
   diff against, so the fetch is recorded as the baseline and reported as `new`. The same
   holds when a maintainer *corrects a registry URL*: the previous snapshot belongs to a
   different page, and subtracting one document from an unrelated one is not drift
   detection. That case is re-baselined and reported as `rebaselined`, never as a change.
3. **Detection never classifies.** Every record `watch()` produces is `unclassified` /
   `unreviewed`, because neither :meth:`ChangeRecord.observed` nor
   :meth:`ChangeRecord.possibly_removed` is given the vocabulary to say anything else.

**And one discipline that had to be added, because its absence was a safety gap.**
Rule 1 is right, and on its own it was not enough. Holding the old baseline is the correct
response to an outage — and a page that has been *taken down* looked exactly like an
outage, forever. The tool would hold a dead page's baseline indefinitely, report it as
"unreachable" in a line of console output nobody keeps, and publish nothing. That is a
wrong "no change": a government page about trans identity documents disappearing is itself
a policy signal (institutions do scrub this content), and answering a long silence with
silence is the failure mode `docs/RESPONSIBLE-TECH-AUDITS.md` §A is written about.

So `watch()` counts *consecutive* failures per source, persists the streak, resets it on
any success, and after `removal_threshold` runs escalates the source to a distinct
`possibly_removed` change record that a human must review. The escalation is emphatically
**not** a classification: it does not say the page was removed. It says we could not fetch
it N times running, hands over the literal error string, and names the three readings —
removed, blocked, or down — without choosing between them. A 404 and a 403 and a fortnight
of timeouts all arrive here, and telling them apart is a person's job.

**And a second one, for the same reason: its absence was a safety gap.**
A hash only means something *relative to the normalizer that produced it*. Two hashes
computed under different `(normalizer_version, extractor_version)` contracts are not
comparable, and subtracting one from the other answers a question nobody asked — it
measures a change in *us*, not a change in the world. Until this was fixed the detector
compared them anyway, so bumping a normalizer version turned every affected page into a
change record whose diff was a re-normalization artifact, minted by the tool and handed to
a reviewer as drift. `passage-text-v2` (2026-08-01) made that live: v1 baselines now sit in
operators' stores alongside a v2 normalizer.

The fix is neither of the two obvious ones. **Refusing** the comparison — the response to a
corrected registry URL, below — would be wrong here, because it fails *unsafe*: it would
blind every source with a v1 baseline for a full pass, and a wrong "no change" about a
government page that may have been scrubbed is the exact failure `docs/RESPONSIBLE-TECH-AUDITS.md`
§A is written about. **Labelling** the comparison would be wrong too: it emits one flagged
record per affected source, and a caveat attached to a wall of alarms is a caveat that gets
scrolled past.

Neither is necessary, because unlike a corrected URL — where the old evidence is about a
*different page* and is genuinely unrecoverable — a version bump leaves the evidence intact.
The store retains the baseline's raw bytes. So the detector **re-derives the baseline under
today's contract** before comparing: it re-normalizes the retained bytes with the current
normalizer and compares that against the current fetch. Both sides of every comparison are
then products of the same normalizer, which is the only condition under which a hash
comparison means anything.

That makes a version bump *structurally incapable* of manufacturing drift, and equally
incapable of hiding it: a page that really did change during the same pass still produces a
change record, and its diff is a like-for-like diff rather than a normalization artifact.
The one case where re-derivation is impossible — retained bytes that cannot reproduce the
recorded baseline — claims no drift, re-baselines, and is reported in its own bucket, because
"we cannot make this comparison valid" must never be dressed up as "the page changed".

**And a third, for the same reason: its absence was a safety gap (issue #19).**
`sha256("")` is a legitimate detection hash — it is what a JS shell, an empty 200, or a
bot-wall serves once its markup and scripts are stripped, and two *different* sources with
no page text share that one hash. Nothing before this discipline distinguished it from a real,
stable measurement: a source that first serves no text is quietly baselined as `new`, and every
week it keeps serving no text, the (identical) hash matches and it reports `unchanged` — the
loudest possible silence, forever, about a page nobody has actually watched. `unchanged` and
`new` both mean "we have a comparison and it means something"; neither is true of a page with
no extractable text, and the discipline that protects a corrected URL and a version bump
applies here too: refuse to conclude *anything* from that comparison, and say so every single
time, not once. So a text/HTML fetch that normalizes to zero passages is checked and routed to
its own bucket, `no_text`, before baselining or comparison — win, lose, or draw, that source's
result this run is "we could not measure this," reported loudly, every run, for as long as it
persists. (Binary content is exempt: an *opaque* zero-length normalized text is its documented,
honest behaviour, not a symptom — see :func:`normalize.content_evidence`.)

**Three consequences of that discipline, each of which was still a live defect after the
bucket existed**, because a bucket in a report is not the same thing as a refusal to record:

1. **Nothing is written to the snapshot store.** The bucket alone left the empty fetch being
   recorded as a snapshot first and *then* routed, so `sha256("")` still became the source's
   latest snapshot — which is to say its baseline, the thing `sentinel baseline write` commits
   and the thing next week's fetch is compared against. Three lies followed from that one row:
   the committed baseline file gained a hash of nothing; a page that recovered its text was
   reported as `changed` against nothing, minting drift out of a recovery; and five blind runs
   evicted the last real bytes through snapshot retention, destroying the evidence that would
   have made the comparison possible again. So the check now happens *before* `record_snapshot`,
   and an unmeasurable fetch leaves the last real baseline exactly where it was.
2. **The failure streak is not reset.** `record_success` means "this source answered, so
   whatever was wrong is over". A page serving a bot-wall has not answered in any sense this
   tool cares about, and exonerating it would let a source sit permanently blind with a clean
   health record. Nor is a failure recorded: we did reach the host, and inventing a fetch
   failure would eventually escalate a `possibly_removed` record whose stated evidence — N
   consecutive *failed fetches* — never happened. The streak is left exactly as it was, which
   is the only honest option: this run neither confirms nor clears anything.
3. **The run is not `quiet`.** `quiet` is the state that publishes as "the latest watch
   completed for every eligible source and created no observations", and that sentence is
   false for a run that could not measure a source at all. A run with an unmeasurable source
   is `partial` — the state that already exists for "we did not get a comparable observation
   for every eligible source" — and the store now refuses to record it as anything else.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from id_churn_sentinel.core.changes import ChangeRecord
from id_churn_sentinel.core.eligibility import (
    SourceEligibility,
    eligibility_report,
    registry_revision,
)
from id_churn_sentinel.core.fetch import Fetcher, FetchResult
from id_churn_sentinel.core.normalize import (
    CURRENT_CONTRACT,
    EXTRACTOR_VERSION,
    NORMALIZER_VERSION,
    ContentEvidence,
    ContentKind,
    content_evidence,
    content_hash,
    kind_for_content_type,
    passages,
    representation_contract,
)
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import (
    RUN_COMPLETE,
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_QUIET,
    AttemptEvidence,
    RunSourceInput,
    Snapshot,
    SnapshotStore,
)

__all__ = [
    "DIFF_CONTEXT_LINES",
    "MAX_DIFF_EXCERPT_CHARS",
    "REMOVAL_THRESHOLD",
    "StabilityReport",
    "WatchReport",
    "check_stability",
    "diff_excerpt",
    "watch",
    "watch_registry",
]

DIFF_CONTEXT_LINES = 2
MAX_DIFF_EXCERPT_CHARS = 4000

# Consecutive failed fetches before a source escalates to `possibly_removed`.
#
# Three, at the weekly cadence this tool runs at, means roughly three weeks of a source
# answering nothing at all before a human is asked to look at it. That is the number the
# two failure modes have to be traded off against each other:
#
#   Too low, and every routine weekend outage or WAF mood swing mints an alarm, the
#   reviewer learns the escalations are noise, and they start closing them unread — at
#   which point the mechanism is worse than not having it, because it *looks* like
#   someone is watching.
#
#   Too high, and a page that was quietly deleted keeps serving its stale baseline for
#   months while the feed says nothing. Silence is the failure mode this whole escalation
#   exists to fix; a threshold of 12 would technically satisfy the code and defeat the
#   purpose.
#
# Three is a starting guess, not a finding. M2 measures real outage lengths against real
# government hosts, and this number should be re-derived from that data rather than
# defended. It is a module constant and a CLI flag precisely so it is cheap to change.
REMOVAL_THRESHOLD = 3


@dataclass(slots=True)
class WatchReport:
    """What one watch pass saw. Every source lands in exactly one bucket, and the buckets
    are disjoint by construction — an unreachable source cannot also be a changed one.

    `unreachable` and `possibly_removed` are the one deliberate exception: a source that
    escalates appears in *both*, because it is still unreachable (that is the fact) and it
    is now also an escalation (that is the consequence). `total` counts it once.

    `renormalized` and `unrenormalizable` are *not* drift buckets and are counted alongside
    `unchanged`: a source whose baseline was recorded under an older representation contract
    lands in one of them only when the re-derived comparison found no content change. A
    source whose content really did change lands in `changed`, whatever contract its baseline
    was recorded under.

    `no_text` is the third non-drift bucket (issue #19): a successful text/HTML fetch whose
    normalized text has zero passages. It preempts `new`/`unchanged`/`changed`/`renormalized`
    entirely — no snapshot is recorded, no baseline is written or overwritten, no comparison is
    made, and no drift is claimed either way, for as long as the condition holds. Unlike
    `unrenormalizable`, this is not a one-time transition; a source that keeps serving no text
    lands here on *every* run, which is the point: the old behaviour let identical "nothing"
    hash-match itself into a permanently silent `unchanged`. A run containing one is `partial`,
    never `quiet`."""

    changed: list[ChangeRecord] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    rebaselined: list[tuple[str, str, str]] = field(default_factory=list)
    renormalized: list[tuple[str, str, str]] = field(default_factory=list)
    unrenormalizable: list[tuple[str, str]] = field(default_factory=list)
    no_text: list[tuple[str, str]] = field(default_factory=list)
    unreachable: list[tuple[str, str]] = field(default_factory=list)
    possibly_removed: list[ChangeRecord] = field(default_factory=list)
    run_id: str = ""
    state: str = ""
    eligible_source_ids: tuple[str, ...] = ()
    ineligible: tuple[SourceEligibility, ...] = ()

    @property
    def total(self) -> int:
        return (
            len(self.changed)
            + len(self.unchanged)
            + len(self.new)
            + len(self.rebaselined)
            + len(self.renormalized)
            + len(self.unrenormalizable)
            + len(self.no_text)
            + len(self.unreachable)
        )

    def summary(self) -> str:
        escalated = (
            f", {len(self.possibly_removed)} escalated to possibly_removed"
            if self.possibly_removed
            else ""
        )
        rebaselined = (
            f", {len(self.rebaselined)} re-baselined (registry URL changed)"
            if self.rebaselined
            else ""
        )
        renormalized = (
            f", {len(self.renormalized)} unchanged after re-deriving the baseline under "
            f"{CURRENT_CONTRACT} (normalizer version changed, not drift)"
            if self.renormalized
            else ""
        )
        unrenormalizable = (
            f", {len(self.unrenormalizable)} re-baselined (baseline not re-derivable under "
            f"{CURRENT_CONTRACT}, no drift claimed)"
            if self.unrenormalizable
            else ""
        )
        no_text = (
            f", {len(self.no_text)} served NO extractable text (not baselined, no drift "
            f"claimed — see below)"
            if self.no_text
            else ""
        )
        return (
            f"{self.total} source(s): {len(self.changed)} changed, "
            f"{len(self.unchanged)} unchanged, {len(self.new)} new baseline, "
            f"{len(self.unreachable)} unreachable (not drift)"
            f"{rebaselined}{renormalized}{unrenormalizable}{no_text}{escalated}"
        )


@dataclass(slots=True)
class StabilityReport:
    """What `check_stability` saw: which sources hash the same twice, and which do not.

    See :func:`check_stability` for why a source that does not is a *defect in the registry*
    rather than a finding about the world.

    `no_text` is the same refusal `WatchReport.no_text` and `BaselineReport.no_text` make, in
    the third and last place a comparison happens, and it is emphatically **not** a `stable`
    result. A text/HTML fetch that normalizes to zero passages hashes to `sha256("")`, and
    `sha256("") == sha256("")` — so a JS shell, an empty 200 and a bot-wall each match
    themselves perfectly across two fetches and were reported as `stable`, which is the most
    reassuring word this command prints, about the one condition it must never print it for.
    It named nothing on stdout either, because only `UNSTABLE` and `unreach` get a line, so a
    blind page passed the check *silently*. "Nothing rotates on this page" and "there is
    nothing on this page" are different sentences, and only the first is a reason to add a
    source to the registry."""

    stable: list[str] = field(default_factory=list)
    unstable: list[tuple[str, str, str]] = field(default_factory=list)
    no_text: list[tuple[str, str]] = field(default_factory=list)
    unreachable: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.stable) + len(self.unstable) + len(self.no_text) + len(self.unreachable)

    def summary(self) -> str:
        no_text = (
            f", {len(self.no_text)} served NO extractable text (NOT compared, stability unknown)"
            if self.no_text
            else ""
        )
        return (
            f"{self.total} source(s): {len(self.stable)} stable, "
            f"{len(self.unstable)} UNSTABLE (false-drift by construction)"
            f"{no_text}, {len(self.unreachable)} unreachable"
        )


def check_stability(sources: Iterable[Source], fetcher: Fetcher) -> StabilityReport:
    """Fetch each source **twice, back to back**, and report any whose normalized hash
    differs between the two.

    This exists because of a finding, not a hunch. The first real two-run pass over the
    registry (2026-07-13, the runs minutes apart) produced two "changes" that were not
    changes at all:

    * `dpbh.nv.gov` renders a rotating "Nevada state symbol" trivia block into its footer —
      *state fish → state reptile* — and re-rolls it on **every single request**. Watching
      that page would mint a change record every week, forever, whose diff is a fact about
      the desert tortoise.
    * `azdot.gov/mvd` renders a randomly-sampled "frequently viewed links" list.

    Neither is markup churn, so the normalizer cannot save us: the rotating text is real,
    visible page text, structurally indistinguishable from policy text. Stripping it would
    require per-source content selectors, and a normalizer that guesses which visible text
    "does not count" is a normalizer that can *hide a real change* — the one failure this
    repo will not trade for tidiness (RESPONSIBLE-TECH-AUDITS §A).

    So the answer is not to normalize harder. It is to **not watch a page that cannot be
    watched honestly**, and to be able to *find out* which pages those are before they
    reach a reviewer. A sentinel that cries wolf gets muted, and a muted sentinel is worse
    than none.

    **And a page with no extractable text is not judged at all** — the same refusal
    `watch()` and `check_baselines()` make (issue #19), made here because this was the one
    comparison in the codebase still missing it. `sha256("")` is what a JS shell, an empty
    200 and a bot-wall all normalize to, and it compares equal to itself, so every blind page
    passed this check as `stable` — silently, since only `UNSTABLE` and `unreach` print a
    line. That is the worst possible place for that particular false all-clear: CLAUDE.md
    guardrail #7 makes this command the gate a maintainer runs *before* adding a source, so
    the check said "safe to watch" about exactly the pages `watch()` can never observe. Such a
    source is routed to `no_text` before either hash is compared, and never to `stable`: we
    did not find that the page is stable, we found that we cannot read it.

    Checked on the first fetch, which also means the second one is not spent: a page we
    already know we cannot read has nothing to tell us on a re-fetch, and this command's
    stated cost is that it doubles the load on the host.

    Two limits, stated plainly:

    * **A pass here is not a guarantee.** It catches per-*request* rotation. A page that
      re-rolls a widget hourly, daily, or per-cache-generation will look stable across two
      back-to-back fetches and still drift week over week — `azdot.gov/mvd` did exactly
      that, and was caught by the weekly run, not by this check. Consecutive `editorial`
      dismissals on the same source are the other half of the signal.
    * **It doubles the load on the host for the sources it checks.** It is an operator's
      diagnostic, run when the registry changes — never the weekly job.
    """
    report = StabilityReport()
    for source in sources:
        first = fetcher.fetch(source.url)
        if not first.ok:
            report.unreachable.append((source.id, first.error or "unknown error"))
            continue

        first_hash, first_text = content_hash(first.body, first.content_type)
        if _is_unmeasurable(first_text, first.content_type):
            # Refused before the second fetch, not after: the comparison this command exists
            # to make is between two readings of a page, and there is no reading here. A
            # second request would buy the host's bandwidth and answer nothing.
            report.no_text.append((source.id, source.url))
            continue

        second = fetcher.fetch(source.url)
        if not second.ok:
            report.unreachable.append((source.id, second.error or "unknown error"))
            continue

        second_hash, second_text = content_hash(second.body, second.content_type)
        if _is_unmeasurable(second_text, second.content_type):
            # Readable once, blind once. The hashes necessarily differ — a real digest
            # against `sha256("")` — and reporting that as UNSTABLE would name a rotating
            # widget that is not there. A page that intermittently serves nothing is a page
            # we cannot read, not a page that churns.
            report.no_text.append((source.id, source.url))
            continue

        if first_hash == second_hash:
            report.stable.append(source.id)
        else:
            report.unstable.append((source.id, first_hash, second_hash))
    return report


def diff_excerpt(
    previous_text: str,
    current_text: str,
    *,
    source_url: str,
    renormalized_from: str | None = None,
) -> str:
    """A unified diff of the normalized passages, truncated to a reviewable size.

    Binary sources (PDFs) normalize to empty text and cannot be diffed. Rather than emit a
    misleading empty diff, say so plainly — the reviewer needs to know that the honest next
    step is to open both documents themselves.

    `renormalized_from` names the contract the baseline was *recorded* under when it had to
    be re-derived from retained bytes to be comparable. It is stated to the reviewer rather
    than assumed harmless: the passages below are content drift precisely because both sides
    came out of the same normalizer, and a reviewer is entitled to know that the left-hand
    side is a re-derivation rather than the text as stored.
    """
    note = _renormalization_note(renormalized_from)
    if not previous_text and not current_text:
        return note + (
            "(no text diff available — this source is a binary document, e.g. a PDF; its "
            f"bytes changed. Open {source_url} and compare against the retained snapshot.)"
        )

    lines = difflib.unified_diff(
        passages(previous_text),
        passages(current_text),
        fromfile="previous",
        tofile="current",
        lineterm="",
        n=DIFF_CONTEXT_LINES,
    )
    text = "\n".join(lines)
    if not text:
        # Reachable when the hash changed but normalized text did not — i.e. a binary
        # source, or a change confined to bytes the normalizer strips. Both mean: a human
        # has to look at the artifact, not at our summary of it.
        return note + (
            "(the content hash changed but the normalized text did not differ — the change "
            f"is in markup or in non-text bytes. Open {source_url} to inspect.)"
        )
    if len(text) > MAX_DIFF_EXCERPT_CHARS:
        text = (
            text[:MAX_DIFF_EXCERPT_CHARS]
            + f"\n… (diff truncated at {MAX_DIFF_EXCERPT_CHARS} chars; "
            + "run `sentinel diff <change-id>` for the full text)"
        )
    # Prepended *after* truncation, deliberately: the provenance of the left-hand side is
    # not a detail that may be cut to fit a character budget.
    return note + text


def _renormalization_note(renormalized_from: str | None) -> str:
    if renormalized_from is None:
        return ""
    return (
        f"(baseline re-derived from its retained bytes: it was recorded under "
        f"{renormalized_from} and has been re-normalized under {CURRENT_CONTRACT} so that "
        f"both sides of this diff come from the same normalizer. The passages below are "
        f"therefore content drift, not a normalization artifact.)\n\n"
    )


@dataclass(frozen=True, slots=True)
class _ComparableBaseline:
    """A baseline restated under today's representation contract, ready to compare.

    `renormalized_from` is `None` when nothing had to be restated — the overwhelmingly
    common case, and the one that must stay byte-identical to the old behaviour.
    """

    content_sha256: str
    normalized_text: str
    renormalized_from: str | None


def _comparable_baseline(
    previous: Snapshot, content_type: str | None
) -> _ComparableBaseline | None:
    """Restate a stored baseline under the current contract, or return `None` if it cannot be.

    The re-derivation deliberately routes the retained bytes through the *current* fetch's
    `Content-Type`, not a remembered one, because the comparison being set up is "what would
    the baseline bytes hash to if we saw them today, exactly as we are treating today's
    bytes". Routing both sides identically is the whole property. A source that switched from
    HTML to PDF therefore re-derives as bytes on both sides and reports honest drift, rather
    than diffing extracted text against opaque bytes.

    `None` means the recorded baseline cannot be reproduced: the snapshot claims normalized
    text but retains no bytes to re-normalize. There is no correct hash to compare in that
    case, and inventing one would be exactly the fabricated-evidence failure the store's
    triggers exist to prevent.
    """
    recorded = representation_contract(previous.normalizer_version, previous.extractor_version)
    if recorded == CURRENT_CONTRACT:
        return _ComparableBaseline(previous.content_sha256, previous.normalized_text, None)
    if not previous.raw_bytes and previous.normalized_text:
        return None
    evidence = content_evidence(previous.raw_bytes, content_type)
    return _ComparableBaseline(evidence.detection_sha256, evidence.normalized_text, recorded)


def _is_unmeasurable(normalized_text: str, content_type: str | None) -> bool:
    """True when a successful fetch produced nothing this tool can compare (issue #19).

    Binary content is excluded, and the exclusion is not a special case bolted on: an opaque
    body's normalized text is empty *by design* (`EXTRACTOR_VERSION = "none-v1"` — there is no
    PDF extractor), its detection hash covers the raw bytes rather than the empty string, and
    comparing those hashes week to week is a real, honest measurement. A text/HTML body that
    normalizes to zero passages is the opposite: the hash covers nothing, every page with no
    text shares it, and comparing it to itself proves only that we are still not reading
    anything.
    """
    if kind_for_content_type(content_type) == ContentKind.BINARY:
        return False
    return not passages(normalized_text)


def _watch_authorized_sources(
    sources: Iterable[Source],
    store: SnapshotStore,
    fetcher: Fetcher,
    *,
    removal_threshold: int = REMOVAL_THRESHOLD,
    run_id: str | None = None,
) -> WatchReport:
    """Low-level comparison over an already-authorized source set.

    Production callers use :func:`watch_registry`, which computes that set through the
    canonical dated eligibility predicate and persists a run receipt before entering this
    function.  Keeping the comparison primitive separate makes offline detector fixtures
    small; it is not an alternate operator path.

    `fetcher` is injected, which is what makes the whole tool testable with no network:
    the suite passes a dict-backed stub, CI passes nothing at all, and `sentinel watch`
    passes an :class:`~id_churn_sentinel.core.fetch.HttpFetcher`.
    """
    report = WatchReport()

    for source in sources:
        if run_id is not None:
            store.begin_fetch_attempt(run_id, source_id=source.id, url=source.url)
        result = fetcher.fetch(source.url)
        evidence: ContentEvidence | None = None
        if result.ok:
            evidence = content_evidence(result.body, result.content_type)
        # Decided here, before anything is stored, because every write below depends on it:
        # a fetch that produced no comparable observation must not reach the snapshot table,
        # must not clear the health streak, and must not let the run finish `quiet`.
        measured = evidence is not None and not _is_unmeasurable(
            evidence.normalized_text, result.content_type
        )
        if run_id is not None:
            store.finish_fetch_attempt(
                run_id,
                source_id=source.id,
                ok=result.ok,
                http_status=result.status,
                content_type=result.content_type or "",
                normalizer_version=NORMALIZER_VERSION if result.ok else "",
                extractor_version=EXTRACTOR_VERSION if result.ok else "",
                error=result.error or "",
                evidence=_attempt_evidence(result, evidence),
                measured=measured,
                completed_at=result.fetched_at,
            )

        if not result.ok:
            _handle_failure(
                source,
                store,
                report,
                result.error,
                result.status,
                removal_threshold,
                run_id=run_id,
            )
            continue

        if evidence is None:  # pragma: no cover - guarded by result.ok above
            raise AssertionError("successful fetch did not produce normalized content")

        if not measured:
            # Zero passages out of a page that promised text. There is nothing here to
            # baseline and nothing to compare, on the first sighting or the hundredth, so
            # this source's run ends here — before `record_success` (which would exonerate a
            # source we did not observe) and before `record_snapshot` (which would overwrite
            # the last real baseline with a hash of nothing). Reported every run it recurs.
            report.no_text.append((source.id, source.url))
            continue

        new_hash, normalized = evidence.detection_sha256, evidence.normalized_text
        previous = store.latest_snapshot(source.id)

        # The source answered, so whatever was wrong is over. Reset the streak *before*
        # anything else: a source that is serving bytes is not a source that was removed,
        # and leaving a stale streak standing would let old flakiness escalate a healthy page.
        store.record_success(source.id)

        store.record_snapshot(
            source_id=source.id,
            url=source.url,
            fetched_at=result.fetched_at,
            http_status=result.status,
            content_sha256=new_hash,
            raw_bytes=result.body,
            normalized_text=normalized,
            normalizer_version=NORMALIZER_VERSION,
            extractor_version=EXTRACTOR_VERSION,
        )

        _compare_against_baseline(
            source,
            store,
            report,
            previous,
            new_hash=new_hash,
            normalized=normalized,
            content_type=result.content_type,
            observed_at=result.fetched_at,
            run_id=run_id,
        )

    return report


def _compare_against_baseline(
    source: Source,
    store: SnapshotStore,
    report: WatchReport,
    previous: Snapshot | None,
    *,
    new_hash: str,
    normalized: str,
    content_type: str | None,
    observed_at: datetime,
    run_id: str | None,
) -> None:
    """One *measured* fetch against its baseline: the only place drift is ever concluded.

    Every early return here is a refusal to conclude drift, and each one is a different
    reason the comparison in front of us would not mean what a change record claims it
    means. The sibling of :func:`_handle_failure`, which refuses for the one remaining
    reason (there were no bytes at all).

    A fetch with no extractable text never reaches this function: the caller routes it to
    `no_text` before recording anything, because that case must be refused *upstream* of the
    snapshot store rather than downstream of it (issue #19).
    """
    if previous is None:
        report.new.append(source.id)
        return

    if previous.url != source.url:
        # The registry now points this source id at a DIFFERENT page than the one the
        # baseline was taken from — a maintainer corrected a URL, or swapped a landing
        # page for a deep link. Diffing page A against page B is not drift detection;
        # it is two unrelated documents subtracted from each other, and the change
        # record it produces would say "this source changed" when what actually changed
        # is *which page we watch*. That record is unreviewable (its diff is noise) and
        # it is a lie about the world.
        #
        # A first observation of a watch target is a baseline, and a new URL is a new
        # watch target. So: re-baseline, report it loudly, claim no drift.
        report.rebaselined.append((source.id, previous.url, source.url))
        return

    # The baseline may have been recorded under a normalizer this build no longer runs.
    # A hash means nothing except relative to the normalizer that produced it, so restate
    # the baseline under today's contract BEFORE comparing. Unlike a corrected URL, the
    # old evidence here is recoverable — the store retains the bytes — so the answer is
    # to make the comparison valid rather than to refuse it or to caveat it.
    baseline = _comparable_baseline(previous, content_type)

    if baseline is None:
        # The one unrecoverable case. We cannot say the page is unchanged (we have no
        # comparable baseline) and we must not say it changed (we have no comparable
        # baseline). So we say exactly that, re-baseline on today's fetch — which the
        # caller already recorded — and claim nothing about drift.
        report.unrenormalizable.append(
            (
                source.id,
                representation_contract(previous.normalizer_version, previous.extractor_version),
            )
        )
        return

    if baseline.content_sha256 == new_hash:
        if baseline.renormalized_from is None:
            report.unchanged.append(source.id)
        else:
            # A version bump and nothing else. Reported in its own bucket rather than
            # silently folded into `unchanged`, because "your normalizer changed and this
            # page did not" is a fact the operator wants stated once — and because the
            # baseline row now carries a different contract from the one it was compared
            # under, which is provenance, not noise.
            report.renormalized.append((source.id, baseline.renormalized_from, CURRENT_CONTRACT))
        return

    change = ChangeRecord.observed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        # The re-derived hash, not the stored one — `previous_hash` and `diff_excerpt`
        # have to describe the same comparison. normalize.py's invariant is that the hash
        # and the diff can never disagree about what the content was, and recording a hash
        # computed under a contract the diff did not use would break it.
        previous_hash=baseline.content_sha256,
        new_hash=new_hash,
        diff_excerpt=diff_excerpt(
            baseline.normalized_text,
            normalized,
            source_url=source.url,
            renormalized_from=baseline.renormalized_from,
        ),
        observed_at=observed_at,
    )
    store.record_change(change, run_id=run_id)
    report.changed.append(change)


def watch_registry(
    registry: Registry,
    store: SnapshotStore,
    fetcher: Fetcher,
    *,
    as_of: date,
    jurisdiction: str | None = None,
    removal_threshold: int = REMOVAL_THRESHOLD,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> WatchReport:
    """Run the production watcher behind the shared fail-closed source predicate.

    The exact eligible set is frozen in SQLite before any fetch begins.  Ineligible entries
    remain in the receipt with their reasons but cannot enter the attempt denominator.  A
    previous retrieval failure is intentionally absent from the predicate, so an eligible
    source that failed last week is attempted again and remains visible in this week's count.
    """

    selected = (
        registry.for_jurisdiction(jurisdiction) if jurisdiction is not None else registry.sources
    )
    scoped = Registry(version=registry.version, sources=selected)
    eligibility = eligibility_report(scoped, as_of=as_of)
    decision_by_id = {decision.source_id: decision for decision in eligibility.decisions}
    inputs = tuple(
        RunSourceInput(
            source_id=source.id,
            jurisdiction=source.jurisdiction,
            document_class=source.document_class,
            url=source.url,
            authority=source.authority,
            eligible=decision_by_id[source.id].eligible,
            eligibility_reasons=decision_by_id[source.id].reasons,
        )
        for source in selected
    )
    run_id = store.start_watch_run(
        as_of=as_of,
        registry_version=registry.version,
        registry_revision=registry_revision(registry),
        jurisdiction=jurisdiction.upper() if jurisdiction is not None else None,
        sources=inputs,
        started_at=started_at,
    )
    eligible_ids = eligibility.attempt_source_ids
    eligible_set = frozenset(eligible_ids)
    authorized = tuple(source for source in selected if source.id in eligible_set)

    if not authorized:
        store.finish_watch_run(
            run_id,
            state=RUN_FAILED,
            observation_count=0,
            error="no attempt-eligible sources in scope",
            completed_at=completed_at,
        )
        return WatchReport(
            run_id=run_id,
            state=RUN_FAILED,
            eligible_source_ids=(),
            ineligible=eligibility.ineligible,
        )

    try:
        report = _watch_authorized_sources(
            authorized,
            store,
            fetcher,
            removal_threshold=removal_threshold,
            run_id=run_id,
        )
    except Exception as exc:
        # Persist the failed terminal state, then preserve the original exception and
        # traceback.  Catching `Exception` intentionally excludes operator interrupts and
        # process termination; those leave a `running` receipt, which `status.json` presents
        # as non-success rather than inventing a completion.
        store.finish_watch_run(
            run_id,
            state=RUN_FAILED,
            error=f"{type(exc).__name__}: {exc}",
            completed_at=completed_at,
        )
        raise

    observation_count = len(report.changed) + len(report.possibly_removed)
    # `partial` covers both ways a run can fail to produce a comparable observation for every
    # eligible source: a retrieval that failed, and a retrieval that succeeded and yielded no
    # text to compare (issue #19). Neither may finish `quiet`, whose published sentence is
    # "completed for every eligible source and created no observations" — a claim that a run
    # which could not measure a source has not earned. The store enforces the same rule
    # independently: `finish_watch_run` refuses `quiet` or `complete` while any attempted
    # source is recorded as unmeasured, so a future caller cannot re-introduce the silence by
    # computing this line differently.
    unmeasured = bool(report.unreachable or report.no_text)
    state = RUN_PARTIAL if unmeasured else RUN_COMPLETE if observation_count else RUN_QUIET
    store.finish_watch_run(
        run_id,
        state=state,
        observation_count=observation_count,
        completed_at=completed_at,
    )
    report.run_id = run_id
    report.state = state
    report.eligible_source_ids = eligible_ids
    report.ineligible = eligibility.ineligible
    return report


def watch(
    registry: Registry,
    store: SnapshotStore,
    fetcher: Fetcher,
    *,
    jurisdiction: str | None = None,
    removal_threshold: int = REMOVAL_THRESHOLD,
) -> WatchReport:
    """Production watcher API; eligibility is always evaluated on today's UTC date.

    ``watch_registry`` carries explicit clock injection for deterministic tests and historical
    audit tooling.  This production entry point deliberately accepts neither an arbitrary
    iterable of sources nor an operator-selected policy date: backdating must never revive an
    expired verification or fetch-policy approval.
    """

    return watch_registry(
        registry,
        store,
        fetcher,
        as_of=datetime.now(UTC).date(),
        jurisdiction=jurisdiction,
        removal_threshold=removal_threshold,
    )


def _attempt_evidence(result: FetchResult, content: ContentEvidence | None) -> AttemptEvidence:
    """Map one fetch outcome onto the evidence its attempt receipt persists (`DATA-04`).

    The fallbacks (`result.url` for a blank final URL, `len(result.body)` for an unset byte
    count) restate facts already present on the result for injected fetchers that predate
    the evidence fields — they never invent new ones. A byte *limit* in particular is not
    inferable from a body, so it passes through as given and the store's triggers refuse a
    successful attempt that declines to state one.
    """
    if content is not None:
        return AttemptEvidence(
            final_url=result.final_url or result.url,
            redirect_chain=result.redirect_chain,
            raw_sha256=content.raw_sha256,
            normalized_sha256=content.normalized_sha256,
            bytes_received=result.bytes_received or len(result.body),
            byte_limit=result.byte_limit,
            truncated=result.truncated,
            extraction_outcome=content.extraction_outcome,
            error_class="",
        )
    return AttemptEvidence(
        final_url=result.final_url or result.url,
        redirect_chain=result.redirect_chain,
        raw_sha256="",
        normalized_sha256="",
        bytes_received=result.bytes_received,
        byte_limit=result.byte_limit,
        truncated=result.truncated,
        extraction_outcome="",
        error_class=result.error_class,
    )


def _handle_failure(
    source: Source,
    store: SnapshotStore,
    report: WatchReport,
    error: str | None,
    status: int | None,
    removal_threshold: int,
    *,
    run_id: str | None = None,
) -> None:
    """One failed fetch: hold the baseline, count the streak, escalate if it is long enough.

    THE RULE IS UNCHANGED AND UNCONDITIONAL. An outage, a 403, a WAF, a timeout — none of
    these are a policy change. We record no snapshot (a failed fetch has no bytes to
    record), we leave the previous hash standing as the baseline, and we never, at any
    streak length, mint a *content* change out of a failure to fetch.

    What is new is that we no longer respond to a long silence *with* silence. Holding the
    baseline forever is right for an outage and wrong for a page that was taken down, and
    the tool previously could not tell those apart — so it treated every removal as an
    outage, indefinitely, and said nothing. A government page about trans identity
    documents disappearing is itself a signal; failing to surface it is a wrong "no change",
    which is the safety failure this repo is organised around (RESPONSIBLE-TECH-AUDITS §A).

    So: after `removal_threshold` consecutive failures we mint a `possibly_removed` record.
    It is unclassified, unreviewed, unpublishable, and it carries the literal error string
    rather than a guess about what the error means.
    """
    reason = error or "unknown error"
    report.unreachable.append((source.id, reason))

    streak = store.record_failure(source.id, error=reason, status=status)
    if streak < removal_threshold:
        return

    baseline = store.latest_snapshot(source.id)
    if baseline is None:
        # A source that has NEVER been fetched successfully has no baseline to have lost.
        # Escalating it would claim a page "possibly disappeared" when we never once saw it
        # — that is a registry problem (a bad URL, a host that blocks us) and it belongs in
        # `sources check`, not in a change record that says something vanished.
        return

    escalation = ChangeRecord.possibly_removed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        last_known_hash=baseline.content_sha256,
        consecutive_failures=streak,
        last_error=reason,
    )
    store.record_change(escalation, run_id=run_id)
    report.possibly_removed.append(escalation)
