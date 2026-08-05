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
    BaselineEntry,
    check_baselines,
    default_baseline_path,
    load_baselines,
    write_baselines,
)
from id_churn_sentinel.core.detect import _watch_authorized_sources as watch
from id_churn_sentinel.core.normalize import CURRENT_CONTRACT, UNRECORDED_CONTRACT
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
    snapshot = store.latest_snapshot(source.id)
    assert snapshot is not None
    assert loaded[source.id].sha256 == snapshot.content_sha256
    # The hash never travels without the normalizer that produced it: an unlabelled hash is
    # one a future build cannot tell is comparable.
    assert loaded[source.id].contract == CURRENT_CONTRACT


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
    committed = {source.id: BaselineEntry(_hash_of(before, source), CURRENT_CONTRACT)}

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
    committed = {source.id: BaselineEntry(_hash_of(unchanged, source), CURRENT_CONTRACT)}

    report = check_baselines(
        [source], StubFetcher({source.url: (fixture_cosmetic, "text/html")}), committed
    )

    assert report.matched == [source.id]
    assert report.moved == []


def test_an_unreachable_source_is_never_reported_as_moved(source: Source) -> None:
    """The rule that governs the whole tool, restated here because this is a second code
    path that could have broken it: a fetch failure is never drift."""
    report = check_baselines([source], StubFetcher(), {source.id: BaselineEntry("a" * 64)})

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
    assert all(len(entry.sha256) == 64 for entry in baselines.values())


def _hash_of(fetcher: StubFetcher, source: Source) -> str:
    from id_churn_sentinel.core.normalize import content_hash

    result = fetcher.fetch(source.url)
    return content_hash(result.body, result.content_type)[0]


# -- the representation contract ---------------------------------------------------
#
# `sentinel watch` re-derives an old baseline from retained bytes and settles the question.
# This file holds hashes and no bytes, so it cannot — and these tests pin the different
# answer that follows from that: label the comparison, never withhold it, and never let an
# unqualified MOVED line stand for a hash our own normalizer moved.


def test_a_hash_recorded_by_a_different_normalizer_is_flagged_not_presented_as_drift(
    source: Source, fixture_before: bytes
) -> None:
    """The live case: `sources/baseline-hashes.json` was written on 2026-07-13 under v1, and
    a clean checkout now runs v2 against it. The MOVED line is still emitted — refusing it
    would leave a fresh clone unable to say anything, which is the hole this file fills —
    but it is emitted as a comparison that may be measuring us."""
    committed = {source.id: BaselineEntry("a" * 64, "passage-text-v1/none-v1")}

    report = check_baselines(
        [source], StubFetcher({source.url: (fixture_before, "text/html")}), committed
    )

    assert [entry[0] for entry in report.moved] == [source.id]
    assert report.moved_across_contracts == {source.id: "passage-text-v1/none-v1"}
    assert "may be a normalization artifact, not drift" in report.summary()


def test_a_hash_with_no_recorded_normalizer_is_flagged_rather_than_assumed_to_be_v1(
    source: Source, fixture_before: bytes
) -> None:
    """An entry written before the field existed loads as `unrecorded`, and `unrecorded` is
    treated as not-comparable. Guessing "it was probably v1" would be convenient and would
    be a claim about provenance we do not have."""
    committed = {source.id: BaselineEntry("a" * 64)}

    report = check_baselines(
        [source], StubFetcher({source.url: (fixture_before, "text/html")}), committed
    )

    assert report.moved_across_contracts == {source.id: UNRECORDED_CONTRACT}


def test_a_same_contract_move_carries_no_caveat(
    source: Source, fixture_before: bytes, fixture_after: bytes
) -> None:
    """The qualifier has to be *conditional* to mean anything. A caveat printed on every
    MOVED line is a caveat nobody reads by the third run."""
    before = StubFetcher({source.url: (fixture_before, "text/html")})
    committed = {source.id: BaselineEntry(_hash_of(before, source), CURRENT_CONTRACT)}

    report = check_baselines(
        [source], StubFetcher({source.url: (fixture_after, "text/html")}), committed
    )

    assert len(report.moved) == 1
    assert report.moved_across_contracts == {}
    assert "normalization artifact" not in report.summary()


def test_a_hash_that_matches_across_contracts_needs_no_caveat(
    source: Source, fixture_before: bytes
) -> None:
    """Two different normalizers producing the same digest produced the same text, so a
    match is a match. Qualifying it would manufacture doubt rather than report it."""
    live = StubFetcher({source.url: (fixture_before, "text/html")})
    committed = {source.id: BaselineEntry(_hash_of(live, source), "passage-text-v1/none-v1")}

    report = check_baselines(
        [source], StubFetcher({source.url: (fixture_before, "text/html")}), committed
    )

    assert report.matched == [source.id]
    assert report.moved_across_contracts == {}


def test_write_baselines_stamps_the_snapshots_contract_not_this_builds(
    tmp_path: Path, registry: Registry, source: Source, store: SnapshotStore
) -> None:
    """A store can hold a baseline recorded under an older normalizer. Stamping today's
    version onto yesterday's hash would fabricate the exact provenance this field exists to
    make checkable."""
    store.record_snapshot(
        source_id=source.id,
        url=source.url,
        fetched_at=GENERATED,
        http_status=200,
        content_sha256="b" * 64,
        raw_bytes=b"<p>x</p>",
        normalized_text="x",
        normalizer_version="passage-text-v1",
        extractor_version="none-v1",
    )
    out = tmp_path / "baseline-hashes.json"

    write_baselines(store, registry, out, now=GENERATED)

    assert load_baselines(out)[source.id].contract == "passage-text-v1/none-v1"
