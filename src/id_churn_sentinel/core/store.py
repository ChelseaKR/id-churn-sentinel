"""The snapshot + change store — SQLite, and the second, independent home of the
human-in-the-loop invariant.

Two tables:

* **`snapshots`** — every fetch we kept: the raw bytes, the normalized text, the sha256,
  when we fetched it, and the HTTP status. We keep the last N (default 5) per source, and
  we keep the *bytes*, not just the hash. That is the difference between a tool that says
  "it changed" and one that can still show you *what* changed six months later, after the
  state has quietly re-edited the page twice more. A diff you cannot reproduce is a claim,
  not evidence.
* **`changes`** — the reviewable records.

The `changes` schema carries a `CHECK` constraint that restates, in SQL, the rule
`changes.py` enforces in Python:

    CHECK (significance = 'unclassified' OR (reviewer IS NOT NULL AND reviewer <> ''))

A classified change without a named human reviewer is **rejected by the database**. The
Python types make it unrepresentable; the schema makes it un-*storable*. Two independent
enforcement points, because the failure mode this guards against — a machine asserting
that the law substantively changed — is one where being right 99% of the time is not good
enough. (The same doubling is why `self-osint-monitor`'s consent gate lives in both the
`Subject` type and a `NOT NULL REFERENCES` clause.)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from id_churn_sentinel.core.changes import (
    ChangeKind,
    ChangeRecord,
    IndependentReviewStatus,
    PublicationStatus,
    ReviewStatus,
    Significance,
    actor_identity,
    canonical_actor,
    governance_reference_is_safe,
    observation_fields_are_valid,
    private_text_is_safe,
    public_copy_is_safe,
)
from id_churn_sentinel.errors import StoreError

__all__ = [
    "DEFAULT_SNAPSHOT_RETENTION",
    "RUN_COMPLETE",
    "RUN_FAILED",
    "RUN_PARTIAL",
    "RUN_QUIET",
    "RUN_RUNNING",
    "RunSourceInput",
    "Snapshot",
    "SnapshotStore",
    "WatchRun",
]

DEFAULT_SNAPSHOT_RETENTION = 5

RUN_RUNNING = "running"
RUN_QUIET = "quiet"
RUN_COMPLETE = "complete"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"
_TERMINAL_RUN_STATES = frozenset({RUN_QUIET, RUN_COMPLETE, RUN_PARTIAL, RUN_FAILED})
_SUCCESSFUL_RUN_STATES = frozenset({RUN_QUIET, RUN_COMPLETE})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    fetched_at      TEXT    NOT NULL,
    http_status     INTEGER,
    content_sha256  TEXT    NOT NULL,
    raw_bytes       BLOB    NOT NULL,
    normalized_text TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_source
    ON snapshots (source_id, snapshot_id DESC);

CREATE TABLE IF NOT EXISTS changes (
    change_id      TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL,
    jurisdiction   TEXT NOT NULL,
    document_class TEXT NOT NULL,
    url            TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    previous_hash  TEXT NOT NULL,
    new_hash       TEXT NOT NULL,
    diff_excerpt   TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'content_drift'
        CHECK (kind IN ('content_drift', 'possibly_removed')),
    significance   TEXT NOT NULL
        CHECK (significance IN ('unclassified', 'editorial', 'substantive')),
    review_status  TEXT NOT NULL
        CHECK (review_status IN ('unreviewed', 'confirmed', 'dismissed')),
    reviewer       TEXT,
    reviewed_at    TEXT,
    review_note    TEXT NOT NULL DEFAULT '',

    -- The human-in-the-loop gate, in the schema. A significance other than
    -- 'unclassified' REQUIRES a named reviewer. No code path, and no stray SQL, can
    -- store a machine-asserted legal classification.
    CHECK (significance = 'unclassified' OR (reviewer IS NOT NULL AND reviewer <> '')),

    -- And a confirmed record must be classified: 'confirmed but unclassified' would sail
    -- through the publisher's status filter carrying no human judgment at all.
    CHECK (review_status <> 'confirmed' OR significance <> 'unclassified')
);

CREATE INDEX IF NOT EXISTS idx_changes_review
    ON changes (review_status, observed_at DESC);

-- Per-source fetch health. This is the table that lets the tool tell "the server hiccuped"
-- apart from "the page is gone" — a distinction it previously could not make at all, and
-- whose absence meant a removed page held its stale baseline forever in total silence.
--
-- `consecutive_failures` is the whole mechanism: incremented on every failed fetch, reset
-- to zero on every successful one. It is deliberately NOT a count of total failures — a
-- source that fails one week in eight is a flaky server, and a source that has failed
-- eight weeks running is something else. Only the *streak* distinguishes them.
CREATE TABLE IF NOT EXISTS source_health (
    source_id            TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
        CHECK (consecutive_failures >= 0),
    last_status          INTEGER,
    last_error           TEXT,
    last_failure_at      TEXT,
    last_success_at      TEXT
);
"""

# The alpha store pre-dated schema versioning.  Keep its tables in `_SCHEMA`, then apply
# every V1 addition through this ledger.  A recorded checksum is verified on every open so
# changing an already-applied migration is a loud integrity failure, not an accidental second
# meaning for the same schema version.
_MIGRATIONS = (
    (
        1,
        "v1-watch-runs-and-attempts",
        """
CREATE TABLE IF NOT EXISTS watch_runs (
    run_id              TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    completed_at        TEXT,
    as_of               TEXT NOT NULL,
    registry_version    TEXT NOT NULL,
    registry_revision   TEXT NOT NULL,
    jurisdiction        TEXT,
    state               TEXT NOT NULL
        CHECK (state IN ('running', 'quiet', 'complete', 'partial', 'failed')),
    eligible_count      INTEGER NOT NULL CHECK (eligible_count >= 0),
    attempted_count     INTEGER NOT NULL DEFAULT 0 CHECK (attempted_count >= 0),
    successful_count    INTEGER NOT NULL DEFAULT 0 CHECK (successful_count >= 0),
    observation_count   INTEGER NOT NULL DEFAULT 0 CHECK (observation_count >= 0),
    error               TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_watch_runs_started ON watch_runs (started_at DESC, run_id);
CREATE INDEX IF NOT EXISTS idx_watch_runs_state ON watch_runs (state, completed_at DESC);

CREATE TABLE IF NOT EXISTS run_sources (
    run_id               TEXT NOT NULL REFERENCES watch_runs(run_id) ON DELETE RESTRICT,
    source_id            TEXT NOT NULL,
    jurisdiction         TEXT NOT NULL,
    document_class       TEXT NOT NULL,
    url                  TEXT NOT NULL,
    authority            TEXT NOT NULL,
    eligible             INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    eligibility_reasons  TEXT NOT NULL,
    attempted            INTEGER NOT NULL DEFAULT 0 CHECK (attempted IN (0, 1)),
    retrieval_success    INTEGER CHECK (retrieval_success IN (0, 1)),
    outcome              TEXT NOT NULL DEFAULT '',
    error                TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS fetch_attempts (
    run_id          TEXT NOT NULL REFERENCES watch_runs(run_id) ON DELETE RESTRICT,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    attempted_at    TEXT NOT NULL,
    completed_at    TEXT,
    ok              INTEGER CHECK (ok IN (0, 1)),
    http_status     INTEGER,
    content_type    TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, source_id),
    FOREIGN KEY (run_id, source_id) REFERENCES run_sources(run_id, source_id)
        ON DELETE RESTRICT
);
""",
    ),
    (
        2,
        "v1-bind-observations-to-watch-runs",
        """
CREATE TABLE IF NOT EXISTS run_observations (
    run_id       TEXT NOT NULL REFERENCES watch_runs(run_id) ON DELETE RESTRICT,
    change_id    TEXT NOT NULL REFERENCES changes(change_id) ON DELETE RESTRICT,
    observed_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, change_id)
);

CREATE INDEX IF NOT EXISTS idx_run_observations_change
    ON run_observations (change_id, run_id);
""",
    ),
    (
        3,
        "v1-append-only-review-and-correction-events",
        """
CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id                    TEXT PRIMARY KEY,
    change_id                      TEXT NOT NULL REFERENCES changes(change_id) ON DELETE RESTRICT,
    stage                          TEXT NOT NULL CHECK (stage IN ('first', 'independent')),
    decision                       TEXT NOT NULL
        CHECK (decision IN ('confirmed', 'dismissed', 'returned')),
    significance                   TEXT NOT NULL
        CHECK (significance IN ('unclassified', 'editorial', 'substantive')),
    actor                          TEXT NOT NULL
        CHECK (actor = canonical_actor(actor) AND actor <> ''),
    decided_at                     TEXT NOT NULL CHECK (julianday(decided_at) IS NOT NULL),
    internal_rationale             TEXT NOT NULL DEFAULT ''
        CHECK (private_text_is_safe(internal_rationale)),
    public_copy                    TEXT NOT NULL DEFAULT '',
    qualification_ref              TEXT NOT NULL DEFAULT '',
    conflict_attestation_ref       TEXT NOT NULL DEFAULT '',
    UNIQUE (change_id, stage),
    CHECK (
        (stage = 'first'
         AND decision IN ('confirmed', 'dismissed')
         AND (decision <> 'confirmed' OR significance <> 'unclassified')
         AND ((decision = 'confirmed' AND public_copy_is_safe(public_copy))
              OR (decision = 'dismissed' AND public_copy = ''))
         AND qualification_ref = ''
         AND conflict_attestation_ref = '')
        OR
        (stage = 'independent'
         AND decision IN ('confirmed', 'returned')
         AND significance = 'substantive'
         AND public_copy = ''
         AND governance_reference_is_safe(qualification_ref)
         AND governance_reference_is_safe(conflict_attestation_ref)
         AND (decision <> 'returned' OR length(trim(internal_rationale)) > 0))
    )
);

CREATE INDEX IF NOT EXISTS idx_review_decisions_change
    ON review_decisions (change_id, stage);

CREATE TRIGGER IF NOT EXISTS trg_changes_observation_valid_on_insert
BEFORE INSERT ON changes
BEGIN
    SELECT CASE WHEN NOT observation_fields_are_valid(
        NEW.change_id, NEW.source_id, NEW.observed_at, NEW.previous_hash,
        NEW.new_hash, NEW.diff_excerpt, NEW.kind
    ) THEN RAISE(ABORT, 'change observation fields are invalid') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_changes_observation_no_update
BEFORE UPDATE OF change_id, source_id, jurisdiction, document_class, url, observed_at,
                 previous_hash, new_hash, diff_excerpt, kind ON changes
BEGIN
    SELECT RAISE(ABORT, 'change observations are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_changes_no_delete
BEFORE DELETE ON changes
BEGIN
    SELECT RAISE(ABORT, 'change observations are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_review_decisions_no_update
BEFORE UPDATE ON review_decisions
BEGIN
    SELECT RAISE(ABORT, 'review decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_review_decisions_no_delete
BEFORE DELETE ON review_decisions
BEGIN
    SELECT RAISE(ABORT, 'review decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_first_review_follows_observation
BEFORE INSERT ON review_decisions
WHEN NEW.stage = 'first'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM changes AS observation
        WHERE observation.change_id = NEW.change_id
          AND julianday(NEW.decided_at) >= julianday(observation.observed_at)
    ) THEN RAISE(ABORT, 'first review cannot precede observation') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_independent_review_requires_first
BEFORE INSERT ON review_decisions
WHEN NEW.stage = 'independent'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM review_decisions AS first
        WHERE first.change_id = NEW.change_id
          AND first.stage = 'first'
          AND first.decision = 'confirmed'
          AND first.significance = 'substantive'
    ) THEN RAISE(ABORT, 'independent review requires first-confirmed substantive review') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM review_decisions AS first
        WHERE first.change_id = NEW.change_id
          AND first.stage = 'first'
          AND actor_identity(first.actor) = actor_identity(NEW.actor)
    ) THEN RAISE(ABORT, 'independent reviewer must differ from first reviewer') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM review_decisions AS first
        WHERE first.change_id = NEW.change_id
          AND first.stage = 'first'
          AND julianday(NEW.decided_at) < julianday(first.decided_at)
    ) THEN RAISE(ABORT, 'independent review cannot precede first review') END;
END;

CREATE TABLE IF NOT EXISTS change_lifecycle_events (
    event_id                 TEXT PRIMARY KEY,
    change_id                TEXT NOT NULL UNIQUE
        REFERENCES changes(change_id) ON DELETE RESTRICT,
    action                   TEXT NOT NULL CHECK (action IN ('corrected', 'withdrawn')),
    superseded_by_change_id  TEXT REFERENCES changes(change_id) ON DELETE RESTRICT,
    reason                   TEXT NOT NULL CHECK (reason IN (
        'source_evidence_error', 'review_error', 'duplicate_record',
        'privacy_or_safety', 'superseded_observation', 'other_governed_reason'
    )),
    actor                    TEXT NOT NULL
        CHECK (actor = canonical_actor(actor) AND actor <> ''),
    decided_at               TEXT NOT NULL CHECK (julianday(decided_at) IS NOT NULL),
    CHECK (
        (action = 'corrected' AND superseded_by_change_id IS NOT NULL
         AND superseded_by_change_id <> change_id)
        OR (action = 'withdrawn' AND superseded_by_change_id IS NULL)
    )
);

CREATE TRIGGER IF NOT EXISTS trg_change_lifecycle_no_update
BEFORE UPDATE ON change_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'change lifecycle events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_change_lifecycle_no_delete
BEFORE DELETE ON change_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'change lifecycle events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_lifecycle_requires_publishable_subject
BEFORE INSERT ON change_lifecycle_events
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM review_decisions AS first
        WHERE first.change_id = NEW.change_id
          AND first.stage = 'first'
          AND first.decision = 'confirmed'
          AND first.significance <> 'unclassified'
          AND length(trim(first.public_copy)) > 0
          AND (
              first.significance <> 'substantive'
              OR EXISTS (
                  SELECT 1 FROM review_decisions AS second
                  WHERE second.change_id = NEW.change_id
                    AND second.stage = 'independent'
                    AND second.decision = 'confirmed'
                    AND actor_identity(second.actor) <> actor_identity(first.actor)
              )
          )
    ) THEN RAISE(ABORT, 'lifecycle event requires publishable reviewed subject') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM review_decisions AS decision
        WHERE decision.change_id = NEW.change_id
          AND julianday(NEW.decided_at) < julianday(decision.decided_at)
    ) THEN RAISE(ABORT, 'lifecycle event cannot precede review decisions') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_correction_requires_publishable_replacement
BEFORE INSERT ON change_lifecycle_events
WHEN NEW.action = 'corrected'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM changes AS subject
        JOIN changes AS replacement
          ON replacement.change_id = NEW.superseded_by_change_id
        WHERE subject.change_id = NEW.change_id
          AND replacement.source_id = subject.source_id
          AND replacement.jurisdiction = subject.jurisdiction
          AND replacement.document_class = subject.document_class
          AND replacement.url = subject.url
    ) THEN RAISE(ABORT, 'correction replacement source identity differs') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM review_decisions AS first
        WHERE first.change_id = NEW.superseded_by_change_id
          AND first.stage = 'first'
          AND first.decision = 'confirmed'
          AND first.significance <> 'unclassified'
          AND length(trim(first.public_copy)) > 0
          AND (
              first.significance <> 'substantive'
              OR EXISTS (
                  SELECT 1 FROM review_decisions AS second
                  WHERE second.change_id = NEW.superseded_by_change_id
                    AND second.stage = 'independent'
                    AND second.decision = 'confirmed'
                    AND actor_identity(second.actor) <> actor_identity(first.actor)
              )
          )
    ) THEN RAISE(ABORT, 'correction replacement must be publishable and reviewed') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM review_decisions AS decision
        WHERE decision.change_id = NEW.superseded_by_change_id
          AND julianday(NEW.decided_at) < julianday(decision.decided_at)
    ) THEN RAISE(ABORT, 'correction cannot precede replacement review') END;
    SELECT CASE WHEN EXISTS (
        WITH RECURSIVE successors(change_id) AS (
            SELECT NEW.superseded_by_change_id
            UNION ALL
            SELECT event.superseded_by_change_id
            FROM change_lifecycle_events AS event
            JOIN successors ON event.change_id = successors.change_id
            WHERE event.action = 'corrected'
              AND event.superseded_by_change_id IS NOT NULL
        )
        SELECT 1 FROM successors WHERE change_id = NEW.change_id
    ) THEN RAISE(ABORT, 'correction supersession cycle') END;
END;
""",
    ),
)

_V1_REQUIRED_COLUMNS = {
    "watch_runs": frozenset(
        {
            "run_id",
            "started_at",
            "completed_at",
            "as_of",
            "registry_version",
            "registry_revision",
            "jurisdiction",
            "state",
            "eligible_count",
            "attempted_count",
            "successful_count",
            "observation_count",
            "error",
        }
    ),
    "run_sources": frozenset(
        {
            "run_id",
            "source_id",
            "jurisdiction",
            "document_class",
            "url",
            "authority",
            "eligible",
            "eligibility_reasons",
            "attempted",
            "retrieval_success",
            "outcome",
            "error",
        }
    ),
    "fetch_attempts": frozenset(
        {
            "run_id",
            "source_id",
            "url",
            "attempted_at",
            "completed_at",
            "ok",
            "http_status",
            "content_type",
            "error",
        }
    ),
    "run_observations": frozenset({"run_id", "change_id", "observed_at"}),
    "review_decisions": frozenset(
        {
            "decision_id",
            "change_id",
            "stage",
            "decision",
            "significance",
            "actor",
            "decided_at",
            "internal_rationale",
            "public_copy",
            "qualification_ref",
            "conflict_attestation_ref",
        }
    ),
    "change_lifecycle_events": frozenset(
        {
            "event_id",
            "change_id",
            "action",
            "superseded_by_change_id",
            "reason",
            "actor",
            "decided_at",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class RunSourceInput:
    """The immutable source identity and eligibility decision captured for one run."""

    source_id: str
    jurisdiction: str
    document_class: str
    url: str
    authority: str
    eligible: bool
    eligibility_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WatchRun:
    """A persisted watcher receipt with exact numerator and denominator source sets."""

    run_id: str
    started_at: datetime
    completed_at: datetime | None
    as_of: date
    registry_version: str
    registry_revision: str
    jurisdiction: str | None
    state: str
    eligible_source_ids: tuple[str, ...]
    attempted_source_ids: tuple[str, ...]
    successful_source_ids: tuple[str, ...]
    observation_count: int
    error: str

    @property
    def eligible_count(self) -> int:
        return len(self.eligible_source_ids)

    @property
    def attempted_count(self) -> int:
        return len(self.attempted_source_ids)

    @property
    def successful_count(self) -> int:
        return len(self.successful_source_ids)

    @property
    def attempt_completeness(self) -> float | None:
        if not self.eligible_source_ids:
            return None
        return len(self.attempted_source_ids) / len(self.eligible_source_ids)


def _execute_sql_script(conn: sqlite3.Connection, sql: str) -> None:
    """Execute complete SQL statements without ``executescript``'s implicit commit.

    Python's ``sqlite3.executescript`` commits before running its input, which makes it
    impossible to atomically bind a multi-statement migration to its ledger row.  Accumulating
    with SQLite's own completeness parser preserves trigger bodies and lets the caller own the
    surrounding transaction.
    """

    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                conn.execute(statement)
            pending = ""
    if pending.strip():
        raise sqlite3.OperationalError("migration contains an incomplete SQL statement")


def _initialize_base_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
        _execute_sql_script(conn, _SCHEMA)
        conn.commit()
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        raise StoreError(f"base schema initialization failed: {exc}") from exc


def _read_known_migration_ledger(
    conn: sqlite3.Connection,
) -> dict[int, tuple[str, str]]:
    applied = {
        int(row["version"]): (str(row["name"]), str(row["checksum"]))
        for row in conn.execute("SELECT version, name, checksum FROM schema_migrations")
    }
    known_versions = {version for version, _, _ in _MIGRATIONS}
    unknown = sorted(set(applied) - known_versions)
    if unknown:
        raise StoreError(
            "store contains migration version(s) this build does not know: "
            + ", ".join(str(version) for version in unknown)
        )
    return applied


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for stores created by an earlier version.

    `CREATE TABLE IF NOT EXISTS` is a no-op against an existing table, so a column added to
    `_SCHEMA` never reaches a database that already exists — the snapshot store is meant to
    be long-lived (that is the entire point of retaining the bytes), so "just delete the db"
    is not an acceptable upgrade path. `kind` is added with a DEFAULT, which back-fills every
    pre-existing row as `content_drift`: correct, because every record written before this
    column existed was, by construction, a content-drift record.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(changes)")}
        if columns and "kind" not in columns:
            conn.execute(
                "ALTER TABLE changes ADD COLUMN kind TEXT NOT NULL DEFAULT 'content_drift'"
            )

        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " checksum TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        conn.commit()
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        raise StoreError(f"migration ledger initialization failed: {exc}") from exc

    current_migration: tuple[int, str] | None = None
    try:
        # Read the ledger only after taking the writer lock. Otherwise two first-open
        # processes can both observe an empty ledger and race on the same version row.
        conn.execute("BEGIN IMMEDIATE")
        applied = _read_known_migration_ledger(conn)

        for version, name, sql in _MIGRATIONS:
            current_migration = (version, name)
            checksum = sha256(sql.encode("utf-8")).hexdigest()
            recorded = applied.get(version)
            if recorded is not None:
                if recorded != (name, checksum):
                    raise StoreError(
                        f"migration {version} checksum/name mismatch: store has "
                        f"{recorded[0]!r}; this build expects {name!r}"
                    )
                continue
            _execute_sql_script(conn, sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (version, name, checksum, datetime.now(UTC).isoformat()),
            )
        conn.commit()
    except StoreError:
        conn.rollback()
        raise
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        context = (
            f"migration {current_migration[0]} ({current_migration[1]})"
            if current_migration is not None
            else "migration audit"
        )
        raise StoreError(f"{context} failed: {exc}") from exc

    for table, required in _V1_REQUIRED_COLUMNS.items():
        actual = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - actual)
        if missing:
            raise StoreError(
                f"migrated store table {table!r} is missing required column(s): "
                + ", ".join(missing)
            )


class Snapshot:
    """One retained fetch of one source."""

    __slots__ = (
        "content_sha256",
        "fetched_at",
        "http_status",
        "normalized_text",
        "raw_bytes",
        "snapshot_id",
        "source_id",
        "url",
    )

    def __init__(
        self,
        *,
        snapshot_id: int,
        source_id: str,
        url: str,
        fetched_at: datetime,
        http_status: int | None,
        content_sha256: str,
        raw_bytes: bytes,
        normalized_text: str,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.source_id = source_id
        self.url = url
        self.fetched_at = fetched_at
        self.http_status = http_status
        self.content_sha256 = content_sha256
        self.raw_bytes = raw_bytes
        self.normalized_text = normalized_text


class SnapshotStore:
    """The SQLite-backed store. Use as a context manager; it commits on a clean exit."""

    def __init__(self, path: Path, *, retention: int = DEFAULT_SNAPSHOT_RETENTION) -> None:
        if retention < 2:
            # Retention of 1 means the previous snapshot is evicted by the one that
            # replaces it, and the diff that justified a change record becomes
            # irreproducible the moment it is written. That is not a store, it's a rumour.
            raise StoreError("retention must be >= 2 so a diff is always reproducible")
        self._path = path
        self._retention = retention
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.create_function("actor_identity", 1, actor_identity, deterministic=True)
            self._conn.create_function("canonical_actor", 1, canonical_actor, deterministic=True)
            self._conn.create_function(
                "public_copy_is_safe", 1, public_copy_is_safe, deterministic=True
            )
            self._conn.create_function(
                "governance_reference_is_safe",
                1,
                governance_reference_is_safe,
                deterministic=True,
            )
            self._conn.create_function(
                "private_text_is_safe", 1, private_text_is_safe, deterministic=True
            )
            self._conn.create_function(
                "observation_fields_are_valid",
                7,
                observation_fields_are_valid,
                deterministic=True,
            )
            self._conn.execute("PRAGMA foreign_keys = ON")
            _initialize_base_schema(self._conn)
            _migrate(self._conn)
        except Exception:
            self._conn.close()
            raise

    def __enter__(self) -> SnapshotStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()
            return
        self._conn.rollback()
        self._conn.close()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    # -- snapshots ---------------------------------------------------------------

    def record_snapshot(
        self,
        *,
        source_id: str,
        url: str,
        fetched_at: datetime,
        http_status: int | None,
        content_sha256: str,
        raw_bytes: bytes,
        normalized_text: str,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO snapshots "
            "(source_id, url, fetched_at, http_status, content_sha256, raw_bytes, normalized_text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                url,
                fetched_at.isoformat(),
                http_status,
                content_sha256,
                raw_bytes,
                normalized_text,
            ),
        )
        self._prune(source_id)
        self._conn.commit()
        snapshot_id = cursor.lastrowid
        if snapshot_id is None:  # pragma: no cover — sqlite always sets this on INSERT
            raise StoreError("sqlite did not return a snapshot id")
        return snapshot_id

    def latest_snapshot(self, source_id: str) -> Snapshot | None:
        """The most recent retained snapshot, or None if this source has never been fetched.
        `None` is the baseline case: a first sighting is not a change, and `watch` must not
        report one."""
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE source_id = ? ORDER BY snapshot_id DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        return _row_to_snapshot(row) if row else None

    def snapshots(self, source_id: str) -> tuple[Snapshot, ...]:
        """Retained snapshots, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM snapshots WHERE source_id = ? ORDER BY snapshot_id DESC",
            (source_id,),
        ).fetchall()
        return tuple(_row_to_snapshot(row) for row in rows)

    def _prune(self, source_id: str) -> None:
        """Keep the newest `retention` snapshots for this source; drop the rest. Bounded
        storage is a feature: this runs unattended on a cheap box, and an unbounded blob
        table of every fetch of every state page for a decade is how a solo-dev service
        quietly dies."""
        self._conn.execute(
            "DELETE FROM snapshots WHERE source_id = ? AND snapshot_id NOT IN ("
            "  SELECT snapshot_id FROM snapshots WHERE source_id = ?"
            "  ORDER BY snapshot_id DESC LIMIT ?"
            ")",
            (source_id, source_id, self._retention),
        )

    # -- source health (the outage-vs-removal signal) -----------------------------

    def record_failure(self, source_id: str, *, error: str, status: int | None = None) -> int:
        """Record one failed fetch and return the source's new consecutive-failure count.

        Returning the streak (rather than making the caller re-query it) keeps the
        increment and the threshold test in one place in `watch()`, so there is no window
        in which they can disagree.
        """
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO source_health"
            " (source_id, consecutive_failures, last_status, last_error, last_failure_at)"
            " VALUES (?, 1, ?, ?, ?)"
            " ON CONFLICT (source_id) DO UPDATE SET"
            "   consecutive_failures = source_health.consecutive_failures + 1,"
            "   last_status = excluded.last_status,"
            "   last_error = excluded.last_error,"
            "   last_failure_at = excluded.last_failure_at",
            (source_id, status, error, now),
        )
        self._conn.commit()
        return self.failure_streak(source_id)

    def record_success(self, source_id: str) -> None:
        """Reset the streak. A single successful fetch is total exoneration: whatever was
        wrong — an outage, a WAF mood, a bad deploy — the source is answering again, and a
        source that is answering again is not a source that was taken down. Carrying any
        part of the old streak forward would let long-ago flakiness eventually escalate a
        perfectly healthy page."""
        self._conn.execute(
            "INSERT INTO source_health"
            " (source_id, consecutive_failures, last_success_at) VALUES (?, 0, ?)"
            " ON CONFLICT (source_id) DO UPDATE SET"
            "   consecutive_failures = 0,"
            "   last_error = NULL,"
            "   last_status = NULL,"
            "   last_success_at = excluded.last_success_at",
            (source_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def failure_streak(self, source_id: str) -> int:
        """Consecutive failed fetches. Zero for a source that has never been fetched, and
        zero for one that succeeded last run."""
        row = self._conn.execute(
            "SELECT consecutive_failures FROM source_health WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return int(row["consecutive_failures"]) if row else 0

    # -- watch runs and attempts --------------------------------------------------

    def start_watch_run(
        self,
        *,
        as_of: date,
        registry_version: str,
        registry_revision: str,
        jurisdiction: str | None,
        sources: tuple[RunSourceInput, ...],
        started_at: datetime | None = None,
    ) -> str:
        """Create the immutable run denominator before the first network attempt.

        Every considered source is copied into ``run_sources`` with the exact eligibility
        reasons used on this date.  That makes a later percentage auditable as a set of IDs,
        not just two counters whose membership disappeared with a registry edit.
        """

        ids = [source.source_id for source in sources]
        if len(ids) != len(set(ids)):
            raise StoreError("run source ids must be unique")
        run_id = uuid4().hex
        stamp = _as_utc(started_at or datetime.now(UTC))
        eligible_count = sum(source.eligible for source in sources)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO watch_runs "
                "(run_id, started_at, as_of, registry_version, registry_revision, "
                " jurisdiction, state, eligible_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    stamp.isoformat(),
                    as_of.isoformat(),
                    registry_version,
                    registry_revision,
                    jurisdiction,
                    RUN_RUNNING,
                    eligible_count,
                ),
            )
            self._conn.executemany(
                "INSERT INTO run_sources "
                "(run_id, source_id, jurisdiction, document_class, url, authority, eligible, "
                " eligibility_reasons) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        run_id,
                        source.source_id,
                        source.jurisdiction,
                        source.document_class,
                        source.url,
                        source.authority,
                        int(source.eligible),
                        json.dumps(source.eligibility_reasons, separators=(",", ":")),
                    )
                    for source in sources
                ),
            )
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not start watch run: {exc}") from exc
        self._conn.commit()
        return run_id

    def begin_fetch_attempt(
        self,
        run_id: str,
        *,
        source_id: str,
        url: str,
        attempted_at: datetime | None = None,
    ) -> None:
        """Persist an attempt before calling the network boundary.

        If the process dies inside the fetch, the attempt is still in the numerator with no
        terminal outcome.  A restarted operator can see a running/failed receipt rather than
        a flattering denominator that forgot the call ever began.
        """

        stamp = _as_utc(attempted_at or datetime.now(UTC)).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                "UPDATE run_sources SET attempted = 1, outcome = 'running' "
                "WHERE run_id = ? AND source_id = ? AND url = ? AND eligible = 1 "
                "AND EXISTS (SELECT 1 FROM watch_runs "
                "            WHERE run_id = ? AND state = 'running')",
                (run_id, source_id, url, run_id),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise StoreError(
                    "attempt refused for ineligible or unknown, identity-mismatched, or terminal "
                    f"run source: {run_id}/{source_id}"
                )
            self._conn.execute(
                "INSERT INTO fetch_attempts (run_id, source_id, url, attempted_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, source_id, url, stamp),
            )
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise StoreError(
                f"attempt refused for run {run_id!r}, source {source_id!r}: {exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not begin fetch attempt: {exc}") from exc
        self._conn.commit()

    def finish_fetch_attempt(
        self,
        run_id: str,
        *,
        source_id: str,
        ok: bool,
        http_status: int | None,
        content_type: str,
        error: str,
        completed_at: datetime | None = None,
    ) -> None:
        stamp = _as_utc(completed_at or datetime.now(UTC)).isoformat()
        outcome = "success" if ok else "failure"
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                "UPDATE fetch_attempts SET completed_at = ?, ok = ?, http_status = ?, "
                "content_type = ?, error = ? WHERE run_id = ? AND source_id = ? "
                "AND completed_at IS NULL "
                "AND EXISTS (SELECT 1 FROM watch_runs "
                "            WHERE run_id = ? AND state = 'running')",
                (stamp, int(ok), http_status, content_type, error, run_id, source_id, run_id),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise StoreError(
                    f"unknown or already-terminal fetch attempt, or terminal-run attempt: "
                    f"{run_id}/{source_id}"
                )
            source_cursor = self._conn.execute(
                "UPDATE run_sources SET retrieval_success = ?, outcome = ?, error = ? "
                "WHERE run_id = ? AND source_id = ? AND attempted = 1",
                (int(ok), outcome, error, run_id, source_id),
            )
            if source_cursor.rowcount != 1:
                self._conn.rollback()
                raise StoreError(f"fetch attempt lost its run source: {run_id}/{source_id}")
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not finish fetch attempt: {exc}") from exc
        self._conn.commit()

    def finish_watch_run(
        self,
        run_id: str,
        *,
        state: str,
        observation_count: int | None = None,
        error: str = "",
        completed_at: datetime | None = None,
    ) -> None:
        _validate_requested_terminal_state(state, observation_count)
        try:
            # Freeze attempts, observations, validation, and the terminal update behind one
            # writer lock.  Otherwise a second connection can complete a fetch after counts
            # are read but before state is persisted, creating a contradictory receipt.
            self._conn.execute("BEGIN IMMEDIATE")
            counts = self._conn.execute(
                "SELECT COUNT(*) AS attempted, "
                "COALESCE(SUM(CASE WHEN retrieval_success = 1 THEN 1 ELSE 0 END), 0) "
                "AS successful FROM run_sources WHERE run_id = ? AND attempted = 1",
                (run_id,),
            ).fetchone()
            run = self._conn.execute(
                "SELECT eligible_count, state FROM watch_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None or run["state"] != RUN_RUNNING:
                raise StoreError(f"unknown or already-terminal watch run: {run_id!r}")
            persisted_observations = int(
                self._conn.execute(
                    "SELECT COUNT(*) AS count FROM run_observations WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["count"]
            )
            attempted_count = int(counts["attempted"])
            successful_count = int(counts["successful"])
            eligible_count = int(run["eligible_count"])
            _validate_terminal_evidence(
                state=state,
                eligible_count=eligible_count,
                attempted_count=attempted_count,
                successful_count=successful_count,
                persisted_observations=persisted_observations,
                claimed_observations=observation_count,
            )
            cursor = self._conn.execute(
                "UPDATE watch_runs SET completed_at = ?, state = ?, attempted_count = ?, "
                "successful_count = ?, observation_count = ?, error = ? "
                "WHERE run_id = ? AND state = 'running'",
                (
                    _as_utc(completed_at or datetime.now(UTC)).isoformat(),
                    state,
                    attempted_count,
                    successful_count,
                    persisted_observations,
                    error,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"unknown or already-terminal watch run: {run_id!r}")
            self._conn.commit()
        except StoreError:
            self._conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not finish watch run: {exc}") from exc

    def watch_run(self, run_id: str) -> WatchRun:
        row = self._conn.execute("SELECT * FROM watch_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise StoreError(f"unknown watch run: {run_id!r}")
        return self._row_to_watch_run(row)

    def latest_watch_run(
        self,
        *,
        successful_only: bool = False,
        aggregate_only: bool = False,
    ) -> WatchRun | None:
        """Return the newest receipt, optionally restricted to whole-registry runs.

        A jurisdiction-scoped run is useful operational evidence for that jurisdiction, but
        it cannot make the aggregate public feed green.  ``aggregate_only`` makes that safety
        boundary explicit at the query rather than asking a caller to inspect scope later.
        """

        clauses: list[str] = []
        params: list[str] = []
        if successful_only:
            placeholders = ",".join("?" for _ in _SUCCESSFUL_RUN_STATES)
            clauses.append(f"state IN ({placeholders})")
            params.extend(sorted(_SUCCESSFUL_RUN_STATES))
        if aggregate_only:
            clauses.append("jurisdiction IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._conn.execute(
            f"SELECT * FROM watch_runs{where} "  # noqa: S608 -- clauses are module literals
            "ORDER BY started_at DESC, run_id DESC LIMIT 1",
            params,
        ).fetchone()
        return self._row_to_watch_run(row) if row is not None else None

    def _row_to_watch_run(self, row: sqlite3.Row) -> WatchRun:
        source_rows = self._conn.execute(
            "SELECT source_id, eligible, attempted, retrieval_success FROM run_sources "
            "WHERE run_id = ? ORDER BY source_id",
            (row["run_id"],),
        ).fetchall()
        eligible = tuple(str(source["source_id"]) for source in source_rows if source["eligible"])
        attempted = tuple(str(source["source_id"]) for source in source_rows if source["attempted"])
        successful = tuple(
            str(source["source_id"]) for source in source_rows if source["retrieval_success"] == 1
        )
        persisted_observation_count = int(
            self._conn.execute(
                "SELECT COUNT(*) AS count FROM run_observations WHERE run_id = ?",
                (row["run_id"],),
            ).fetchone()["count"]
        )
        # The redundant counts are an integrity tripwire.  Public percentages are derived
        # from the exact ID sets, but a mismatch says the store was tampered with or a
        # migration lost information and must fail before publication.
        expected = (
            int(row["eligible_count"]),
            int(row["attempted_count"]),
            int(row["successful_count"]),
            int(row["observation_count"]),
        )
        actual = (len(eligible), len(attempted), len(successful), persisted_observation_count)
        if row["state"] != RUN_RUNNING and expected != actual:
            raise StoreError(
                f"watch run {row['run_id']} count/set mismatch: stored={expected}, exact={actual}"
            )
        return WatchRun(
            run_id=str(row["run_id"]),
            started_at=_parse_dt(str(row["started_at"])),
            completed_at=_parse_dt(str(row["completed_at"])) if row["completed_at"] else None,
            as_of=date.fromisoformat(str(row["as_of"])),
            registry_version=str(row["registry_version"]),
            registry_revision=str(row["registry_revision"]),
            jurisdiction=str(row["jurisdiction"]) if row["jurisdiction"] else None,
            state=str(row["state"]),
            eligible_source_ids=eligible,
            attempted_source_ids=attempted,
            successful_source_ids=successful,
            observation_count=int(row["observation_count"]),
            error=str(row["error"]),
        )

    # -- changes -----------------------------------------------------------------

    def record_change(self, change: ChangeRecord, *, run_id: str | None = None) -> None:
        """Insert a change idempotently and optionally bind it to its watcher run.

        The id is deterministic in (source, previous_hash, new_hash), so re-running `watch`
        over unchanged drift is a no-op rather than a duplicate — and, critically, a re-run
        **cannot overwrite a human's review** with a fresh `unreviewed` record. A watcher
        that silently un-reviews its own queue would train its reviewer to distrust it.

        Note the exact conflict clause. `INSERT OR IGNORE` would be the obvious way to write
        this and it is **wrong**: in SQLite, `OR IGNORE` swallows *every* constraint
        violation on the row, CHECK constraints included. It would therefore silently
        discard exactly the rows the human-in-the-loop CHECK above exists to reject — an
        illegal machine-classified change would vanish without raising, and the gate would
        pass while enforcing nothing. `ON CONFLICT (change_id) DO NOTHING` scopes the
        forgiveness to the primary-key collision we actually want to tolerate, and leaves
        the CHECK constraints loud.
        """
        if not change.observation_valid:
            raise StoreError("change observation fields are invalid")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO changes "
                "(change_id, source_id, jurisdiction, document_class, url, observed_at,"
                " previous_hash, new_hash, diff_excerpt, kind, significance, review_status,"
                " reviewer, reviewed_at, review_note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (change_id) DO NOTHING",
                (
                    change.id,
                    change.source_id,
                    change.jurisdiction,
                    change.document_class,
                    change.url,
                    change.observed_at.isoformat(),
                    change.previous_hash,
                    change.new_hash,
                    change.diff_excerpt,
                    str(change.kind),
                    str(change.significance),
                    str(change.review_status),
                    change.reviewer,
                    change.reviewed_at.isoformat() if change.reviewed_at else None,
                    change.review_note,
                ),
            )
            if run_id is not None:
                association = self._conn.execute(
                    "INSERT INTO run_observations (run_id, change_id, observed_at) "
                    "SELECT ?, ?, ? WHERE EXISTS "
                    "(SELECT 1 FROM watch_runs AS run "
                    " JOIN run_sources AS source ON source.run_id = run.run_id "
                    " WHERE run.run_id = ? AND run.state = 'running' "
                    " AND source.source_id = ? AND source.jurisdiction = ? "
                    " AND source.document_class = ? AND source.url = ? "
                    " AND source.eligible = 1 AND source.attempted = 1) "
                    "ON CONFLICT (run_id, change_id) DO NOTHING",
                    (
                        run_id,
                        change.id,
                        _as_utc(change.observed_at).isoformat(),
                        run_id,
                        change.source_id,
                        change.jurisdiction,
                        change.document_class,
                        change.url,
                    ),
                )
                if association.rowcount != 1:
                    existing = self._conn.execute(
                        "SELECT 1 FROM run_observations AS observation "
                        "JOIN watch_runs AS run ON run.run_id = observation.run_id "
                        "WHERE observation.run_id = ? AND observation.change_id = ? "
                        "AND run.state = 'running'",
                        (run_id, change.id),
                    ).fetchone()
                    if existing is None:
                        raise StoreError(
                            f"change observation refused for unknown or terminal run: {run_id!r}"
                        )
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            # This is the schema-level human-in-the-loop CHECK firing. If it ever fires,
            # something tried to store a classification with no human behind it.
            raise StoreError(f"change rejected by the store's integrity rules: {exc}") from exc
        except StoreError:
            self._conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not record change: {exc}") from exc
        except Exception as exc:
            self._conn.rollback()
            raise StoreError("could not record change: invalid non-database value") from exc
        self._conn.commit()

    def update_change(self, change: ChangeRecord) -> None:
        """Append the first human decision without mutating the observation.

        The method name is retained for the alpha CLI/API, but the V1 storage meaning is now
        append-only.  The old mutable columns remain only for migration compatibility and are
        never written by this path.
        """
        if (
            not change.observation_valid
            or change.review_status is ReviewStatus.UNREVIEWED
            or not change.reviewer
            or change.reviewed_at is None
            or not change.first_review_valid
        ):
            raise StoreError("first review decision is incomplete")
        decision_id = _stable_event_id(
            "review",
            change.id,
            "first",
            str(change.review_status),
            change.reviewer,
            change.reviewed_at.isoformat(),
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if (
                self._conn.execute(
                    "SELECT 1 FROM changes WHERE change_id = ?", (change.id,)
                ).fetchone()
                is None
            ):
                raise StoreError(f"unknown change id: {change.id!r}")
            self._conn.execute(
                "INSERT INTO review_decisions "
                "(decision_id, change_id, stage, decision, significance, actor, decided_at, "
                " internal_rationale, public_copy) VALUES (?, ?, 'first', ?, ?, ?, ?, ?, ?)",
                (
                    decision_id,
                    change.id,
                    str(change.review_status),
                    str(change.significance),
                    change.reviewer,
                    _as_utc(change.reviewed_at).isoformat(),
                    change.internal_rationale,
                    change.review_note,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise StoreError(f"change rejected by the store's integrity rules: {exc}") from exc
        except StoreError:
            self._conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not append first review: {exc}") from exc
        except Exception as exc:
            self._conn.rollback()
            raise StoreError("could not append first review: invalid non-database value") from exc

    def record_independent_review(self, change: ChangeRecord) -> None:
        """Append a qualified, conflict-attested decision for a substantive item."""

        if (
            not change.observation_valid
            or not change.first_review_valid
            or change.independent_review_status is None
            or not change.independent_reviewer
            or change.independent_reviewed_at is None
            or not change.independent_qualification_ref
            or not change.independent_conflict_attestation_ref
            or not change.independent_review_valid
        ):
            raise StoreError("independent review decision is incomplete")
        decision_id = _stable_event_id(
            "review",
            change.id,
            "independent",
            str(change.independent_review_status),
            change.independent_reviewer,
            change.independent_reviewed_at.isoformat(),
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO review_decisions "
                "(decision_id, change_id, stage, decision, significance, actor, decided_at, "
                " internal_rationale, public_copy, qualification_ref, "
                " conflict_attestation_ref) "
                "VALUES (?, ?, 'independent', ?, 'substantive', ?, ?, ?, '', ?, ?)",
                (
                    decision_id,
                    change.id,
                    str(change.independent_review_status),
                    change.independent_reviewer,
                    _as_utc(change.independent_reviewed_at).isoformat(),
                    change.independent_rationale,
                    change.independent_qualification_ref,
                    change.independent_conflict_attestation_ref,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise StoreError(f"independent review rejected by integrity rules: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not append independent review: {exc}") from exc
        except Exception as exc:
            self._conn.rollback()
            raise StoreError(
                "could not append independent review: invalid non-database value"
            ) from exc

    def record_lifecycle_event(self, change: ChangeRecord) -> None:
        """Append one correction or withdrawal event; history is never overwritten."""

        if (
            change.publication_status is PublicationStatus.ACTIVE
            or not change.lifecycle_reason
            or not change.lifecycle_actor
            or change.lifecycle_at is None
            or not change.lifecycle_valid
            or not change.publishable
        ):
            raise StoreError("correction or withdrawal event is incomplete")
        action = str(change.publication_status)
        event_id = _stable_event_id(
            "lifecycle",
            change.id,
            action,
            change.superseded_by or "",
            change.lifecycle_actor,
            change.lifecycle_at.isoformat(),
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO change_lifecycle_events "
                "(event_id, change_id, action, superseded_by_change_id, reason, actor, "
                " decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    change.id,
                    action,
                    change.superseded_by,
                    change.lifecycle_reason,
                    change.lifecycle_actor,
                    _as_utc(change.lifecycle_at).isoformat(),
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise StoreError(f"lifecycle event rejected by integrity rules: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise StoreError(f"could not append lifecycle event: {exc}") from exc
        except Exception as exc:
            self._conn.rollback()
            raise StoreError(
                "could not append lifecycle event: invalid non-database value"
            ) from exc

    def get_change(self, change_id: str) -> ChangeRecord:
        row = self._conn.execute(
            "SELECT * FROM changes WHERE change_id = ?", (change_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown change id: {change_id!r}")
        return self._project_change(row)

    def changes(
        self,
        *,
        review_status: ReviewStatus | None = None,
        jurisdiction: str | None = None,
    ) -> tuple[ChangeRecord, ...]:
        """Query changes, newest first, then filter the immutable-event projection.

        Review state no longer lives in mutable columns: each row is projected from
        append-only decisions before any review-status filter is applied.
        """
        clauses: list[str] = []
        params: list[str] = []
        if jurisdiction is not None:
            clauses.append("jurisdiction = ?")
            params.append(jurisdiction.upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM changes{where} ORDER BY observed_at DESC, change_id",  # noqa: S608 — clauses are literals, values are bound
            params,
        ).fetchall()
        projected = tuple(self._project_change(row) for row in rows)
        if review_status is None:
            return projected
        return tuple(change for change in projected if change.review_status is review_status)

    def _project_change(self, row: sqlite3.Row) -> ChangeRecord:
        """Derive current state from immutable decisions while quarantining legacy notes."""

        change = _row_to_change(row)
        first = self._conn.execute(
            "SELECT * FROM review_decisions WHERE change_id = ? AND stage = 'first'",
            (change.id,),
        ).fetchone()
        if first is not None:
            change = replace(
                change,
                significance=Significance(str(first["significance"])),
                review_status=ReviewStatus(str(first["decision"])),
                reviewer=str(first["actor"]),
                reviewed_at=_parse_dt(str(first["decided_at"])),
                review_note=str(first["public_copy"]),
                internal_rationale=str(first["internal_rationale"]),
            )
        elif change.review_status is not ReviewStatus.UNREVIEWED:
            # Alpha stores exposed `review_note` directly.  Preserve it only as private
            # rationale and withhold the record until an operator creates an audited V1
            # decision with constrained public copy.
            change = replace(
                change,
                significance=Significance.UNCLASSIFIED,
                review_status=ReviewStatus.UNREVIEWED,
                reviewer=None,
                reviewed_at=None,
                review_note="",
                internal_rationale=change.review_note,
            )
        independent = self._conn.execute(
            "SELECT * FROM review_decisions WHERE change_id = ? AND stage = 'independent'",
            (change.id,),
        ).fetchone()
        if independent is not None:
            change = replace(
                change,
                independent_review_status=IndependentReviewStatus(str(independent["decision"])),
                independent_reviewer=str(independent["actor"]),
                independent_reviewed_at=_parse_dt(str(independent["decided_at"])),
                independent_rationale=str(independent["internal_rationale"]),
                independent_qualification_ref=str(independent["qualification_ref"]),
                independent_conflict_attestation_ref=str(independent["conflict_attestation_ref"]),
            )
        lifecycle = self._conn.execute(
            "SELECT * FROM change_lifecycle_events WHERE change_id = ?", (change.id,)
        ).fetchone()
        if lifecycle is not None:
            change = replace(
                change,
                publication_status=PublicationStatus(str(lifecycle["action"])),
                superseded_by=(
                    str(lifecycle["superseded_by_change_id"])
                    if lifecycle["superseded_by_change_id"] is not None
                    else None
                ),
                lifecycle_reason=str(lifecycle["reason"]),
                lifecycle_actor=str(lifecycle["actor"]),
                lifecycle_at=_parse_dt(str(lifecycle["decided_at"])),
            )
        return change

    def __iter__(self) -> Iterator[ChangeRecord]:
        return iter(self.changes())


def _row_to_snapshot(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        snapshot_id=row["snapshot_id"],
        source_id=row["source_id"],
        url=row["url"],
        fetched_at=_parse_dt(row["fetched_at"]),
        http_status=row["http_status"],
        content_sha256=row["content_sha256"],
        raw_bytes=row["raw_bytes"],
        normalized_text=row["normalized_text"],
    )


def _row_to_change(row: sqlite3.Row) -> ChangeRecord:
    reviewed_at = row["reviewed_at"]
    return ChangeRecord(
        id=row["change_id"],
        source_id=row["source_id"],
        jurisdiction=row["jurisdiction"],
        document_class=row["document_class"],
        url=row["url"],
        observed_at=_parse_dt(row["observed_at"]),
        previous_hash=row["previous_hash"],
        new_hash=row["new_hash"],
        diff_excerpt=row["diff_excerpt"],
        kind=ChangeKind(row["kind"]),
        significance=Significance(row["significance"]),
        review_status=ReviewStatus(row["review_status"]),
        reviewer=row["reviewer"],
        reviewed_at=_parse_dt(reviewed_at) if reviewed_at else None,
        review_note=row["review_note"],
    )


def _parse_dt(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


def _stable_event_id(*parts: str) -> str:
    """Content-address one immutable operator event without leaking its text."""

    return sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_requested_terminal_state(state: str, observation_count: int | None) -> None:
    if state not in _TERMINAL_RUN_STATES:
        raise StoreError(f"invalid terminal watch-run state: {state!r}")
    if observation_count is not None and observation_count < 0:
        raise StoreError("watch-run observation_count cannot be negative")


def _validate_terminal_evidence(
    *,
    state: str,
    eligible_count: int,
    attempted_count: int,
    successful_count: int,
    persisted_observations: int,
    claimed_observations: int | None,
) -> None:
    if state != RUN_FAILED and (eligible_count == 0 or attempted_count != eligible_count):
        raise StoreError(
            f"cannot mark run {state}: attempted {attempted_count} of "
            f"{eligible_count} eligible sources"
        )
    if state == RUN_QUIET and (persisted_observations != 0 or successful_count != eligible_count):
        raise StoreError(
            "quiet requires zero observations and successful retrieval of every eligible source"
        )
    if state == RUN_COMPLETE and (
        persisted_observations == 0 or successful_count != eligible_count
    ):
        raise StoreError(
            "complete requires at least one observation and successful retrieval of every "
            "eligible source"
        )
    if state == RUN_PARTIAL and successful_count >= eligible_count:
        raise StoreError("partial requires at least one failed eligible-source retrieval")
    if claimed_observations is not None and claimed_observations != persisted_observations:
        raise StoreError(
            "watch-run observation count does not match persisted run observations: "
            f"claimed {claimed_observations}, exact {persisted_observations}"
        )
