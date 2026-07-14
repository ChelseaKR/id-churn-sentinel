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

STATUS_SCHEMA_VERSION = "1.0"
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
    attempted = store.latest_watch_run()
    successful = store.latest_watch_run(successful_only=True)

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
        "eligible_count": run.eligible_count,
        "attempted_count": run.attempted_count,
        "successful_retrieval_count": run.successful_count,
        "attempt_completeness": run.attempt_completeness,
        "observation_count": run.observation_count,
        # Raw errors remain operational evidence.  Publishing arbitrary network/database
        # strings would create a path for hostile page text or local paths to cross the
        # private/public boundary.
        "has_internal_error": bool(run.error),
    }


def _message(status: PublicRunStatus) -> str:
    messages = {
        "running": "A watch run started but has not recorded a terminal outcome.",
        "quiet": "The latest watch completed for every eligible source and created no observations.",
        "complete": "The latest watch completed and created one or more observations for human review.",
        "partial": "The latest watch attempted its eligible sources, but one or more retrievals failed.",
        "failed": "The latest watch did not complete. Feed silence is not evidence of no change.",
        "stale": "No terminal watch receipt is recent enough. Feed silence is not evidence of no change.",
    }
    return messages[status.state]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
