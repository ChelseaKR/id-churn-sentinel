"""Persisted run health and the public status contract."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from id_churn_sentinel.core.detect import watch_registry
from id_churn_sentinel.core.eligibility import registry_revision
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.status import build_public_status, no_run_status, status_json
from id_churn_sentinel.core.store import (
    RUN_COMPLETE,
    RUN_PARTIAL,
    RUN_QUIET,
    RunSourceInput,
    SnapshotStore,
)

from .conftest import StubFetcher, eligible_source

AS_OF = date(2026, 7, 13)
NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _registry(source: Source) -> Registry:
    return Registry(version="1.0", sources=(eligible_source(source),))


def test_no_run_is_stale_even_when_the_page_was_just_generated() -> None:
    payload = json.loads(status_json(no_run_status(), generated_at=NOW))

    assert payload["generated_at"] == NOW.isoformat()
    assert payload["state"] == "stale"
    assert payload["stale"] is True
    assert payload["last_attempted_run"] is None
    assert payload["last_successful_run"] is None
    assert "Feed silence is not evidence" in payload["message"]


def test_quiet_run_captures_exact_sets_and_is_not_a_no_change_claim(
    tmp_path: Path, source: Source, fixture_before: bytes
) -> None:
    registry = _registry(source)
    with SnapshotStore(tmp_path / "status.db") as store:
        report = watch_registry(
            registry,
            store,
            StubFetcher({source.url: (fixture_before, "text/html")}),
            as_of=AS_OF,
            started_at=NOW,
            completed_at=NOW,
        )
        status = build_public_status(store, now=NOW)

    assert report.state == RUN_QUIET
    payload = json.loads(status_json(status, generated_at=NOW))
    attempted = payload["last_attempted_run"]
    assert payload["state"] == RUN_QUIET
    assert attempted["eligible_source_ids"] == [source.id]
    assert attempted["attempted_source_ids"] == [source.id]
    assert attempted["successful_source_ids"] == [source.id]
    assert attempted["attempt_completeness"] == 1.0
    assert payload["last_successful_run"]["run_id"] == attempted["run_id"]


def test_partial_run_is_latest_but_does_not_replace_last_successful_run(
    tmp_path: Path, source: Source, fixture_before: bytes
) -> None:
    registry = _registry(source)
    with SnapshotStore(tmp_path / "status.db") as store:
        quiet = watch_registry(
            registry,
            store,
            StubFetcher({source.url: (fixture_before, "text/html")}),
            as_of=AS_OF,
            started_at=NOW,
            completed_at=NOW,
        )
        partial = watch_registry(
            registry,
            store,
            StubFetcher({}),
            as_of=AS_OF,
            started_at=NOW + timedelta(days=1),
            completed_at=NOW + timedelta(days=1),
        )
        status = build_public_status(store, now=NOW + timedelta(days=1))

    assert partial.state == RUN_PARTIAL
    assert status.state == RUN_PARTIAL
    assert status.last_attempted is not None
    assert status.last_attempted.run_id == partial.run_id
    assert status.last_successful is not None
    assert status.last_successful.run_id == quiet.run_id


def test_changed_run_is_complete_and_becomes_the_last_successful_run(
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    fixture_after: bytes,
) -> None:
    registry = _registry(source)
    with SnapshotStore(tmp_path / "status.db") as store:
        watch_registry(
            registry,
            store,
            StubFetcher({source.url: (fixture_before, "text/html")}),
            as_of=AS_OF,
            started_at=NOW,
            completed_at=NOW,
        )
        changed = watch_registry(
            registry,
            store,
            StubFetcher({source.url: (fixture_after, "text/html")}),
            as_of=AS_OF,
            started_at=NOW + timedelta(days=1),
            completed_at=NOW + timedelta(days=1),
        )
        status = build_public_status(store, now=NOW + timedelta(days=1))

    assert changed.state == RUN_COMPLETE
    assert status.state == RUN_COMPLETE
    assert status.last_successful is not None
    assert status.last_successful.run_id == changed.run_id
    assert status.last_successful.observation_count == 1


def test_running_receipt_is_publicly_running_and_can_also_be_stale(
    tmp_path: Path, source: Source
) -> None:
    registry = _registry(source)
    with SnapshotStore(tmp_path / "status.db") as store:
        store.start_watch_run(
            as_of=AS_OF,
            registry_version=registry.version,
            registry_revision=registry_revision(registry),
            jurisdiction=None,
            sources=(
                RunSourceInput(
                    source_id=source.id,
                    jurisdiction=source.jurisdiction,
                    document_class=source.document_class,
                    url=source.url,
                    authority=source.authority,
                    eligible=True,
                    eligibility_reasons=(),
                ),
            ),
            started_at=NOW,
        )
        status = build_public_status(store, now=NOW + timedelta(days=9))

    assert status.state == "running"
    assert status.stale is True
    payload = json.loads(status_json(status, generated_at=NOW + timedelta(days=9)))
    assert payload["message"].startswith("A watch run started")
    assert payload["last_attempted_run"]["completed_at"] is None


def test_old_terminal_receipt_becomes_stale_without_changing_its_stored_state(
    tmp_path: Path, source: Source, fixture_before: bytes
) -> None:
    with SnapshotStore(tmp_path / "status.db") as store:
        watch_registry(
            _registry(source),
            store,
            StubFetcher({source.url: (fixture_before, "text/html")}),
            as_of=AS_OF,
            started_at=NOW,
            completed_at=NOW,
        )
        status = build_public_status(store, now=NOW + timedelta(days=9))

    assert status.state == "stale"
    assert status.stale
    assert status.last_attempted is not None
    assert status.last_attempted.state == RUN_QUIET


def test_public_status_never_emits_the_internal_error_string(
    tmp_path: Path, source: Source
) -> None:
    # An unverified source produces a failed zero-denominator receipt containing an internal
    # reason.  The public contract exposes the fact of failure, not arbitrary private text.
    with SnapshotStore(tmp_path / "status.db") as store:
        watch_registry(
            Registry(version="1.0", sources=(source,)),
            store,
            StubFetcher({}),
            as_of=AS_OF,
            started_at=NOW,
            completed_at=NOW,
        )
        status = build_public_status(store, now=NOW)

    rendered = status_json(status, generated_at=NOW)
    assert "no attempt-eligible sources in scope" not in rendered
    payload = json.loads(rendered)
    assert payload["state"] == "failed"
    assert payload["last_attempted_run"]["has_internal_error"] is True


def test_publish_always_writes_status_and_the_site_does_not_conflate_generation_with_watch(
    tmp_path: Path, source: Source, fixture_before: bytes
) -> None:
    registry = _registry(source)
    with SnapshotStore(tmp_path / "status.db") as store:
        watch_registry(
            registry,
            store,
            StubFetcher({source.url: (fixture_before, "text/html")}),
            as_of=AS_OF,
            started_at=NOW,
            completed_at=NOW,
        )
        status = build_public_status(store, now=NOW)

    out = tmp_path / "published"
    result = publish([], out, registry=registry, now=NOW, run_status=status)

    assert result.status_path == out / "status.json"
    assert result.status_path.exists()
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Page generation is not watch success" in page
    assert "Run health: QUIET · CURRENT" in page
    assert 'href="status.json"' in page


def test_nonpositive_stale_interval_and_naive_generation_time_are_handled(
    tmp_path: Path,
) -> None:
    with (
        SnapshotStore(tmp_path / "status.db") as store,
        pytest.raises(ValueError, match="positive"),
    ):
        build_public_status(store, stale_after=timedelta(0))

    with pytest.raises(ValueError, match="positive"):
        no_run_status(stale_after=timedelta(seconds=-1))

    payload = json.loads(status_json(no_run_status(), generated_at=datetime(2026, 7, 13, 12, 0)))
    assert payload["generated_at"].endswith("+00:00")

    with SnapshotStore(tmp_path / "empty.db") as empty:
        assert build_public_status(empty, now=NOW).state == "stale"
