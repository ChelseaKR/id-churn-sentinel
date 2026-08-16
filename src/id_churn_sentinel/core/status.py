"""Public watcher health, kept separate from publication time.

Refreshing the site is not evidence that a watch ran.  This module derives health only from
persisted run receipts and exposes the last attempted and last successful run independently,
so a new ``generated_at`` can never turn an old or failed watch green.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from id_churn_sentinel.core.store import RUN_RUNNING, SnapshotStore, WatchRun

__all__ = [
    "DEFAULT_STALE_AFTER",
    "STATUS_SCHEMA_VERSION",
    "PublicRunStatus",
    "build_public_status",
    "no_run_status",
    "status_json",
]

# 1.1 (2026-08-15, issue #19) adds `unmeasured_source_ids`, `unmeasured_count` and
# `observed_source_count` to each run. Additive, and a version bump rather than a silent
# widening: the contract is a *closed* schema (`additionalProperties: false`), so a consumer
# validating against 1.0 must be able to tell that the document it is holding is a different
# one. The fields exist because `successful_retrieval_count` alone let a source that returned
# no readable text be counted among the pages we watched.
STATUS_SCHEMA_VERSION = "1.1"
DEFAULT_STALE_AFTER = timedelta(days=8)


@dataclass(frozen=True, slots=True)
class PublicRunStatus:
    """A point-in-time public view of persisted operational evidence."""

    state: str
    stale: bool
    stale_after: timedelta
    last_attempted: WatchRun | None
    last_successful: WatchRun | None


def no_run_status(*, stale_after: timedelta = DEFAULT_STALE_AFTER) -> PublicRunStatus:
    """The safe publication default when no evidence store was supplied."""

    if stale_after <= timedelta(0):
        raise ValueError("stale_after must be positive")
    return PublicRunStatus(
        state="stale",
        stale=True,
        stale_after=stale_after,
        last_attempted=None,
        last_successful=None,
    )


def build_public_status(
    store: SnapshotStore,
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> PublicRunStatus:
    if stale_after <= timedelta(0):
        raise ValueError("stale_after must be positive")
    current = _as_utc(now or datetime.now(UTC))
    # Aggregate health may only be derived from an aggregate run.  A successful state-only
    # diagnostic remains available in the run ledger but cannot turn the national feed green.
    attempted = store.latest_watch_run(aggregate_only=True)
    successful = store.latest_watch_run(successful_only=True, aggregate_only=True)

    if attempted is None:
        return no_run_status(stale_after=stale_after)

    reference = attempted.completed_at or attempted.started_at
    is_stale = current - reference > stale_after
    state = attempted.state
    # A currently running receipt stays visibly running.  Its separate `stale` flag becomes
    # true if it has exceeded the bound, which distinguishes a live run from a hung one
    # without overwriting the persisted fact that no terminal state was recorded.
    if is_stale and attempted.state != RUN_RUNNING:
        state = "stale"
    return PublicRunStatus(
        state=state,
        stale=is_stale,
        stale_after=stale_after,
        last_attempted=attempted,
        last_successful=successful,
    )


def status_json(status: PublicRunStatus, *, generated_at: datetime) -> str:
    """Serialize the versioned public status contract."""

    payload: dict[str, Any] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "generated_at": _as_utc(generated_at).isoformat(),
        "state": status.state,
        "stale": status.stale,
        "stale_after_seconds": int(status.stale_after.total_seconds()),
        "message": _message(status),
        "last_attempted_run": _run_payload(status.last_attempted),
        "last_successful_run": _run_payload(status.last_successful),
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _run_payload(run: WatchRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "run_id": run.run_id,
        "state": run.state,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "as_of": run.as_of.isoformat(),
        "scope": run.jurisdiction or "all jurisdictions",
        "registry_version": run.registry_version,
        "registry_revision": run.registry_revision,
        "eligible_source_ids": list(run.eligible_source_ids),
        "attempted_source_ids": list(run.attempted_source_ids),
        "successful_source_ids": list(run.successful_source_ids),
        # Retrieved, and unreadable: a text/HTML body that normalized to zero passages, so
        # nothing about this source was observed (issue #19). Published as its own set rather
        # than deducted from the successful one, because a consumer reading
        # `successful_retrieval_count` is entitled to know which of those retrievals produced
        # no observation — and because a source silently missing from a count reads as zero,
        # which here would be a claim that it was watched and nothing changed.
        "unmeasured_source_ids": list(run.unmeasured_source_ids),
        "eligible_count": run.eligible_count,
        "attempted_count": run.attempted_count,
        "successful_retrieval_count": run.successful_count,
        "unmeasured_count": run.unmeasured_count,
        "observed_source_count": run.observed_count,
        "attempt_completeness": run.attempt_completeness,
        "observation_count": run.observation_count,
        # Raw errors remain operational evidence.  Publishing arbitrary network/database
        # strings would create a path for hostile page text or local paths to cross the
        # private/public boundary.
        "has_internal_error": bool(run.error),
    }


def _message(status: PublicRunStatus) -> str:
    """The one sentence a consumer reads. It may never claim more than the receipt holds.

    ``partial`` covers two different shortfalls — a retrieval that failed, and a retrieval
    that succeeded and yielded no text to compare (issue #19) — so its sentence names both
    rather than asserting the first. And a run with unmeasured sources appends the count and
    the consequence, because "we could not read that page" is exactly the fact a reader would
    otherwise fill in with the reassuring default.
    """
    messages = {
        "running": "A watch run started but has not recorded a terminal outcome.",
        "quiet": "The latest watch completed for every eligible source and created no observations.",
        "complete": "The latest watch completed and created one or more observations for human review.",
        "partial": "The latest watch attempted its eligible sources, but one or more produced no comparable observation: a retrieval failed, or a page returned no extractable text.",
        "failed": "The latest watch did not complete. Feed silence is not evidence of no change.",
        "stale": "No terminal watch receipt is recent enough. Feed silence is not evidence of no change.",
    }
    message = messages[status.state]
    unmeasured = status.last_attempted.unmeasured_count if status.last_attempted else 0
    if unmeasured:
        message += (
            f" {unmeasured} source(s) returned no extractable text and were not compared "
            "against a baseline; for those sources this run is not evidence of no change."
        )
    return message


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
