"""Per-jurisdiction watch receipts: what the last covering run did, source by source.

`status.json` answers "did the watcher run, and how much of the registry did it read" for
the whole registry.  A clinic in Texas does not subscribe to the whole registry.  It
subscribes to ``feed-us-tx.xml``, and an empty feed there is compatible with four very
different weeks:

* all four Texas sources were fetched and none of them had changed;
* two were fetched and two never answered;
* Texas was not in the last run's scope at all;
* nothing has ever run.

The RSS specification has no way to say which of those happened, and the README already
warns that "the feed's silence about a jurisdiction means nothing at all".  That warning is
true because the artifact carried nothing that would make silence mean something.  This
module writes the artifact that does: ``status-us-tx.json``, beside the feed, published on
every publish whether or not anything ran.

Two rules run through the whole file.

**A missing row is never an outcome.**  Every published outcome is read from what a run
recorded, and where a run recorded nothing the receipt says so in its own word.  There is
deliberately no branch on which "we have no record of this source" becomes
``observed_unchanged`` -- that substitution is the single defect this repository exists to
remove from a monitoring feed, and it would be at its most damaging exactly here, in the
document a consumer reads to decide whether silence is evidence.

**The hash a run observed is not published, because the store cannot bind one to a run.**
``snapshots`` carries ``content_sha256`` per source with no run column, and ``run_sources``
carries no hash at all, so the only hash recoverable for a given run is the ``new_hash`` on
a change -- available for sources that moved and for no others.  Publishing "the observed
hash" from the latest snapshot would attribute whatever was fetched most recently to this
run, which on any week with two runs is simply a false statement.  The field is absent in
1.0 rather than present and sometimes wrong; binding snapshots to runs is the change that
would earn it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import RunSourceOutcome, SnapshotStore, WatchRun

__all__ = [
    "COVERAGE_COVERED",
    "COVERAGE_NEVER",
    "COVERAGE_NOT_IN_RUN",
    "COVERAGE_STORE_UNAVAILABLE",
    "JURISDICTION_STATUS_SCHEMA_VERSION",
    "OUTCOMES",
    "JurisdictionStatus",
    "build_jurisdiction_status",
    "jurisdiction_status_json",
    "no_run_jurisdiction_status",
    "statement_for",
    "store_unavailable_jurisdiction_status",
]

#: 1.0 — the first published shape (issue #76).
JURISDICTION_STATUS_SCHEMA_VERSION = "1.0"

#: The last run in the store covered this jurisdiction.
COVERAGE_COVERED = "covered"
#: A run has covered this jurisdiction before, but the most recent run did not.
COVERAGE_NOT_IN_RUN = "not_in_run"
#: No run has ever covered this jurisdiction — including "no run has ever happened".
COVERAGE_NEVER = "never_covered"
#: The publisher had no evidence store to read, so this document knows nothing about what was
#: watched. Distinct from `never_covered`, which is a claim ABOUT the store's contents and can
#: only be made by something that read it. Publishing `never_covered` here would say "no run
#: has ever covered you" on the strength of not having looked -- the substitution this whole
#: file exists to refuse, made by the file itself.
COVERAGE_STORE_UNAVAILABLE = "store_unavailable"

#: Every attempt outcome this receipt can publish, and the sentence each one means. The
#: mapping is the contract: a consumer switching on the word gets the meaning, and a word
#: with no sentence cannot be emitted (:func:`_outcome_statement` refuses).
#:
#: `not_in_run` and `not_attempted` are separate on purpose. "The run never saw this source"
#: (added to the registry after the run) and "the run saw it and did not fetch it" lead a
#: reader to different actions, and merging them would put a registry-growth fact and an
#: operational fact under one word.
OUTCOMES: dict[str, str] = {
    "observed_unchanged": (
        "The last covering run read this page and it matched the committed baseline."
    ),
    "observed_changed": (
        "The last covering run read this page and it differed from the committed baseline; "
        "the difference is in the review queue, and appears in a feed only once a human has "
        "confirmed it."
    ),
    "unreachable": (
        "The last covering run tried to fetch this page and did not get one. Nothing about "
        "this source was compared, so this run is not evidence that it did not change."
    ),
    "unreadable": (
        "The last covering run fetched this page and it yielded no extractable text, so "
        "there was nothing to compare against the baseline. Not evidence of no change."
    ),
    "not_eligible": (
        "The last covering run considered this source and did not attempt it — see the "
        "recorded eligibility reasons."
    ),
    "not_attempted": (
        "The last covering run did not attempt this source. Where there is no run at all, "
        "every source reads this way."
    ),
    "in_flight": (
        "The last covering run has not recorded an outcome for this source. It is running, "
        "or it ended without answering for this one."
    ),
    "outcome_unknown": (
        "The run recorded a retrieval for this source but not what was observed. Receipts "
        "written before observation outcomes were persisted read this way."
    ),
    "not_in_run": (
        "This source was not in the last covering run at all — it is newer than that run, "
        "or it was outside its scope."
    ),
}

#: The outcome words that mean the page was RETRIEVED AND READ. Deliberately not "the words
#: that mean it was compared against a baseline": three of `detect.py`'s non-drift buckets
#: (a first sighting, a re-pointed registry URL, an unrenormalizable committed hash) also
#: persist as `observed_unchanged` today, and none of them was held against anything
#: (issue #99). `_statement` publishes the size of this set as a reading count and says so.
#:
#: One constant, read by `_statement` and by the drift gate's re-derivation of the committed
#: receipts' own sentence. A second copy in the test would let the two drift with nothing to
#: notice, which is a shape this portfolio has already measured going green over a widened
#: gate.
_READING_OUTCOMES: frozenset[str] = frozenset({"observed_unchanged", "observed_changed"})

_NO_RUN_STATEMENT = (
    "No watch run has ever covered this jurisdiction, so nothing here has been compared "
    "against a baseline. An empty feed for this jurisdiction is not evidence of no change."
)

_NO_STORE_STATEMENT = (
    "This receipt was written without access to the evidence store, so it does not say "
    "whether anything was watched. It is not a report that nothing ran: it is the absence "
    "of a report. An empty feed for this jurisdiction is not evidence of no change."
)


@dataclass(frozen=True, slots=True)
class JurisdictionStatus:
    """One jurisdiction's receipt for the last run that covered it."""

    jurisdiction: str
    coverage: str
    #: The last run that covered this jurisdiction, or `None`. `None` is why every source
    #: reads `not_attempted`: there is no run whose judgement could be reported.
    run: WatchRun | None
    #: The newest run in the store, whatever its scope. Present so a `not_in_run` receipt
    #: can name the run that skipped this jurisdiction rather than only the older one that
    #: did not.
    latest_run: WatchRun | None
    #: `(source, outcome word)` in registry order, one entry per source in the jurisdiction.
    sources: tuple[tuple[Source, str], ...]
    #: The run's own eligibility judgement per source id, for the sources it held.
    recorded: dict[str, RunSourceOutcome]


def no_run_jurisdiction_status(jurisdiction: str, registry: Registry) -> JurisdictionStatus:
    """The receipt for a jurisdiction the store holds no covering run for.

    A claim about the store's contents, so only something that has read the store may make
    it. Use :func:`store_unavailable_jurisdiction_status` where there was no store to read.
    """
    return _blank(jurisdiction, registry, COVERAGE_NEVER)


def store_unavailable_jurisdiction_status(
    jurisdiction: str, registry: Registry
) -> JurisdictionStatus:
    """The receipt for a publish that had no evidence store at all.

    Written rather than omitted, for the same reason every other file here is written for
    every jurisdiction: a consumer who fetches ``status-us-tx.json`` and gets a 404 learns
    nothing and falls back to reading the empty feed, which is the reading this document
    exists to prevent.

    What it must not do is say ``never_covered``. That is a statement about what the store
    holds, and a publisher with no store has not looked. Rendering "no run has ever covered
    this jurisdiction" out of "I could not check" would be this repository's own defect,
    committed by the file written to remove it.
    """
    return _blank(jurisdiction, registry, COVERAGE_STORE_UNAVAILABLE)


def _blank(jurisdiction: str, registry: Registry, coverage: str) -> JurisdictionStatus:
    """A receipt with no run behind it. Every source reads `not_attempted`, which is what
    "there is no run whose judgement could be reported" means at source level."""
    return JurisdictionStatus(
        jurisdiction=jurisdiction,
        coverage=coverage,
        run=None,
        latest_run=None,
        sources=tuple((source, "not_attempted") for source in _sources_in(registry, jurisdiction)),
        recorded={},
    )


def build_jurisdiction_status(
    store: SnapshotStore,
    jurisdiction: str,
    *,
    registry: Registry,
) -> JurisdictionStatus:
    """Derive one jurisdiction's receipt from persisted run evidence.

    The run consulted is the newest whose *scope* included this jurisdiction, which is what
    the run declared about itself. It is not "the newest run that has a row for one of these
    sources": a jurisdiction whose every source was ineligible would then look like one no
    run had ever covered, when in fact a run looked and had nothing eligible to fetch.
    """
    sources = _sources_in(registry, jurisdiction)
    latest = store.latest_watch_run()
    covering = store.latest_watch_run_covering(jurisdiction)
    if covering is None:
        blank = no_run_jurisdiction_status(jurisdiction, registry)
        # `latest` is still reported when one exists: "no run has ever covered you, and here
        # is the run that just happened elsewhere" is more use than either half alone.
        return JurisdictionStatus(
            jurisdiction=blank.jurisdiction,
            coverage=blank.coverage,
            run=None,
            latest_run=latest,
            sources=blank.sources,
            recorded={},
        )

    recorded = {row.source_id: row for row in store.run_source_outcomes(covering.run_id)}
    observed_ids = store.run_observation_source_ids(covering.run_id)
    coverage = (
        COVERAGE_COVERED
        if latest is not None and latest.run_id == covering.run_id
        else COVERAGE_NOT_IN_RUN
    )
    return JurisdictionStatus(
        jurisdiction=jurisdiction,
        coverage=coverage,
        run=covering,
        latest_run=latest,
        sources=tuple(
            (source, _outcome_for(recorded.get(source.id), source.id in observed_ids))
            for source in sources
        ),
        recorded=recorded,
    )


def _sources_in(registry: Registry, jurisdiction: str) -> tuple[Source, ...]:
    return tuple(
        source
        for source in sorted(registry.sources, key=lambda s: s.id)
        if source.jurisdiction == jurisdiction
    )


def _outcome_for(row: RunSourceOutcome | None, produced_observation: bool) -> str:
    """One source's outcome, in the order the facts actually constrain each other.

    Read top to bottom: each branch is only reached when the one above it did not apply, so
    a source that was never fetched can never fall through to an observation word. The
    ordering is the guard. In particular a failed retrieval is answered *before* anything
    about observations, so a source with no committed baseline that failed to fetch reads
    ``unreachable`` and cannot read ``observed_unchanged``.
    """
    if row is None:
        return "not_in_run"
    if not row.eligible:
        return "not_eligible"
    if not row.attempted:
        return "not_attempted"
    if row.retrieval_success is None:
        return "in_flight"
    if not row.retrieval_success:
        return "unreachable"
    if row.observation_outcome == "no-text":
        return "unreadable"
    if row.observation_outcome not in {"measured", "not-retrieved"}:
        # '' (a row mid-flight) and 'legacy-unknown' (a pre-migration row) both mean the run
        # did not record what was observed. Neither is "nothing changed".
        return "outcome_unknown"
    return "observed_changed" if produced_observation else "observed_unchanged"


def _outcome_statement(outcome: str) -> str:
    statement = OUTCOMES.get(outcome)
    if statement is None:  # pragma: no cover - unreachable while _outcome_for is the only caller
        raise ValueError(f"no published sentence for outcome {outcome!r}")
    return statement


def _run_block(run: WatchRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "run_id": run.run_id,
        "state": run.state,
        "scope": run.jurisdiction or "all jurisdictions",
        "as_of": run.as_of.isoformat(),
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _source_block(
    source: Source, outcome: str, recorded: RunSourceOutcome | None
) -> dict[str, Any]:
    """One source row, carrying its verification status because everything here does.

    The status travels with the source in every artifact this project publishes, and a
    receipt is not an exception: a document saying "we read this page and it had not
    changed" is exactly where a reader is most likely to take the URL as authoritative,
    and nobody has confirmed that it is.
    """
    return {
        "source_id": source.id,
        "jurisdiction": source.jurisdiction,
        "document_class": source.document_class,
        "url": source.url,
        "authority": source.authority,
        "verification_status": source.verification_status,
        "human_verified": source.verified,
        "verified_by": source.verification.verifier,
        "verified_at": source.verification.at,
        "verification_statement": source.verification.public_statement,
        "eligible_in_run": None if recorded is None else recorded.eligible,
        "eligibility_reasons": [] if recorded is None else list(recorded.eligibility_reasons),
        "outcome": outcome,
        "outcome_statement": _outcome_statement(outcome),
    }


def _counts(outcomes: Sequence[str]) -> dict[str, int]:
    """Every outcome word, always, including the ones that did not occur.

    A count dictionary that omits its zeroes makes a reader reconstruct the missing keys,
    and the reconstruction they reach for is that the word did not apply — which is right
    for ``unreachable`` and wrong for nothing else. Publishing all nine keeps the reader
    from guessing which absences were deliberate.
    """
    tally = dict.fromkeys(OUTCOMES, 0)
    for outcome in outcomes:
        tally[outcome] += 1
    return tally


def statement_for(
    *,
    jurisdiction: str,
    coverage: str,
    run_id: str | None,
    run_state: str | None,
    outcomes: Sequence[str],
) -> str:
    """The sentence a person reads, from the fields the receipt itself publishes.

    Deliberately takes primitives rather than a :class:`JurisdictionStatus`. Every input is
    a field of the published document (``jurisdiction``, ``coverage``, ``run.run_id``,
    ``run.state``, and each source's ``outcome``), so this sentence can be **re-derived from
    a committed receipt** and byte-compared against the ``statement`` beside them. That is
    what buys back the drift gate's exclusion of these files: they cannot be regenerated
    from committed inputs, but their prose can be held to their own data.

    One implementation, two callers -- the publisher and the gate. A gate that formatted the
    sentence itself would be a second copy, and a second copy of the thing under test can
    drift from it with nothing to notice.
    """
    if coverage == COVERAGE_STORE_UNAVAILABLE:
        return _NO_STORE_STATEMENT
    if run_id is None:
        return _NO_RUN_STATEMENT
    # READ, not COMPARED. The variable was always named `read` and the sentence used to say
    # "compared ... against the committed baseline", which claims more than the two words
    # summed here can support: `observed_unchanged` is emitted for a first sighting, for a
    # source the registry has re-pointed at a different URL, and for one whose committed
    # hash is not re-derivable under today's normalization contract, and none of those was
    # held against a baseline (issue #99). Narrowing the verb makes the sentence true under
    # both today's vocabulary and whatever #99 settles on, and it never claims more than the
    # receipt holds -- which is this function's own stated rule.
    read = sum(1 for word in outcomes if word in _READING_OUTCOMES)
    total = len(outcomes)
    scope = (
        f"The last run covering {jurisdiction} "
        if coverage == COVERAGE_COVERED
        else f"{jurisdiction} was not in the most recent run. The last run that did cover it "
    )
    return (
        f"{scope}({run_id}, {run_state}) read {read} of {total} "
        f"registered source(s) in this jurisdiction. For the remaining {total - read}, "
        f"this run is not evidence that nothing changed."
    )


def _statement(status: JurisdictionStatus) -> str:
    """The sentence a person reads. It may never claim more than the receipt holds."""
    return statement_for(
        jurisdiction=status.jurisdiction,
        coverage=status.coverage,
        run_id=None if status.run is None else status.run.run_id,
        run_state=None if status.run is None else status.run.state,
        outcomes=[outcome for _, outcome in status.sources],
    )


def jurisdiction_status_json(status: JurisdictionStatus, *, generated_at: datetime) -> str:
    """Serialize one jurisdiction's receipt."""
    blocks = [
        _source_block(source, outcome, status.recorded.get(source.id))
        for source, outcome in status.sources
    ]
    payload: dict[str, Any] = {
        "schema_version": JURISDICTION_STATUS_SCHEMA_VERSION,
        "generated_at": _as_utc(generated_at).isoformat(),
        "jurisdiction": status.jurisdiction,
        "coverage": status.coverage,
        "statement": _statement(status),
        # Every outcome word with the sentence it stands for, in the document itself. A
        # consumer that meets a word it does not know can read what it means without
        # fetching a schema, and cannot be tempted to treat an unfamiliar word as benign.
        "outcome_vocabulary": dict(OUTCOMES),
        "run": _run_block(status.run),
        "latest_run": _run_block(status.latest_run),
        "counts": _counts([outcome for _, outcome in status.sources]),
        "sources": blocks,
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
