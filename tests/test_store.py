"""Tests for :mod:`id_churn_sentinel.core.store` — snapshots, retention, change round-trip."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import id_churn_sentinel.core.store as store_module
from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.store import (
    RUN_FAILED,
    RUN_QUIET,
    RunSourceInput,
    SnapshotStore,
)
from id_churn_sentinel.errors import StoreError

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def record(store: SnapshotStore, source_id: str, digest: str, text: str = "t") -> int:
    return store.record_snapshot(
        source_id=source_id,
        url="https://ex.gov/p",
        fetched_at=NOW,
        http_status=200,
        content_sha256=digest,
        raw_bytes=b"<p>t</p>",
        normalized_text=text,
    )


def test_snapshot_round_trips(store: SnapshotStore) -> None:
    record(store, "s1", "abc")
    latest = store.latest_snapshot("s1")

    assert latest is not None
    assert latest.content_sha256 == "abc"
    assert latest.raw_bytes == b"<p>t</p>"
    assert latest.normalized_text == "t"
    assert latest.http_status == 200
    assert latest.fetched_at == NOW


def test_latest_snapshot_of_an_unseen_source_is_none(store: SnapshotStore) -> None:
    """The baseline case. `None` is what makes a first sighting not-a-change."""
    assert store.latest_snapshot("never-fetched") is None


def test_retention_keeps_the_last_n_snapshots(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "s.db", retention=3) as store:
        for i in range(6):
            record(store, "s1", f"h{i}")

        snapshots = store.snapshots("s1")
        assert [s.content_sha256 for s in snapshots] == ["h5", "h4", "h3"]


def test_retention_is_per_source(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "s.db", retention=2) as store:
        for i in range(4):
            record(store, "s1", f"a{i}")
            record(store, "s2", f"b{i}")

        assert len(store.snapshots("s1")) == 2
        assert len(store.snapshots("s2")) == 2


def test_retention_below_two_is_refused(tmp_path: Path) -> None:
    """Retention of 1 evicts the previous snapshot with the one that replaces it, making
    the diff that justified a change record irreproducible the moment it is written."""
    with pytest.raises(StoreError, match="reproducible"):
        SnapshotStore(tmp_path / "s.db", retention=1)


def test_store_creates_its_parent_directory(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "nested" / "deep" / "s.db") as store:
        record(store, "s1", "abc")
        assert store.latest_snapshot("s1") is not None


def test_change_round_trips(store: SnapshotStore, observed_change: ChangeRecord) -> None:
    store.record_change(observed_change)
    loaded = store.get_change(observed_change.id)

    assert loaded == observed_change


def test_reviewed_change_round_trips(
    store: SnapshotStore, observed_change: ChangeRecord, confirmed_change: ChangeRecord
) -> None:
    store.record_change(observed_change)
    store.update_change(confirmed_change)
    store.record_independent_review(confirmed_change)
    loaded = store.get_change(confirmed_change.id)

    assert loaded.significance is Significance.SUBSTANTIVE
    assert loaded.review_status is ReviewStatus.CONFIRMED
    assert loaded.reviewer == "Chelsea Kelly-Reif"
    assert loaded.reviewed_at is not None
    assert loaded.publishable


def test_recording_the_same_change_twice_is_a_no_op(
    store: SnapshotStore, observed_change: ChangeRecord, confirmed_change: ChangeRecord
) -> None:
    """INSERT OR IGNORE: a re-run cannot overwrite a human's review with a fresh
    `unreviewed` record."""
    store.record_change(observed_change)
    store.update_change(confirmed_change)
    store.record_change(observed_change)  # the detector sees the same drift again

    assert len(store.changes()) == 1
    assert store.get_change(observed_change.id).review_status is ReviewStatus.CONFIRMED


def test_unknown_change_id_raises(store: SnapshotStore) -> None:
    with pytest.raises(StoreError, match="unknown change id"):
        store.get_change("nope")


def test_updating_an_unknown_change_raises(
    store: SnapshotStore, confirmed_change: ChangeRecord
) -> None:
    with pytest.raises(StoreError, match="unknown change id"):
        store.update_change(confirmed_change)


def test_changes_filter_by_review_status(
    store: SnapshotStore, observed_change: ChangeRecord, confirmed_change: ChangeRecord
) -> None:
    store.record_change(observed_change)
    assert len(store.changes(review_status=ReviewStatus.UNREVIEWED)) == 1
    assert len(store.changes(review_status=ReviewStatus.CONFIRMED)) == 0

    store.update_change(confirmed_change)
    assert len(store.changes(review_status=ReviewStatus.UNREVIEWED)) == 0
    assert len(store.changes(review_status=ReviewStatus.CONFIRMED)) == 1


def test_changes_filter_by_jurisdiction(
    store: SnapshotStore, observed_change: ChangeRecord
) -> None:
    store.record_change(observed_change)
    assert len(store.changes(jurisdiction="TX")) == 1
    assert len(store.changes(jurisdiction="tx")) == 1  # normalized at the boundary
    assert len(store.changes(jurisdiction="CA")) == 0


def test_store_is_iterable(store: SnapshotStore, observed_change: ChangeRecord) -> None:
    store.record_change(observed_change)
    assert [c.id for c in store] == [observed_change.id]


def test_data_persists_across_reopen(tmp_path: Path, observed_change: ChangeRecord) -> None:
    db = tmp_path / "s.db"
    with SnapshotStore(db) as store:
        store.record_change(observed_change)
        record(store, "s1", "abc")

    with SnapshotStore(db) as reopened:
        assert reopened.get_change(observed_change.id) == observed_change
        latest = reopened.latest_snapshot("s1")
        assert latest is not None and latest.content_sha256 == "abc"


# -- source health (the outage-vs-removal signal) ---------------------------------


def test_failure_streak_starts_at_zero_for_an_unseen_source(store: SnapshotStore) -> None:
    assert store.failure_streak("never-fetched") == 0


def test_record_failure_increments_and_returns_the_streak(store: SnapshotStore) -> None:
    assert store.record_failure("s1", error="HTTP 404", status=404) == 1
    assert store.record_failure("s1", error="HTTP 404", status=404) == 2
    assert store.failure_streak("s1") == 3 - 1


def test_record_success_resets_the_streak(store: SnapshotStore) -> None:
    store.record_failure("s1", error="HTTP 503", status=503)
    store.record_failure("s1", error="HTTP 503", status=503)
    assert store.failure_streak("s1") == 2

    store.record_success("s1")

    assert store.failure_streak("s1") == 0


def test_failure_streaks_are_per_source(store: SnapshotStore) -> None:
    store.record_failure("s1", error="down", status=None)
    store.record_failure("s1", error="down", status=None)
    store.record_failure("s2", error="down", status=None)

    assert store.failure_streak("s1") == 2
    assert store.failure_streak("s2") == 1


def test_a_failure_streak_persists_across_reopen(tmp_path: Path) -> None:
    """A weekly cron job is a new process every week. A streak held only in memory would
    reset on every run and could never reach any threshold — a no-op that still passes its
    unit tests. The store is what makes the mechanism real."""
    db = tmp_path / "s.db"
    with SnapshotStore(db) as store:
        store.record_failure("s1", error="HTTP 404", status=404)
        store.record_failure("s1", error="HTTP 404", status=404)

    with SnapshotStore(db) as reopened:
        assert reopened.failure_streak("s1") == 2


def test_an_old_store_without_the_kind_column_is_migrated(tmp_path: Path) -> None:
    """`CREATE TABLE IF NOT EXISTS` is a no-op against an existing table, so a new column
    never reaches a database that already exists. The snapshot store is deliberately
    long-lived — retaining the bytes for months is the entire point — so "delete the db and
    start over" is not an acceptable upgrade path. Simulate a pre-M3 store and prove it
    opens, migrates, and back-fills its rows as `content_drift`."""
    db = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        "CREATE TABLE changes ("
        " change_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, jurisdiction TEXT NOT NULL,"
        " document_class TEXT NOT NULL, url TEXT NOT NULL, observed_at TEXT NOT NULL,"
        " previous_hash TEXT NOT NULL, new_hash TEXT NOT NULL, diff_excerpt TEXT NOT NULL,"
        " significance TEXT NOT NULL, review_status TEXT NOT NULL, reviewer TEXT,"
        " reviewed_at TEXT, review_note TEXT NOT NULL DEFAULT '');"
        "INSERT INTO changes VALUES ('old1', 's1', 'TX', 'drivers_license', 'https://e.gov',"
        " '2026-01-01T00:00:00+00:00', 'a', 'b', 'd', 'unclassified', 'unreviewed',"
        " NULL, NULL, '');"
    )
    legacy.commit()
    legacy.close()

    with SnapshotStore(db) as migrated:
        loaded = migrated.get_change("old1")
        assert loaded.kind is ChangeKind.CONTENT_DRIFT  # back-filled, and correctly so
        assert loaded.significance is Significance.UNCLASSIFIED


def test_naive_timestamps_are_read_back_as_utc(store: SnapshotStore) -> None:
    """Defensive: a hand-edited or legacy row with a naive timestamp must not produce a
    tz-naive datetime that later explodes on comparison."""
    store.record_snapshot(
        source_id="s1",
        url="https://ex.gov/p",
        fetched_at=datetime(2026, 7, 13, 12, 0),
        http_status=200,
        content_sha256="abc",
        raw_bytes=b"",
        normalized_text="",
    )
    latest = store.latest_snapshot("s1")
    assert latest is not None
    assert latest.fetched_at.tzinfo is not None


# -- V1 migrations, runs, and exact attempt sets ---------------------------------


def _run_sources() -> tuple[RunSourceInput, ...]:
    return (
        RunSourceInput(
            source_id="eligible",
            jurisdiction="TX",
            document_class="drivers_license",
            url="https://example.gov/eligible",
            authority="Example authority",
            eligible=True,
            eligibility_reasons=(),
        ),
        RunSourceInput(
            source_id="ineligible",
            jurisdiction="TX",
            document_class="birth_certificate",
            url="https://example.gov/ineligible",
            authority="Example authority",
            eligible=False,
            eligibility_reasons=("unverified", "fetch-policy-unreviewed"),
        ),
    )


def _start_run(store: SnapshotStore, *, sources: tuple[RunSourceInput, ...] | None = None) -> str:
    return store.start_watch_run(
        as_of=date(2026, 7, 13),
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction=None,
        sources=sources if sources is not None else _run_sources(),
        started_at=NOW,
    )


def _finish_eligible_attempt(store: SnapshotStore, run_id: str, *, ok: bool) -> None:
    store.begin_fetch_attempt(run_id, source_id="eligible", url="https://example.gov/eligible")
    store.finish_fetch_attempt(
        run_id,
        source_id="eligible",
        ok=ok,
        http_status=200 if ok else 503,
        content_type="text/html",
        error="" if ok else "synthetic outage",
        completed_at=NOW,
    )


def test_run_receipt_round_trips_exact_numerator_and_denominator(store: SnapshotStore) -> None:
    run_id = store.start_watch_run(
        as_of=date(2026, 7, 13),
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction="TX",
        sources=_run_sources(),
        started_at=NOW,
    )
    store.begin_fetch_attempt(run_id, source_id="eligible", url="https://example.gov/eligible")
    store.finish_fetch_attempt(
        run_id,
        source_id="eligible",
        ok=True,
        http_status=200,
        content_type="text/html",
        error="",
        completed_at=NOW,
    )
    store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)

    receipt = store.watch_run(run_id)
    assert receipt.eligible_source_ids == ("eligible",)
    assert receipt.attempted_source_ids == ("eligible",)
    assert receipt.successful_source_ids == ("eligible",)
    assert receipt.attempt_completeness == 1.0
    assert receipt.state == RUN_QUIET
    assert receipt.registry_revision == "a" * 64


def test_an_ineligible_source_cannot_be_inserted_into_the_attempt_numerator(
    store: SnapshotStore,
) -> None:
    run_id = store.start_watch_run(
        as_of=date(2026, 7, 13),
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction=None,
        sources=_run_sources(),
        started_at=NOW,
    )

    with pytest.raises(StoreError, match="ineligible or unknown"):
        store.begin_fetch_attempt(
            run_id,
            source_id="ineligible",
            url="https://example.gov/ineligible",
        )

    receipt = store.watch_run(run_id)
    assert receipt.attempted_source_ids == ()


def test_latest_successful_run_does_not_treat_a_failure_as_success(store: SnapshotStore) -> None:
    quiet_id = store.start_watch_run(
        as_of=date(2026, 7, 13),
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction=None,
        sources=_run_sources(),
        started_at=NOW,
    )
    store.begin_fetch_attempt(quiet_id, source_id="eligible", url="https://example.gov/eligible")
    store.finish_fetch_attempt(
        quiet_id,
        source_id="eligible",
        ok=True,
        http_status=200,
        content_type="text/html",
        error="",
        completed_at=NOW,
    )
    store.finish_watch_run(quiet_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)
    failed_id = store.start_watch_run(
        as_of=date(2026, 7, 14),
        registry_version="1.0",
        registry_revision="b" * 64,
        jurisdiction=None,
        sources=(),
        started_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    store.finish_watch_run(
        failed_id,
        state=RUN_FAILED,
        observation_count=0,
        error="synthetic failure",
        completed_at=datetime(2026, 7, 14, tzinfo=UTC),
    )

    latest = store.latest_watch_run()
    assert latest is not None and latest.run_id == failed_id
    successful = store.latest_watch_run(successful_only=True)
    assert successful is not None and successful.run_id == quiet_id


def test_migration_ledger_is_created_and_a_tampered_checksum_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "migrated.db"
    with SnapshotStore(db):
        pass
    conn = sqlite3.connect(db)
    try:
        version, checksum = conn.execute(
            "SELECT version, checksum FROM schema_migrations"
        ).fetchone()
        assert version == 1
        assert len(checksum) == 64
        conn.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(StoreError, match="checksum/name mismatch"):
        SnapshotStore(db)


def test_simultaneous_first_opens_serialize_migration_initialization(tmp_path: Path) -> None:
    db = tmp_path / "concurrent-migration.db"
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    errors: list[BaseException] = []

    def open_store() -> None:
        try:
            barrier.wait(timeout=5)
            with SnapshotStore(db):
                pass
        except BaseException as exc:  # pragma: no cover - asserted after all workers join
            errors.append(exc)

    workers = [threading.Thread(target=open_store) for _ in range(worker_count)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    with SnapshotStore(db) as opened:
        applied = opened._conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert applied == len(store_module._MIGRATIONS)


def test_unknown_future_migration_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "future.db"
    with SnapshotStore(db):
        pass
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO schema_migrations VALUES (99, 'from-the-future', 'abc', ?)",
            (NOW.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(StoreError, match="does not know: 99"):
        SnapshotStore(db)


def test_failed_migration_rolls_back_its_sql_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "atomic-migration.db"
    failing = (
        *store_module._MIGRATIONS,
        (
            99,
            "synthetic-failing-migration",
            "CREATE TABLE must_rollback (value TEXT); THIS IS NOT SQL;",
        ),
    )
    monkeypatch.setattr(store_module, "_MIGRATIONS", failing)

    with pytest.raises(StoreError, match=r"migration 99 .* failed"):
        SnapshotStore(db)

    conn = sqlite3.connect(db)
    try:
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'must_rollback'"
            ).fetchone()
            is None
        )
        assert (
            conn.execute("SELECT version FROM schema_migrations WHERE version = 99").fetchone()
            is None
        )
    finally:
        conn.close()


def test_applied_migration_with_missing_table_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "missing-migrated-table.db"
    with SnapshotStore(db):
        pass
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE fetch_attempts")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(StoreError, match="table 'fetch_attempts' is missing required column"):
        SnapshotStore(db)


def test_run_source_ids_must_be_unique(store: SnapshotStore) -> None:
    duplicate = _run_sources()[0]
    with pytest.raises(StoreError, match="must be unique"):
        _start_run(store, sources=(duplicate, duplicate))


def test_run_observation_count_is_derived_from_atomic_associations(
    store: SnapshotStore,
) -> None:
    run_id = _start_run(store)
    _finish_eligible_attempt(store, run_id, ok=True)
    observation = ChangeRecord.observed(
        source_id="eligible",
        jurisdiction="TX",
        document_class="drivers_license",
        url="https://example.gov/eligible",
        observed_at=NOW,
        previous_hash="a" * 64,
        new_hash="b" * 64,
        diff_excerpt="synthetic drift",
    )
    store.record_change(observation, run_id=run_id)
    store.finish_watch_run(run_id, state=RUN_FAILED)

    receipt = store.watch_run(run_id)
    assert receipt.observation_count == 1
    with pytest.raises(StoreError, match="unknown or terminal run"):
        store.record_change(observation, run_id=run_id)


def test_unknown_run_observation_rolls_back_the_change(
    store: SnapshotStore,
    observed_change: ChangeRecord,
) -> None:
    with pytest.raises(StoreError, match="unknown or terminal run"):
        store.record_change(observed_change, run_id="missing")
    with pytest.raises(StoreError, match="unknown change id"):
        store.get_change(observed_change.id)


def test_attempt_and_run_state_transitions_fail_closed(store: SnapshotStore) -> None:
    incomplete = _start_run(store)
    with pytest.raises(StoreError, match="invalid terminal"):
        store.finish_watch_run(incomplete, state="green", observation_count=0)
    with pytest.raises(StoreError, match="cannot be negative"):
        store.finish_watch_run(incomplete, state=RUN_FAILED, observation_count=-1)
    with pytest.raises(StoreError, match="attempted 0 of 1"):
        store.finish_watch_run(incomplete, state=RUN_QUIET, observation_count=0)

    failed_retrieval = _start_run(store)
    _finish_eligible_attempt(store, failed_retrieval, ok=False)
    with pytest.raises(StoreError, match="quiet requires"):
        store.finish_watch_run(failed_retrieval, state=RUN_QUIET, observation_count=0)
    with pytest.raises(StoreError, match="complete requires"):
        store.finish_watch_run(failed_retrieval, state="complete", observation_count=1)
    store.finish_watch_run(failed_retrieval, state="partial", observation_count=0)

    successful = _start_run(store)
    _finish_eligible_attempt(store, successful, ok=True)
    with pytest.raises(StoreError, match="partial requires"):
        store.finish_watch_run(successful, state="partial", observation_count=0)
    with pytest.raises(StoreError, match="complete requires"):
        store.finish_watch_run(successful, state="complete", observation_count=0)
    store.finish_watch_run(successful, state=RUN_QUIET, observation_count=0)
    with pytest.raises(StoreError, match="already-terminal"):
        store.finish_watch_run(successful, state=RUN_QUIET, observation_count=0)


def test_duplicate_or_unknown_attempts_and_runs_are_refused(store: SnapshotStore) -> None:
    run_id = _start_run(store)
    store.begin_fetch_attempt(run_id, source_id="eligible", url="https://example.gov/eligible")
    with pytest.raises(StoreError, match="attempt refused"):
        store.begin_fetch_attempt(run_id, source_id="eligible", url="https://example.gov/eligible")
    store.finish_fetch_attempt(
        run_id,
        source_id="eligible",
        ok=True,
        http_status=200,
        content_type="text/html",
        error="",
    )
    with pytest.raises(StoreError, match="already-terminal fetch attempt"):
        store.finish_fetch_attempt(
            run_id,
            source_id="eligible",
            ok=False,
            http_status=503,
            content_type="text/html",
            error="late overwrite",
        )
    with pytest.raises(StoreError, match="unknown or already-terminal fetch attempt"):
        store.finish_fetch_attempt(
            run_id,
            source_id="missing",
            ok=False,
            http_status=None,
            content_type="",
            error="missing",
        )
    with pytest.raises(StoreError, match="unknown watch run"):
        store.watch_run("missing")
    with pytest.raises(StoreError, match="unknown or already-terminal"):
        store.finish_watch_run("missing", state=RUN_FAILED, observation_count=0)


def test_attempts_cannot_mutate_a_terminal_run_or_change_frozen_url(
    store: SnapshotStore,
) -> None:
    identity_locked = _start_run(store)
    with pytest.raises(StoreError, match="identity-mismatched"):
        store.begin_fetch_attempt(
            identity_locked,
            source_id="eligible",
            url="https://attacker.example/wrong",
        )
    store.finish_watch_run(identity_locked, state=RUN_FAILED, observation_count=0)
    with pytest.raises(StoreError, match="terminal"):
        store.begin_fetch_attempt(
            identity_locked,
            source_id="eligible",
            url="https://example.gov/eligible",
        )

    incomplete = _start_run(store)
    store.begin_fetch_attempt(
        incomplete,
        source_id="eligible",
        url="https://example.gov/eligible",
    )
    store.finish_watch_run(incomplete, state=RUN_FAILED, observation_count=0)
    with pytest.raises(StoreError, match="terminal-run"):
        store.finish_fetch_attempt(
            incomplete,
            source_id="eligible",
            ok=True,
            http_status=200,
            content_type="text/html",
            error="",
        )


def test_terminalization_holds_writer_lock_across_count_and_state_update(tmp_path: Path) -> None:
    db = tmp_path / "terminal-race.db"
    with SnapshotStore(db) as seed:
        run_id = _start_run(seed)
        seed.begin_fetch_attempt(
            run_id,
            source_id="eligible",
            url="https://example.gov/eligible",
        )

    before_terminal_update = threading.Event()
    release_terminal_update = threading.Event()
    terminal_errors: list[BaseException] = []
    finisher_errors: list[BaseException] = []

    class PausingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
            if sql.startswith("UPDATE watch_runs SET completed_at"):
                before_terminal_update.set()
                release_terminal_update.wait(timeout=2)
            return self._connection.execute(sql, parameters)  # type: ignore[arg-type]

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    def terminalize() -> None:
        try:
            with SnapshotStore(db) as terminator:
                terminator._conn = PausingConnection(terminator._conn)  # type: ignore[assignment]
                terminator.finish_watch_run(run_id, state="partial")
        except BaseException as exc:  # pragma: no cover - asserted below across a thread
            terminal_errors.append(exc)

    def finish_fetch() -> None:
        try:
            with SnapshotStore(db) as finisher:
                finisher.finish_fetch_attempt(
                    run_id,
                    source_id="eligible",
                    ok=True,
                    http_status=200,
                    content_type="text/html",
                    error="",
                    completed_at=NOW,
                )
        except BaseException as exc:  # expected terminal-run rejection
            finisher_errors.append(exc)

    terminal_thread = threading.Thread(target=terminalize)
    terminal_thread.start()
    assert before_terminal_update.wait(timeout=2)
    finisher_thread = threading.Thread(target=finish_fetch)
    finisher_thread.start()
    time.sleep(0.05)  # give the competing writer time to contend for BEGIN IMMEDIATE
    release_terminal_update.set()
    terminal_thread.join(timeout=2)
    finisher_thread.join(timeout=2)

    assert not terminal_thread.is_alive()
    assert not finisher_thread.is_alive()
    assert terminal_errors == []
    assert len(finisher_errors) == 1
    assert isinstance(finisher_errors[0], StoreError)
    assert "terminal-run" in str(finisher_errors[0])
    with SnapshotStore(db) as store:
        receipt = store.watch_run(run_id)
    assert receipt.state == "partial"
    assert receipt.attempted_source_ids == ("eligible",)
    assert receipt.successful_source_ids == ()


def test_redundant_run_counts_detect_tampering(tmp_path: Path) -> None:
    db = tmp_path / "tampered-run.db"
    with SnapshotStore(db) as store:
        run_id = _start_run(store)
        _finish_eligible_attempt(store, run_id, ok=True)
        store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0)

    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE watch_runs SET attempted_count = 0 WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    with SnapshotStore(db) as store, pytest.raises(StoreError, match="count/set mismatch"):
        store.watch_run(run_id)
