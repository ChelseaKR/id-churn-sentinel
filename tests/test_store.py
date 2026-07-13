"""Tests for :mod:`id_churn_sentinel.core.store` — snapshots, retention, change round-trip."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.store import SnapshotStore
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
