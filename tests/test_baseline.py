"""Tests for :mod:`id_churn_sentinel.core.baseline` — the committed baseline hashes.

Two things are under test. The mechanics (round-trip, validation, drift detection), and one
discipline that matters more than the mechanics: **a source we could not fetch gets no
hash.** A baseline entry is a record of something we observed. Inventing one for a page that
403'd us would be a fabricated observation, and every downstream comparison would inherit it.

The committed `sources/baseline-hashes.json` is also checked for consistency against the
committed registry — offline, with no network, like everything else in this suite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from id_churn_sentinel.core.baseline import (
    BASELINE_VERSION,
    check_baselines,
    default_baseline_path,
    load_baselines,
    write_baselines,
)
from id_churn_sentinel.core.detect import watch
from id_churn_sentinel.core.registry import Registry, Source, load_registry
from id_churn_sentinel.core.store import SnapshotStore
from id_churn_sentinel.errors import RegistryError

from .conftest import StubFetcher

GENERATED = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def registry(source: Source) -> Registry:
    """A one-source registry, shadowing the shared fixture.

    The shared `registry` carries three sources because `publish()` needs one per jurisdiction
    it emits a feed for. These tests are about *one* source's baseline hash, and the assertions
    below ("this source, and nothing else, is unreachable") are sharper with one.
    """
    return Registry(version="1.0", sources=(source,))


def test_write_then_load_round_trips_the_hash(
    tmp_path: Path, registry: Registry, source: Source, store: SnapshotStore, fetcher: StubFetcher
) -> None:
    watch([source], store, fetcher)
    out = tmp_path / "baseline-hashes.json"

    written = write_baselines(store, registry, out, now=GENERATED)

    assert written == 1
    loaded = load_baselines(out)
    assert loaded[source.id] == store.latest_snapshot(source.id).content_sha256  # type: ignore[union-attr]


def test_a_source_we_could_not_fetch_gets_no_hash(
    tmp_path: Path, registry: Registry, source: Source, store: SnapshotStore
) -> None:
    """`ssa.gov` 403s us and always has. It is named as unreachable and carries NO baseline
    — a hash we did not observe is not a hash, and a fabricated one would be laundered into
    fact by every comparison downstream."""
    watch([source], store, StubFetcher())  # every fetch fails
    out = tmp_path / "baseline-hashes.json"

    written = write_baselines(store, registry, out, now=GENERATED)

    assert written == 0
    payload = json.loads(out.read_text())
    assert payload["unreachable"] == [source.id]
    assert payload["baselines"] == {}
    assert load_baselines(out) == {}


def test_check_baselines_detects_a_moved_page(
    source: Source, fixture_before: bytes, fixture_after: bytes
) -> None:
    before = StubFetcher({source.url: (fixture_before, "text/html")})
    committed = {source.id: _hash_of(before, source)}

    report = check_baselines(
        [source], StubFetcher({source.url: (fixture_after, "text/html")}), committed
    )

    assert report.matched == []
    assert len(report.moved) == 1
    moved_id, was, now = report.moved[0]
    assert moved_id == source.id
    assert was != now
    assert "MOVED" in report.summary()


def test_check_baselines_is_quiet_when_the_page_has_not_moved(
    source: Source, fixture_before: bytes, fixture_cosmetic: bytes
) -> None:
    """And cosmetic markup churn is not a move — the baseline is over the NORMALIZED text,
    so a re-minified stylesheet does not wake anyone up here either."""
    unchanged = StubFetcher({source.url: (fixture_before, "text/html")})
    committed = {source.id: _hash_of(unchanged, source)}

    report = check_baselines(
        [source], StubFetcher({source.url: (fixture_cosmetic, "text/html")}), committed
    )

    assert report.matched == [source.id]
    assert report.moved == []


def test_an_unreachable_source_is_never_reported_as_moved(source: Source) -> None:
    """The rule that governs the whole tool, restated here because this is a second code
    path that could have broken it: a fetch failure is never drift."""
    report = check_baselines([source], StubFetcher(), {source.id: "a" * 64})

    assert report.moved == []
    assert report.matched == []
    assert report.unreachable == [(source.id, "stubbed outage: no response configured")]
    assert "not drift" in report.summary()


def test_a_source_with_no_committed_baseline_is_named_not_guessed(
    source: Source, fixture_before: bytes
) -> None:
    report = check_baselines([source], StubFetcher({source.url: (fixture_before, "text/html")}), {})

    assert report.unbaselined == [source.id]
    assert report.moved == []


def test_a_malformed_baseline_file_is_loud(tmp_path: Path) -> None:
    """A silently-wrong baseline would compare a live page against nonsense and report drift
    that never happened. That is worse than having no baseline at all."""
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"baseline_version": "0.9", "baselines": {}}))
    with pytest.raises(RegistryError, match="baseline_version"):
        load_baselines(bad)

    missing_hash = tmp_path / "c.json"
    missing_hash.write_text(
        json.dumps({"baseline_version": BASELINE_VERSION, "baselines": {"x": {"url": "u"}}})
    )
    with pytest.raises(RegistryError, match="no sha256"):
        load_baselines(missing_hash)

    with pytest.raises(RegistryError, match="not found"):
        load_baselines(tmp_path / "nope.json")


def test_the_committed_baseline_matches_the_committed_registry() -> None:
    """Offline, and merge-relevant: every id in the committed baseline must be a real source
    in the committed registry. A baseline for a source that no longer exists is a stale
    claim, and a stale claim in this repo is the failure mode, not a tidiness problem."""
    registry = load_registry()
    baselines = load_baselines(default_baseline_path())
    known = {source.id for source in registry.sources}

    assert baselines, (
        "the committed baseline is empty — run `sentinel watch && sentinel baseline write`"
    )
    orphans = set(baselines) - known
    assert not orphans, f"baseline hashes for sources not in the registry: {sorted(orphans)}"
    assert all(len(h) == 64 for h in baselines.values())


def _hash_of(fetcher: StubFetcher, source: Source) -> str:
    from id_churn_sentinel.core.normalize import content_hash

    result = fetcher.fetch(source.url)
    return content_hash(result.body, result.content_type)[0]
