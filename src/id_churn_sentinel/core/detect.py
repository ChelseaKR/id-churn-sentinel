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
from id_churn_sentinel.core.fetch import Fetcher
from id_churn_sentinel.core.normalize import content_hash, passages
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import (
    RUN_COMPLETE,
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_QUIET,
    RunSourceInput,
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
    is now also an escalation (that is the consequence). `total` counts it once."""

    changed: list[ChangeRecord] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    rebaselined: list[tuple[str, str, str]] = field(default_factory=list)
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
        return (
            f"{self.total} source(s): {len(self.changed)} changed, "
            f"{len(self.unchanged)} unchanged, {len(self.new)} new baseline, "
            f"{len(self.unreachable)} unreachable (not drift){rebaselined}{escalated}"
        )


@dataclass(slots=True)
class StabilityReport:
    """What `check_stability` saw: which sources hash the same twice, and which do not.

    See :func:`check_stability` for why a source that does not is a *defect in the registry*
    rather than a finding about the world."""

    stable: list[str] = field(default_factory=list)
    unstable: list[tuple[str, str, str]] = field(default_factory=list)
    unreachable: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.stable) + len(self.unstable) + len(self.unreachable)

    def summary(self) -> str:
        return (
            f"{self.total} source(s): {len(self.stable)} stable, "
            f"{len(self.unstable)} UNSTABLE (false-drift by construction), "
            f"{len(self.unreachable)} unreachable"
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
        second = fetcher.fetch(source.url)
        if not second.ok:
            report.unreachable.append((source.id, second.error or "unknown error"))
            continue

        first_hash, _ = content_hash(first.body, first.content_type)
        second_hash, _ = content_hash(second.body, second.content_type)
        if first_hash == second_hash:
            report.stable.append(source.id)
        else:
            report.unstable.append((source.id, first_hash, second_hash))
    return report


def diff_excerpt(previous_text: str, current_text: str, *, source_url: str) -> str:
    """A unified diff of the normalized passages, truncated to a reviewable size.

    Binary sources (PDFs) normalize to empty text and cannot be diffed. Rather than emit a
    misleading empty diff, say so plainly — the reviewer needs to know that the honest next
    step is to open both documents themselves.
    """
    if not previous_text and not current_text:
        return (
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
        return (
            "(the content hash changed but the normalized text did not differ — the change "
            f"is in markup or in non-text bytes. Open {source_url} to inspect.)"
        )
    if len(text) > MAX_DIFF_EXCERPT_CHARS:
        text = (
            text[:MAX_DIFF_EXCERPT_CHARS]
            + f"\n… (diff truncated at {MAX_DIFF_EXCERPT_CHARS} chars; "
            + "run `sentinel diff <change-id>` for the full text)"
        )
    return text


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
        if run_id is not None:
            store.finish_fetch_attempt(
                run_id,
                source_id=source.id,
                ok=result.ok,
                http_status=result.status,
                content_type=result.content_type or "",
                error=result.error or "",
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

        new_hash, normalized = content_hash(result.body, result.content_type)
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
        )

        if previous is None:
            report.new.append(source.id)
            continue

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
            continue

        if previous.content_sha256 == new_hash:
            report.unchanged.append(source.id)
            continue

        change = ChangeRecord.observed(
            source_id=source.id,
            jurisdiction=source.jurisdiction,
            document_class=source.document_class,
            url=source.url,
            previous_hash=previous.content_sha256,
            new_hash=new_hash,
            diff_excerpt=diff_excerpt(previous.normalized_text, normalized, source_url=source.url),
            observed_at=result.fetched_at,
        )
        store.record_change(change, run_id=run_id)
        report.changed.append(change)

    return report


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
    state = RUN_PARTIAL if report.unreachable else RUN_COMPLETE if observation_count else RUN_QUIET
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
