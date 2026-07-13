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

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.errors import StoreError

__all__ = ["DEFAULT_SNAPSHOT_RETENTION", "Snapshot", "SnapshotStore"]

DEFAULT_SNAPSHOT_RETENTION = 5

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


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for stores created by an earlier version.

    `CREATE TABLE IF NOT EXISTS` is a no-op against an existing table, so a column added to
    `_SCHEMA` never reaches a database that already exists — the snapshot store is meant to
    be long-lived (that is the entire point of retaining the bytes), so "just delete the db"
    is not an acceptable upgrade path. `kind` is added with a DEFAULT, which back-fills every
    pre-existing row as `content_drift`: correct, because every record written before this
    column existed was, by construction, a content-drift record.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(changes)")}
    if columns and "kind" not in columns:
        conn.execute("ALTER TABLE changes ADD COLUMN kind TEXT NOT NULL DEFAULT 'content_drift'")


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
        self._conn.executescript(_SCHEMA)
        _migrate(self._conn)
        self._conn.commit()

    def __enter__(self) -> SnapshotStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

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

    # -- changes -----------------------------------------------------------------

    def record_change(self, change: ChangeRecord) -> None:
        """Insert a change, idempotently.

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
        try:
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
        except sqlite3.IntegrityError as exc:
            # This is the schema-level human-in-the-loop CHECK firing. If it ever fires,
            # something tried to store a classification with no human behind it.
            raise StoreError(f"change rejected by the store's integrity rules: {exc}") from exc
        self._conn.commit()

    def update_change(self, change: ChangeRecord) -> None:
        """Persist a reviewed record over its observed predecessor."""
        try:
            cursor = self._conn.execute(
                "UPDATE changes SET significance = ?, review_status = ?, reviewer = ?,"
                " reviewed_at = ?, review_note = ? WHERE change_id = ?",
                (
                    str(change.significance),
                    str(change.review_status),
                    change.reviewer,
                    change.reviewed_at.isoformat() if change.reviewed_at else None,
                    change.review_note,
                    change.id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"change rejected by the store's integrity rules: {exc}") from exc
        if cursor.rowcount == 0:
            raise StoreError(f"unknown change id: {change.id!r}")
        self._conn.commit()

    def get_change(self, change_id: str) -> ChangeRecord:
        row = self._conn.execute(
            "SELECT * FROM changes WHERE change_id = ?", (change_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown change id: {change_id!r}")
        return _row_to_change(row)

    def changes(
        self,
        *,
        review_status: ReviewStatus | None = None,
        jurisdiction: str | None = None,
    ) -> tuple[ChangeRecord, ...]:
        """Query changes, newest first. Filtering happens in SQL so the publisher's
        "confirmed only" query is one statement a reader can audit, not a Python filter
        that a later refactor could widen by accident."""
        clauses: list[str] = []
        params: list[str] = []
        if review_status is not None:
            clauses.append("review_status = ?")
            params.append(str(review_status))
        if jurisdiction is not None:
            clauses.append("jurisdiction = ?")
            params.append(jurisdiction.upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM changes{where} ORDER BY observed_at DESC, change_id",  # noqa: S608 — clauses are literals, values are bound
            params,
        ).fetchall()
        return tuple(_row_to_change(row) for row in rows)

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
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
