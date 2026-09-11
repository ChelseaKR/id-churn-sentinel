"""Registry overlays (#77): an organization's own sources, held to this repository's discipline.

Grouped by what the issue asks to be true. First its three "Done when" criteria, each by name;
then the two design notes recorded on the issue before anyone built it — the store key is
``(overlay_id, source_id)`` with ``''`` for the committed registry, and ``coverage --check-docs``
is proved overlay-blind by comparing bytes rather than counts; then the refusals an overlay
depends on; then migration 12, measured row by row.

Every write-guard test here points the destructive argument at a temporary copy, never at a
tracked file, so a regression in a guard cannot damage the tree it is being tested in.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel import cli
from id_churn_sentinel.core import coverage as coverage_module
from id_churn_sentinel.core import store as store_module
from id_churn_sentinel.core.changes import (
    DEFAULT_PUBLIC_COPY,
    ChangeRecord,
    ReviewStatus,
    Significance,
    change_id,
)
from id_churn_sentinel.core.coverage import repo_root
from id_churn_sentinel.core.detect import watch_registry
from id_churn_sentinel.core.eligibility import evaluate_source, registry_revision
from id_churn_sentinel.core.jurisdiction_status import build_jurisdiction_status
from id_churn_sentinel.core.normalize import EXTRACTOR_VERSION, NORMALIZER_VERSION
from id_churn_sentinel.core.overlay import (
    RESERVED_OVERLAY_IDS,
    Overlay,
    load_overlay,
    load_overlays,
)
from id_churn_sentinel.core.publish import (
    default_public_site,
    publish,
    publish_overlay,
)
from id_churn_sentinel.core.registry import (
    DOCUMENT_CLASSES,
    FETCH_POLICY_OUTCOMES,
    GAP_REASONS,
    JURISDICTIONS,
    REGISTRY_VERSION,
    VERIFICATION_STATUSES,
    VERIFIED,
    Registry,
    Source,
    load_registry,
)
from id_churn_sentinel.core.site import PAGES_URL, REPO_URL, feed_slug
from id_churn_sentinel.core.status import build_public_status, status_json
from id_churn_sentinel.core.store import RunSourceInput, SnapshotStore
from id_churn_sentinel.errors import PublishError, RegistryError, StoreError

from .conftest import StubFetcher, eligible_source, eligible_source_entry
from .test_schema import _validate

# By import path, not `from id_churn_sentinel.core import publish`: the package re-binds that
# name to the `publish` function, and a monkeypatch aimed at the function would raise before the
# command it is meant to fence ever runs.
publish_module = importlib.import_module("id_churn_sentinel.core.publish")

AS_OF = date(2026, 9, 11)
PINNED = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
OVERLAY_URL = "https://clerk.example-county.gov/name-change"
OVERLAY_FEED_URL = "https://legal-aid.example.org/feeds/"
HTML = "text/html; charset=utf-8"

COMMITTED = Source(
    id="tx-dps-change-dl-id",
    jurisdiction="TX",
    document_class="drivers_license",
    url="https://www.dps.texas.gov/section/driver-license/change",
    authority="Texas Department of Public Safety",
    verified=False,
    notes="test fixture",
)


def _entry(
    source_id: str,
    url: str,
    *,
    eligible: bool = True,
    jurisdiction: str = "TX",
    document_class: str = "court_order_name_change",
) -> dict[str, object]:
    source = Source(
        id=source_id,
        jurisdiction=jurisdiction,
        document_class=document_class,
        url=url,
        authority="Example County District Clerk",
        verified=False,
        notes="overlay test fixture",
    )
    if eligible:
        return eligible_source_entry(source)
    return {
        "id": source.id,
        "jurisdiction": source.jurisdiction,
        "document_class": source.document_class,
        "url": source.url,
        "authority": source.authority,
        "notes": source.notes,
    }


def _write_overlay(
    directory: Path,
    overlay_id: object,
    entries: list[dict[str, object]],
    *,
    name: str | None = None,
) -> Path:
    document: dict[str, object] = {"registry_version": "1.0", "sources": entries, "gaps": []}
    if overlay_id is not None:
        document["overlay_id"] = overlay_id
    path = directory / (name or f"{overlay_id}.json")
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _write_registry(directory: Path, *sources: Source) -> Path:
    path = directory / "registry.json"
    document = {
        "registry_version": "1.0",
        "sources": [eligible_source_entry(source) for source in sources],
        "gaps": [],
    }
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _committed() -> Registry:
    return Registry(version="1.0", sources=(eligible_source(COMMITTED),))


def _overlay(
    tmp_path: Path,
    overlay_id: str = "county-x",
    source_id: str = "clerk-page",
    url: str = OVERLAY_URL,
) -> Overlay:
    return load_overlay(_write_overlay(tmp_path, overlay_id, [_entry(source_id, url)]))


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _confirm(store: SnapshotStore, change: ChangeRecord) -> ChangeRecord:
    reviewed = store.get_change(change.id).reviewed_by(
        reviewer="Chelsea Kelly-Reif",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.CONFIRMED,
        public_copy=DEFAULT_PUBLIC_COPY,
    )
    store.update_change(reviewed)
    return store.get_change(change.id)


# ---------------------------------------------------------------------------------------
# Done when (1): one run id, overlay_id on each overlay observation
# ---------------------------------------------------------------------------------------


def test_a_watch_with_an_overlay_attempts_both_under_one_run_id_and_tags_each_overlay_row(
    tmp_path: Path, store: SnapshotStore, fixture_before: bytes, fixture_after: bytes
) -> None:
    committed = _committed()
    overlay = _overlay(tmp_path)
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )
    watch_registry(committed, store, fetcher, as_of=AS_OF, overlays=(overlay,))
    fetcher.set(COMMITTED.url, fixture_after)
    fetcher.set(OVERLAY_URL, fixture_after)

    report = watch_registry(committed, store, fetcher, as_of=AS_OF, overlays=(overlay,))

    run = store.watch_run(report.run_id)
    assert run.attempted_source_ids == (COMMITTED.id, "county-x/clerk-page")
    rows = store._conn.execute(
        "SELECT overlay_id, source_id FROM run_sources WHERE run_id = ? ORDER BY 1, 2",
        (report.run_id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [("", COMMITTED.id), ("county-x", "clerk-page")]
    attempts = {(a.overlay_id, a.source_id) for a in store.fetch_attempts(report.run_id)}
    assert attempts == {("", COMMITTED.id), ("county-x", "clerk-page")}

    by_namespace = {change.overlay_id: change for change in report.changed}
    assert set(by_namespace) == {"", "county-x"}
    observed = store._conn.execute(
        "SELECT change.overlay_id, change.source_id FROM run_observations AS observation "
        "JOIN changes AS change ON change.change_id = observation.change_id "
        "WHERE observation.run_id = ? ORDER BY 1",
        (report.run_id,),
    ).fetchall()
    assert [tuple(row) for row in observed] == [("", COMMITTED.id), ("county-x", "clerk-page")]
    assert store.run_overlays(report.run_id) == (("county-x", registry_revision(overlay.registry)),)
    overlay_change = by_namespace["county-x"]
    assert overlay_change.id != change_id(
        "clerk-page", overlay_change.previous_hash, overlay_change.new_hash
    ), "an overlay observation's id must carry its namespace"


# ---------------------------------------------------------------------------------------
# Done when (2): a repeated committed URL fails to load with both ids named
# ---------------------------------------------------------------------------------------


def test_an_overlay_repeating_a_committed_url_fails_to_load_naming_both_ids(tmp_path: Path) -> None:
    committed = load_registry()
    taken = committed.sources[0]
    path = _write_overlay(tmp_path, "county-x", [_entry("our-copy", taken.url)])

    with pytest.raises(RegistryError) as refused:
        load_overlays([path], committed)

    assert "county-x/our-copy" in str(refused.value)
    assert repr(taken.id) in str(refused.value)


def test_the_cli_refuses_the_same_collision_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    taken = load_registry().sources[0]
    path = _write_overlay(tmp_path, "county-x", [_entry("our-copy", taken.url)])

    assert cli.main(["sources", "validate", "--overlay", str(path)]) == 1

    err = capsys.readouterr().err
    assert "county-x/our-copy" in err and taken.id in err


def test_a_url_collision_is_judged_under_the_crosswalk_normalizer(tmp_path: Path) -> None:
    shouted = COMMITTED.url.replace("https://www.dps.texas.gov", "HTTPS://WWW.DPS.TEXAS.GOV")
    path = _write_overlay(tmp_path, "county-x", [_entry("shouted", shouted)])

    with pytest.raises(RegistryError, match="county-x/shouted"):
        load_overlays([path], _committed())


def test_two_overlays_listing_one_url_are_refused_naming_both(tmp_path: Path) -> None:
    first = _write_overlay(tmp_path, "county-a", [_entry("clerk", OVERLAY_URL)])
    second = _write_overlay(tmp_path, "county-b", [_entry("court", OVERLAY_URL)])

    with pytest.raises(RegistryError) as refused:
        load_overlays([first, second], _committed())

    assert "county-b/court" in str(refused.value) and "county-a/clerk" in str(refused.value)


def test_two_overlay_files_declaring_one_overlay_id_are_refused(tmp_path: Path) -> None:
    first = _write_overlay(tmp_path, "county-x", [_entry("a", OVERLAY_URL)], name="one.json")
    second = _write_overlay(
        tmp_path, "county-x", [_entry("b", OVERLAY_URL + "/other")], name="two.json"
    )

    with pytest.raises(RegistryError, match="declared by both"):
        load_overlays([first, second], _committed())


def test_one_overlay_may_list_a_url_under_two_document_classes_as_the_registry_may(
    tmp_path: Path,
) -> None:
    """The cross-namespace rule is between namespaces. Inside one, the committed validator's
    own rule applies unchanged — a unique jurisdiction/document-class/URL triple."""
    path = _write_overlay(
        tmp_path,
        "county-x",
        [
            _entry("court-page", OVERLAY_URL),
            _entry("dl-page", OVERLAY_URL, document_class="drivers_license"),
        ],
    )

    (overlay,) = load_overlays([path], _committed())

    assert [source.key for source in overlay.sources] == ["county-x/court-page", "county-x/dl-page"]


# ---------------------------------------------------------------------------------------
# Done when (3a): publish --out docs/ --overlay exits non-zero and writes nothing
# ---------------------------------------------------------------------------------------


def test_publish_to_docs_with_an_overlay_exits_non_zero_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay_path = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL)])
    docs = repo_root() / "docs"
    before = _tree_digest(docs)

    def refuse_to_write(*_: object) -> None:
        raise AssertionError("the refusal let a write through")

    # Belt and braces for the one test that names the real `docs/`: if the refusal ever
    # regresses, the writer raises instead of writing, so the tree cannot be damaged by the
    # test that is supposed to protect it.
    monkeypatch.setattr(publish_module, "_write_overlay_artifacts", refuse_to_write)
    monkeypatch.chdir(repo_root())
    code = cli.main(
        [
            "--db",
            str(tmp_path / "store.db"),
            "publish",
            "--out",
            "docs/",
            "--overlay",
            str(overlay_path),
            "--feed-url",
            OVERLAY_FEED_URL,
        ]
    )

    assert code == 1
    assert "committed public site" in capsys.readouterr().err
    assert _tree_digest(docs) == before
    assert not (tmp_path / "store.db").exists(), "the refusal comes before the store is opened"


def test_the_protected_directory_is_the_committed_site() -> None:
    """The guard is only as good as the directory it protects — prove it is the real one."""
    site = default_public_site()

    assert site == repo_root() / "docs"
    assert all((site / name).is_file() for name in ("index.html", "sources.json", "status.json"))


def test_publish_overlay_refuses_the_public_site_by_every_spelling_and_writes_nothing(
    tmp_path: Path,
) -> None:
    # No marker file in `site`: only the path rule may answer here. The marker rule has its own
    # test below, and a fixture both could refuse would prove neither.
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<p>public</p>", encoding="utf-8")
    (tmp_path / "link").symlink_to(site, target_is_directory=True)
    overlay = _overlay(tmp_path)
    before = _tree_digest(tmp_path)

    for destination in (site, site / "nested", tmp_path / "link", site / ".." / "site"):
        with pytest.raises(PublishError, match="committed public site"):
            publish_overlay(
                (),
                destination,
                overlay=overlay.registry,
                feed_url=OVERLAY_FEED_URL,
                public_site=site,
                now=PINNED,
            )

    assert _tree_digest(tmp_path) == before
    assert not (site / "nested").exists()


def test_publish_overlay_refuses_a_directory_holding_a_committed_publication(
    tmp_path: Path,
) -> None:
    copy = tmp_path / "gh-pages-checkout"
    copy.mkdir()
    (copy / "sources.json").write_text("{}", encoding="utf-8")
    before = _tree_digest(copy)

    with pytest.raises(PublishError, match=r"sources\.json"):
        publish_overlay(
            (),
            copy,
            overlay=_overlay(tmp_path).registry,
            feed_url=OVERLAY_FEED_URL,
            public_site=tmp_path / "elsewhere",
            now=PINNED,
        )

    assert _tree_digest(copy) == before


@pytest.mark.parametrize("feed_url", [REPO_URL, f"{PAGES_URL}county/", "", "   "])
def test_publish_overlay_refuses_this_projects_own_home_or_no_home(
    tmp_path: Path, feed_url: str
) -> None:
    with pytest.raises(PublishError):
        publish_overlay(
            (),
            tmp_path / "out",
            overlay=_overlay(tmp_path).registry,
            feed_url=feed_url,
            public_site=tmp_path / "site",
            now=PINNED,
        )
    assert not (tmp_path / "out").exists()


def test_publish_overlay_cli_needs_a_feed_url_of_its_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay_path = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL)])

    code = cli.main(
        [
            "--db",
            str(tmp_path / "s.db"),
            "publish",
            "--out",
            str(tmp_path / "out"),
            "--overlay",
            str(overlay_path),
        ]
    )

    assert code == 1
    assert "--feed-url" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_publish_overlay_writes_two_files_in_the_closed_changes_v2_shape(
    tmp_path: Path, store: SnapshotStore, fixture_before: bytes, fixture_after: bytes
) -> None:
    overlay = _overlay(tmp_path)
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )
    watch_registry(_committed(), store, fetcher, as_of=AS_OF, overlays=(overlay,))
    fetcher.set(OVERLAY_URL, fixture_after)
    report = watch_registry(_committed(), store, fetcher, as_of=AS_OF, overlays=(overlay,))
    (change,) = report.changed
    _confirm(store, change)
    out = tmp_path / "feeds"

    result = publish_overlay(
        store.changes(review_status=ReviewStatus.CONFIRMED, overlay_id="county-x"),
        out,
        overlay=overlay.registry,
        feed_url=OVERLAY_FEED_URL,
        public_site=tmp_path / "site",
    )

    assert result.published == 1
    assert sorted(path.name for path in out.iterdir()) == [
        "changes-county-x.json",
        "feed-county-x.xml",
    ]
    document = json.loads((out / "changes-county-x.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (repo_root() / "docs" / "schema" / "changes-v2.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["required"]) <= set(document) <= set(schema["properties"])
    assert [item["source_id"] for item in document["changes"]] == ["clerk-page"]
    assert document["registry_verification"]["scope"] == "overlay county-x"
    feed = (out / "feed-county-x.xml").read_text(encoding="utf-8")
    assert "overlay county-x" in feed and "NOT the public id-churn-sentinel feed" in feed


# ---------------------------------------------------------------------------------------
# Done when (3b), and the second design note: --check-docs is overlay-blind, byte for byte
# ---------------------------------------------------------------------------------------


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_coverage_check_docs_is_byte_identical_with_and_without_any_overlay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    valid = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL)])
    unreadable = tmp_path / "not-json.json"
    unreadable.write_text("{ this is not json", encoding="utf-8")
    colliding = _write_overlay(
        tmp_path, "county-y", [_entry("dup", load_registry().sources[0].url)]
    )

    baseline = _run(capsys, ["coverage", "--check-docs"])
    assert baseline[0] == 0, "compare two passing gates, not two failures"
    for overlay_path in (valid, unreadable, colliding, tmp_path / "absent.json"):
        assert (
            _run(capsys, ["coverage", "--check-docs", "--overlay", str(overlay_path)]) == baseline
        )


def test_coverage_reports_an_overlay_apart_and_never_in_the_gated_grammar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay_path = _write_overlay(
        tmp_path,
        "county-x",
        [_entry("clerk-page", OVERLAY_URL), _entry("fees", OVERLAY_URL + "/fees", eligible=False)],
    )
    plain = _run(capsys, ["coverage"])[1]
    with_overlay = _run(capsys, ["coverage", "--overlay", str(overlay_path)])[1]

    assert with_overlay.startswith(plain), "the committed figures are untouched and come first"
    extra = with_overlay[len(plain) :]
    assert "overlay county-x" in extra and "entries:          2" in extra
    gated = (
        coverage_module._SOURCES_RE,
        coverage_module._GAPS_RE,
        coverage_module._VERIFIED_RE,
        coverage_module._UNREACHABLE_RE,
        coverage_module._BASELINE_HASHES_RE,
        coverage_module._jurisdictions_re(len(JURISDICTIONS)),
    )
    assert not [
        (line, pattern.pattern)
        for line in extra.splitlines()
        for pattern in gated
        if pattern.search(line)
    ]

    plain_json = json.loads(_run(capsys, ["coverage", "--json"])[1])
    overlay_json = json.loads(
        _run(capsys, ["coverage", "--json", "--overlay", str(overlay_path)])[1]
    )
    assert overlay_json.pop("overlays") == [
        {
            "overlay_id": "county-x",
            "entries": 2,
            "jurisdictions": 1,
            "gaps_recorded": 0,
            "human_verified": 1,
            "attempt_eligible": 1,
        }
    ]
    assert overlay_json == plain_json


# ---------------------------------------------------------------------------------------
# The first design note: (overlay_id, source_id), so a shared name shares no row
# ---------------------------------------------------------------------------------------


def test_an_overlay_entry_sharing_a_committed_id_shares_no_store_row(
    tmp_path: Path, store: SnapshotStore, fixture_before: bytes, fixture_after: bytes
) -> None:
    overlay = _overlay(tmp_path, source_id=COMMITTED.id)
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )
    watch_registry(_committed(), store, fetcher, as_of=AS_OF, overlays=(overlay,))
    committed_baseline = store.latest_snapshot(COMMITTED.id)
    fetcher.set(OVERLAY_URL, fixture_after)

    report = watch_registry(_committed(), store, fetcher, as_of=AS_OF, overlays=(overlay,))

    (change,) = report.changed
    assert (change.overlay_id, change.url) == ("county-x", OVERLAY_URL)
    assert report.unchanged == [COMMITTED.id]
    committed_now = store.latest_snapshot(COMMITTED.id)
    overlay_now = store.latest_snapshot(COMMITTED.id, overlay_id="county-x")
    assert committed_baseline is not None and committed_now is not None and overlay_now is not None
    assert (committed_now.url, committed_now.content_sha256) == (
        COMMITTED.url,
        committed_baseline.content_sha256,
    )
    assert overlay_now.url == OVERLAY_URL
    assert len(store.snapshots(COMMITTED.id)) == 2
    assert len(store.snapshots(COMMITTED.id, overlay_id="county-x")) == 2

    del fetcher.responses[OVERLAY_URL]
    watch_registry(_committed(), store, fetcher, as_of=AS_OF, overlays=(overlay,))
    assert store.failure_streak(COMMITTED.id) == 0
    assert store.failure_streak(COMMITTED.id, overlay_id="county-x") == 1


def test_two_overlays_sharing_a_source_id_and_the_same_bytes_keep_two_observations(
    tmp_path: Path, store: SnapshotStore, fixture_before: bytes, fixture_after: bytes
) -> None:
    """Without the namespace in the change id, the second observation would be the same id and
    `ON CONFLICT (change_id) DO NOTHING` would drop it without a word."""
    first = _overlay(tmp_path, "county-a", "clerk-page", OVERLAY_URL)
    second = _overlay(tmp_path, "county-b", "clerk-page", OVERLAY_URL + "-b")
    fetcher = StubFetcher(
        {
            COMMITTED.url: (fixture_before, HTML),
            OVERLAY_URL: (fixture_before, HTML),
            OVERLAY_URL + "-b": (fixture_before, HTML),
        }
    )
    watch_registry(_committed(), store, fetcher, as_of=AS_OF, overlays=(first, second))
    fetcher.set(OVERLAY_URL, fixture_after)
    fetcher.set(OVERLAY_URL + "-b", fixture_after)

    report = watch_registry(_committed(), store, fetcher, as_of=AS_OF, overlays=(first, second))

    assert sorted(change.source_key for change in report.changed) == [
        "county-a/clerk-page",
        "county-b/clerk-page",
    ]
    stored = store.changes(overlay_id=None)
    assert len({change.id for change in stored}) == 2


# ---------------------------------------------------------------------------------------
# What an overlay can never do: buy eligibility, reach the public artifact, move a status
# ---------------------------------------------------------------------------------------


def test_an_overlay_cannot_carry_a_verified_true_nobody_signed(tmp_path: Path) -> None:
    unsigned = {**_entry("clerk-page", OVERLAY_URL, eligible=False), "verified": True}
    path = _write_overlay(tmp_path, "county-x", [unsigned])

    with pytest.raises(RegistryError, match="no `verification` block"):
        load_overlay(path)


def test_an_overlay_cannot_buy_eligibility(tmp_path: Path) -> None:
    path = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL, eligible=False)])
    (entry,) = load_overlay(path).sources
    twin = Source(
        id=entry.id,
        jurisdiction=entry.jurisdiction,
        document_class=entry.document_class,
        url=entry.url,
        authority=entry.authority,
        verified=False,
        notes=entry.notes,
    )

    overlay_decision = evaluate_source(entry, as_of=AS_OF)
    committed_decision = evaluate_source(twin, as_of=AS_OF)

    assert overlay_decision.eligible is False
    assert (
        overlay_decision.reasons
        == committed_decision.reasons
        == (
            "unverified",
            "fetch-policy-unreviewed",
        )
    )
    assert overlay_decision.source_id == "county-x/clerk-page"


@pytest.mark.parametrize("overlay_id", ["", "County X", "a/b", "-lead", "us", "us-tx", 7, None])
def test_an_overlay_id_is_a_slug_that_is_not_a_published_feed_name(
    tmp_path: Path, overlay_id: object
) -> None:
    path = _write_overlay(tmp_path, overlay_id, [_entry("clerk-page", OVERLAY_URL)], name="o.json")

    with pytest.raises(RegistryError, match="overlay_id"):
        load_overlay(path)


def test_the_reserved_overlay_ids_are_exactly_the_published_feed_slugs() -> None:
    assert {feed_slug(jurisdiction) for jurisdiction in JURISDICTIONS} == RESERVED_OVERLAY_IDS


def test_the_committed_loader_refuses_an_overlay_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL)])

    with pytest.raises(RegistryError, match="registry OVERLAY"):
        load_registry(path)
    assert cli.main(["--registry", str(path), "publish", "--out", str(tmp_path / "out")]) == 1
    assert "registry OVERLAY" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_every_artifact_refuses_records_from_another_namespace(tmp_path: Path) -> None:
    overlay = _overlay(tmp_path)
    committed = _committed()
    overlay_record = ChangeRecord.observed(
        source_id="clerk-page",
        overlay_id="county-x",
        jurisdiction="TX",
        document_class="court_order_name_change",
        url=OVERLAY_URL,
        previous_hash="a" * 64,
        new_hash="b" * 64,
        diff_excerpt="-old passage\n+new passage",
    )
    committed_record = ChangeRecord.observed(
        source_id=COMMITTED.id,
        jurisdiction=COMMITTED.jurisdiction,
        document_class=COMMITTED.document_class,
        url=COMMITTED.url,
        previous_hash="a" * 64,
        new_hash="b" * 64,
        diff_excerpt="-old passage\n+new passage",
    )

    with pytest.raises(PublishError, match="another namespace"):
        publish([overlay_record], tmp_path / "public", registry=committed, now=PINNED)
    with pytest.raises(PublishError, match="another namespace"):
        publish_overlay(
            [committed_record],
            tmp_path / "feeds",
            overlay=overlay.registry,
            feed_url=OVERLAY_FEED_URL,
            public_site=tmp_path / "site",
            now=PINNED,
        )
    with pytest.raises(PublishError, match="overlay registry"):
        publish([], tmp_path / "public", registry=overlay.registry, now=PINNED)
    assert not (tmp_path / "public").exists() and not (tmp_path / "feeds").exists()


def _publish_public(store: SnapshotStore, registry: Registry, out: Path) -> None:
    publish(
        store.changes(review_status=ReviewStatus.CONFIRMED),
        out,
        registry=registry,
        feed_url=REPO_URL,
        now=PINNED,
        run_status=build_public_status(store, now=PINNED),
        jurisdiction_status={
            jurisdiction: build_jurisdiction_status(store, jurisdiction, registry=registry)
            for jurisdiction in sorted(registry.jurisdictions)
        },
    )


def test_the_public_site_is_byte_identical_whatever_overlay_rows_the_store_holds(
    tmp_path: Path, store: SnapshotStore, fixture_before: bytes, fixture_after: bytes
) -> None:
    committed = _committed()
    fetcher = StubFetcher({COMMITTED.url: (fixture_before, HTML)})
    watch_registry(committed, store, fetcher, as_of=AS_OF)
    fetcher.set(COMMITTED.url, fixture_after)
    (committed_change,) = watch_registry(committed, store, fetcher, as_of=AS_OF).changed
    _confirm(store, committed_change)
    _publish_public(store, committed, tmp_path / "before")

    overlay = _overlay(tmp_path)
    fetcher.set(OVERLAY_URL, fixture_before)
    watch_registry(committed, store, fetcher, as_of=AS_OF, overlays=(overlay,))
    fetcher.set(OVERLAY_URL, fixture_after)
    later = watch_registry(committed, store, fetcher, as_of=AS_OF, overlays=(overlay,))
    (overlay_change,) = later.changed
    assert _confirm(store, overlay_change).publishable
    _publish_public(store, committed, tmp_path / "after")

    before = {p.name: p.read_bytes() for p in (tmp_path / "before").iterdir()}
    after = {p.name: p.read_bytes() for p in (tmp_path / "after").iterdir()}
    assert len(before) > 3
    assert after == before


def test_a_run_that_carried_an_overlay_never_becomes_the_public_status(
    tmp_path: Path, store: SnapshotStore, fixture_before: bytes
) -> None:
    committed = _committed()
    overlay = _overlay(tmp_path)
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )

    only_overlay_run = watch_registry(committed, store, fetcher, as_of=AS_OF, overlays=(overlay,))
    assert store.latest_watch_run(aggregate_only=True) is None
    assert build_public_status(store, now=PINNED).last_attempted is None
    assert build_jurisdiction_status(store, "TX", registry=committed).run is None

    committed_run = watch_registry(committed, store, fetcher, as_of=AS_OF)
    watch_registry(committed, store, fetcher, as_of=AS_OF, overlays=(overlay,))

    status = build_public_status(store, now=PINNED)
    assert status.last_attempted is not None
    assert status.last_attempted.run_id == committed_run.run_id
    rendered = status_json(status, generated_at=PINNED)
    assert only_overlay_run.run_id not in rendered
    receipt = build_jurisdiction_status(store, "TX", registry=committed)
    assert receipt.run is not None and receipt.run.run_id == committed_run.run_id
    latest_any = store.latest_watch_run(include_overlay_runs=True)
    assert latest_any is not None and latest_any.run_id != committed_run.run_id


# ---------------------------------------------------------------------------------------
# The other verbs: decisions land in the overlay; baselines beside it; runs say what they are
# ---------------------------------------------------------------------------------------


def _registry_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "registry-copy.json"
    copy.write_bytes((repo_root() / "sources" / "registry.json").read_bytes())
    return copy


def test_verify_with_an_overlay_writes_into_the_overlay_and_never_the_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_copy = _registry_copy(tmp_path)
    before = registry_copy.read_bytes()
    overlay_path = _write_overlay(
        tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL, eligible=False)]
    )

    assert (
        cli.main(
            ["--registry", str(registry_copy), "verify", "--overlay", str(overlay_path), "--list"]
        )
        == 0
    )
    assert "clerk-page" in capsys.readouterr().out
    code = cli.main(
        [
            "--registry",
            str(registry_copy),
            "verify",
            "--overlay",
            str(overlay_path),
            "--source-id",
            "clerk-page",
            "--confirm",
            "--verifier",
            "Chelsea Kelly-Reif",
            "--evidence",
            "tests/evidence/synthetic-source-review.json",
        ]
    )

    assert code == 0
    assert registry_copy.read_bytes() == before
    verification = load_overlay(overlay_path).registry.by_id("clerk-page").verification
    assert (verification.status, verification.verifier) == (VERIFIED, "Chelsea Kelly-Reif")


def test_sources_policy_with_an_overlay_writes_into_the_overlay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_copy = _registry_copy(tmp_path)
    before = registry_copy.read_bytes()
    overlay_path = _write_overlay(
        tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL, eligible=False)]
    )

    code = cli.main(
        [
            "--registry",
            str(registry_copy),
            "sources",
            "policy",
            "--overlay",
            str(overlay_path),
            "--source-id",
            "clerk-page",
            "--outcome",
            "allow",
            "--reviewer",
            "Chelsea Kelly-Reif",
            "--reason",
            "robots.txt permits a weekly fetch of this path",
            "--evidence",
            "tests/evidence/synthetic-fetch-policy-review.json",
        ]
    )

    assert code == 0
    assert registry_copy.read_bytes() == before
    policy = load_overlay(overlay_path).registry.by_id("clerk-page").fetch_policy
    assert (policy.outcome, policy.reviewer) == ("allow", "Chelsea Kelly-Reif")
    assert "NOT yet attempt-eligible" in capsys.readouterr().out


def test_sources_check_with_an_overlay_fetches_both_and_names_the_overlay_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fixture_before: bytes
) -> None:
    registry_path = _write_registry(tmp_path, COMMITTED)
    overlay_path = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL)])
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )

    code = cli.main(
        ["--registry", str(registry_path), "sources", "check", "--overlay", str(overlay_path)],
        fetcher=fetcher,
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "county-x/clerk-page" in out and COMMITTED.id in out
    assert "sources check: 2/2 reachable" in out


def test_watch_cli_says_which_overlays_a_run_carried_and_what_it_cannot_become(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fixture_before: bytes, fixture_after: bytes
) -> None:
    registry_path = _write_registry(tmp_path, COMMITTED)
    overlay_path = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL)])
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )
    argv = [
        "--registry",
        str(registry_path),
        "--db",
        str(tmp_path / "s.db"),
        "watch",
        "--overlay",
        str(overlay_path),
    ]

    assert cli.main(argv, fetcher=fetcher) == 0
    out = capsys.readouterr().out
    assert "overlay county-x: 1 entr(ies), 1 attempt-eligible" in out
    assert "not a committed-registry run" in out

    fetcher.set(OVERLAY_URL, fixture_after)
    assert cli.main(argv, fetcher=fetcher) == 0
    capsys.readouterr()
    assert cli.main(["--db", str(tmp_path / "s.db"), "review", "--list"]) == 0
    assert "[overlay county-x]" in capsys.readouterr().out


def test_baseline_with_an_overlay_uses_its_own_file_and_refuses_the_committed_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fixture_before: bytes,
) -> None:
    registry_path = _write_registry(tmp_path, COMMITTED)
    overlay_path = _write_overlay(tmp_path, "county-x", [_entry("clerk-page", OVERLAY_URL)])
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )
    db = str(tmp_path / "s.db")
    common = ["--registry", str(registry_path)]
    assert (
        cli.main([*common, "--db", db, "watch", "--overlay", str(overlay_path)], fetcher=fetcher)
        == 0
    )

    assert cli.main([*common, "--db", db, "baseline", "write", "--overlay", str(overlay_path)]) == 0
    written = tmp_path / "county-x.baseline-hashes.json"
    assert set(json.loads(written.read_text(encoding="utf-8"))["baselines"]) == {"clerk-page"}

    capsys.readouterr()
    assert (
        cli.main([*common, "baseline", "check", "--overlay", str(overlay_path)], fetcher=fetcher)
        == 0
    )
    out = capsys.readouterr().out
    assert "baseline-check-attempted-count: 1" in out and "baseline-check-moved-count: 0" in out

    stand_in = tmp_path / "committed-baseline.json"
    monkeypatch.setattr(cli, "default_baseline_path", lambda: stand_in)
    code = cli.main(
        [
            *common,
            "--db",
            db,
            "baseline",
            "write",
            "--overlay",
            str(overlay_path),
            "--out",
            str(stand_in),
        ]
    )
    assert code == 1
    assert "committed registry's baseline" in capsys.readouterr().err
    assert not stand_in.exists()


def test_evidence_export_of_an_overlay_change_reads_the_overlays_own_bytes(
    tmp_path: Path, fixture_before: bytes, fixture_after: bytes
) -> None:
    """The overlay entry shares the committed entry's id; only the namespace says whose bytes."""
    registry_path = _write_registry(tmp_path, COMMITTED)
    overlay_path = _write_overlay(tmp_path, "county-x", [_entry(COMMITTED.id, OVERLAY_URL)])
    fetcher = StubFetcher(
        {COMMITTED.url: (fixture_before, HTML), OVERLAY_URL: (fixture_before, HTML)}
    )
    db = str(tmp_path / "s.db")
    argv = ["--registry", str(registry_path), "--db", db, "watch", "--overlay", str(overlay_path)]
    assert cli.main(argv, fetcher=fetcher) == 0
    fetcher.set(OVERLAY_URL, fixture_after)
    assert cli.main(argv, fetcher=fetcher) == 0
    with SnapshotStore(Path(db)) as store:
        (change,) = store.changes(overlay_id="county-x")
    bundle = tmp_path / "bundle"

    assert cli.main(["--db", db, "evidence", "export", change.id, "--out", str(bundle)]) == 0
    assert cli.main(["evidence", "verify", str(bundle)]) == 0


# ---------------------------------------------------------------------------------------
# Migration 12, measured
# ---------------------------------------------------------------------------------------

_ORIGINAL_COLUMNS = {
    "snapshots": ("snapshot_id", "source_id", "url", "content_sha256", "raw_bytes"),
    "changes": ("change_id", "source_id", "url", "previous_hash", "new_hash", "kind"),
    "source_health": ("source_id", "consecutive_failures", "last_error", "streak_started_at"),
    "run_sources": ("run_id", "source_id", "eligible", "attempted", "observation_outcome"),
    "fetch_attempts": ("run_id", "source_id", "ok", "raw_sha256", "extraction_outcome"),
    "run_observations": ("run_id", "change_id"),
    "review_decisions": ("decision_id", "change_id", "decision", "actor"),
}
_ORDER = {
    "snapshots": "snapshot_id",
    "changes": "change_id",
    "source_health": "source_id",
    "run_sources": "run_id, source_id",
    "fetch_attempts": "run_id, source_id",
    "run_observations": "run_id, change_id",
    "review_decisions": "decision_id",
}
_H1, _H2 = "1" * 64, "2" * 64


def _seed_prefix_11(conn: sqlite3.Connection) -> str:
    """Rows in every table migration 12 touches, written the way a store at prefix 11 held them."""
    stamp = "2026-09-01T12:00:00+00:00"
    conn.execute(
        "INSERT INTO watch_runs (run_id, started_at, completed_at, as_of, registry_version, "
        "registry_revision, jurisdiction, state, eligible_count, attempted_count, "
        "successful_count, observation_count, unmeasured_count, error) VALUES "
        "('run-1', ?, ?, '2026-09-01', '1.0', ?, NULL, 'complete', 1, 1, 1, 1, 0, '')",
        (stamp, stamp, "a" * 64),
    )
    conn.execute(
        "INSERT INTO run_sources (run_id, source_id, jurisdiction, document_class, url, "
        "authority, eligible, eligibility_reasons, attempted, retrieval_success, outcome, "
        "observation_outcome) VALUES "
        "('run-1', 'eligible', 'TX', 'drivers_license', 'https://ex.gov/e', 'A', 1, '[]', 1, 1, "
        "'success', 'measured'), "
        "('run-1', 'ineligible', 'TX', 'birth_certificate', 'https://ex.gov/i', 'A', 0, "
        "'[\"unverified\"]', 0, NULL, '', '')"
    )
    conn.execute(
        "INSERT INTO fetch_attempts (run_id, source_id, url, attempted_at, completed_at, ok, "
        "http_status, content_type, normalizer_version, extractor_version, final_url, "
        "redirect_chain, raw_sha256, normalized_sha256, bytes_received, byte_limit, truncated, "
        "extraction_outcome, error_class) VALUES ('run-1', 'eligible', 'https://ex.gov/e', ?, ?, "
        "1, 200, 'text/html', ?, ?, 'https://ex.gov/e', '[]', ?, ?, 8, 1024, 0, "
        "'text-normalized', '')",
        (stamp, stamp, NORMALIZER_VERSION, EXTRACTOR_VERSION, "a" * 64, "b" * 64),
    )
    for digest in (_H1, _H2):
        conn.execute(
            "INSERT INTO snapshots (source_id, url, fetched_at, http_status, content_sha256, "
            "raw_bytes, normalized_text, normalizer_version, extractor_version) VALUES "
            "('eligible', 'https://ex.gov/e', ?, 200, ?, ?, 'text', ?, ?)",
            (stamp, digest, digest.encode(), NORMALIZER_VERSION, EXTRACTOR_VERSION),
        )
    cid = change_id("eligible", _H1, _H2)
    conn.execute(
        "INSERT INTO changes (change_id, source_id, jurisdiction, document_class, url, "
        "observed_at, previous_hash, new_hash, diff_excerpt, kind, significance, review_status) "
        "VALUES (?, 'eligible', 'TX', 'drivers_license', 'https://ex.gov/e', ?, ?, ?, "
        "'-old passage\n+new passage', 'content_drift', 'unclassified', 'unreviewed')",
        (cid, stamp, _H1, _H2),
    )
    conn.execute(
        "INSERT INTO run_observations (run_id, change_id, observed_at) VALUES ('run-1', ?, ?)",
        (cid, stamp),
    )
    conn.execute(
        "INSERT INTO review_decisions (decision_id, change_id, stage, decision, significance, "
        "actor, decided_at, public_copy) VALUES ('decision-1', ?, 'first', 'dismissed', "
        "'unclassified', 'Chelsea Kelly-Reif', '2026-09-02T12:00:00+00:00', '')",
        (cid,),
    )
    conn.execute(
        "INSERT INTO source_health (source_id, consecutive_failures, last_error, "
        "last_failure_at, streak_started_at) VALUES ('flaky', 3, 'timeout', ?, ?)",
        (stamp, "2026-08-18T12:00:00+00:00"),
    )
    conn.commit()
    return cid


def _prefix(last: int) -> tuple[tuple[int, str, str], ...]:
    return tuple(migration for migration in store_module._MIGRATIONS if migration[0] <= last)


def _dump(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: [
            tuple(row)
            for row in conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY {_ORDER[table]}"  # noqa: S608 — module literals
            )
        ]
        for table, columns in _ORIGINAL_COLUMNS.items()
    }


def test_migration_12_keeps_every_row_and_files_it_under_the_committed_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "prefix-11.db"
    with monkeypatch.context() as patched:
        patched.setattr(store_module, "_MIGRATIONS", _prefix(11))
        with SnapshotStore(db) as old:
            cid = _seed_prefix_11(old._conn)
            before = _dump(old._conn)
    assert all(before[table] for table in before), "every table the migration touches has a row"

    with SnapshotStore(db) as migrated:
        after = _dump(migrated._conn)
        namespaces = {
            table: {row[0] for row in migrated._conn.execute(f"SELECT overlay_id FROM {table}")}  # noqa: S608 — module literals
            for table in ("snapshots", "changes", "source_health", "run_sources", "fetch_attempts")
        }
        orphans = migrated._conn.execute("PRAGMA foreign_key_check").fetchall()
        change = migrated.get_change(cid)
        run = migrated.watch_run("run-1")
        streak = migrated.failure_streak("flaky")
        ledger = [row[0] for row in migrated._conn.execute("SELECT version FROM schema_migrations")]
        # Writable, not merely readable: the first form of this migration read back every row
        # and could not start a run, because a trigger pointed at a table it had dropped.
        new_run = migrated.start_watch_run(
            as_of=AS_OF,
            registry_version="1.0",
            registry_revision="c" * 64,
            jurisdiction=None,
            sources=(
                RunSourceInput(
                    source_id="eligible",
                    jurisdiction="TX",
                    document_class="drivers_license",
                    url="https://ex.gov/e",
                    authority="A",
                    eligible=True,
                    eligibility_reasons=(),
                ),
            ),
        )
        migrated.begin_fetch_attempt(new_run, source_id="eligible", url="https://ex.gov/e")

    assert after == before
    assert namespaces == {table: {""} for table in namespaces}
    assert orphans == []
    assert (change.id, change.overlay_id) == (cid, "")
    assert (run.eligible_source_ids, run.observation_count) == (("eligible",), 1)
    assert streak == 3
    assert max(ledger) == 12


def _triggers(db: Path) -> dict[str, tuple[str, str]]:
    with sqlite3.connect(db) as conn:
        return {
            name: (table, sql)
            for name, table, sql in conn.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
            )
        }


def test_migration_12_changes_exactly_the_triggers_it_names_and_recreates_the_rest_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_db, new_db = tmp_path / "old.db", tmp_path / "new.db"
    with monkeypatch.context() as patched:
        patched.setattr(store_module, "_MIGRATIONS", _prefix(11))
        SnapshotStore(old_db).close()
    SnapshotStore(new_db).close()
    before, after = _triggers(old_db), _triggers(new_db)

    assert set(before) - set(after) == set()
    assert set(after) - set(before) == {
        "trg_run_overlays_declared_before_the_denominator",
        "trg_run_overlays_no_update",
        "trg_run_overlays_no_delete",
        "trg_overlay_run_sources_require_a_declared_overlay",
        "trg_run_sources_namespace_is_immutable",
    }
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {
        "trg_changes_observation_valid_on_insert",
        "trg_changes_observation_no_update",
        "trg_correction_requires_publishable_replacement",
    }
    rebuilt = {name for name, (table, _) in before.items() if table == "fetch_attempts"}
    assert len(rebuilt) == 6, "the six attempt triggers came back, each byte-identical"


def test_migration_12_refuses_and_rolls_back_rather_than_orphan_a_copied_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "orphaned.db"
    with monkeypatch.context() as patched:
        patched.setattr(store_module, "_MIGRATIONS", _prefix(11))
        with SnapshotStore(db) as old:
            old._conn.execute("PRAGMA foreign_keys = OFF")
            old._conn.execute(
                "INSERT INTO fetch_attempts (run_id, source_id, url, attempted_at) "
                "VALUES ('no-such-run', 'x', 'https://ex.gov/x', '2026-09-01T00:00:00+00:00')"
            )
            old._conn.commit()

    with pytest.raises(StoreError, match="migration 12"):
        SnapshotStore(db)

    with sqlite3.connect(db) as conn:
        assert max(row[0] for row in conn.execute("SELECT version FROM schema_migrations")) == 11
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(fetch_attempts)")}
    assert "run_sources_pre_overlay" not in tables and "run_overlays" not in tables
    assert "overlay_id" not in columns


# ---------------------------------------------------------------------------------------
# The SQL half of each namespace rule, independent of the Python that normally obeys it
# ---------------------------------------------------------------------------------------


def _run_input(source_id: str, overlay_id: str = "") -> RunSourceInput:
    return RunSourceInput(
        source_id=source_id,
        jurisdiction="TX",
        document_class="drivers_license",
        url=f"https://ex.gov/{source_id}",
        authority="A",
        eligible=True,
        eligibility_reasons=(),
        overlay_id=overlay_id,
    )


def test_an_overlay_row_cannot_enter_a_run_that_did_not_declare_its_overlay(
    store: SnapshotStore,
) -> None:
    with pytest.raises(StoreError, match="does not declare"):
        store.start_watch_run(
            as_of=AS_OF,
            registry_version="1.0",
            registry_revision="a" * 64,
            jurisdiction=None,
            sources=(_run_input("clerk", "county-x"),),
        )
    run_id = store.start_watch_run(
        as_of=AS_OF,
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction=None,
        sources=(_run_input("committed"),),
    )
    with pytest.raises(sqlite3.IntegrityError, match="declare the overlay"):
        store._conn.execute(
            "INSERT INTO run_sources (run_id, overlay_id, source_id, jurisdiction, "
            "document_class, url, authority, eligible, eligibility_reasons) VALUES "
            "(?, 'county-x', 'clerk', 'TX', 'drivers_license', 'https://ex.gov/c', 'A', 1, '[]')",
            (run_id,),
        )


def test_a_run_declares_its_overlays_before_its_denominator_and_never_after(
    store: SnapshotStore,
) -> None:
    run_id = store.start_watch_run(
        as_of=AS_OF,
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction=None,
        sources=(_run_input("committed"),),
    )

    with pytest.raises(sqlite3.IntegrityError, match="before its source denominator"):
        store._conn.execute(
            "INSERT INTO run_overlays (run_id, overlay_id, overlay_revision) VALUES (?, 'x', ?)",
            (run_id, "a" * 64),
        )


def test_the_namespace_of_a_recorded_row_cannot_be_rewritten(store: SnapshotStore) -> None:
    change = ChangeRecord.observed(
        source_id="s",
        jurisdiction="TX",
        document_class="drivers_license",
        url="https://ex.gov/s",
        previous_hash="a" * 64,
        new_hash="b" * 64,
        diff_excerpt="-old passage\n+new passage",
    )
    store.record_change(change)
    run_id = store.start_watch_run(
        as_of=AS_OF,
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction=None,
        sources=(_run_input("committed"),),
    )

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("UPDATE changes SET overlay_id = 'x' WHERE change_id = ?", (change.id,))
    with pytest.raises(sqlite3.IntegrityError, match="keeps the namespace"):
        store._conn.execute("UPDATE run_sources SET overlay_id = 'x' WHERE run_id = ?", (run_id,))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        store._conn.execute(
            "INSERT INTO snapshots (overlay_id, source_id, url, fetched_at, content_sha256, "
            "raw_bytes, normalized_text, normalizer_version, extractor_version) VALUES "
            "('a/b', 's', 'https://ex.gov/s', ?, ?, x'00', 't', ?, ?)",
            (PINNED.isoformat(), "c" * 64, NORMALIZER_VERSION, EXTRACTOR_VERSION),
        )


def test_a_correction_cannot_cross_namespaces_and_still_works_within_one(
    store: SnapshotStore,
) -> None:
    def observed(overlay_id: str, new_hash: str) -> ChangeRecord:
        change = ChangeRecord.observed(
            source_id="s",
            overlay_id=overlay_id,
            jurisdiction="TX",
            document_class="drivers_license",
            url="https://ex.gov/s",
            previous_hash="a" * 64,
            new_hash=new_hash,
            diff_excerpt="-old passage\n+new passage",
            observed_at=PINNED - timedelta(days=2),
        )
        store.record_change(change)
        return _confirm(store, change)

    subject = observed("", "b" * 64)
    other_namespace = observed("county-x", "c" * 64)
    same_namespace = observed("", "d" * 64)

    with pytest.raises(StoreError, match="source identity differs"):
        store.record_lifecycle_event(
            subject.corrected_by(
                replacement_id=other_namespace.id,
                actor="Chelsea Kelly-Reif",
                reason="superseded_observation",
            )
        )
    store.record_lifecycle_event(
        subject.corrected_by(
            replacement_id=same_namespace.id,
            actor="Chelsea Kelly-Reif",
            reason="superseded_observation",
        )
    )
    assert store.get_change(subject.id).superseded_by == same_namespace.id


# ---------------------------------------------------------------------------------------
# The overlay file's published schema, held to the loader that is its authority
# ---------------------------------------------------------------------------------------

OVERLAY_SCHEMA = repo_root() / "docs" / "schema" / "registry-overlay-v1.schema.json"


def _overlay_schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(OVERLAY_SCHEMA.read_text(encoding="utf-8"))
    return loaded


def _valid_document() -> dict[str, Any]:
    return {
        "registry_version": REGISTRY_VERSION,
        "overlay_id": "county-x",
        "sources": [_entry("clerk-page", OVERLAY_URL)],
        "gaps": [
            {
                "jurisdiction": "TX",
                "document_class": "birth_certificate",
                "reason": "blocked-403",
                "hosts": ["vitals.example-county.gov"],
                "checked": "2026-09-11",
                "detail": "serves a browser and 403s a descriptive user agent",
            }
        ],
    }


def test_the_documented_overlay_example_validates_and_loads(tmp_path: Path) -> None:
    text = (repo_root() / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    blocks = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)]
    (example,) = [block for block in blocks if "overlay_id" in block]
    schema = _overlay_schema()
    path = tmp_path / "example.json"
    path.write_text(json.dumps(example), encoding="utf-8")

    assert _validate(example, schema, schema, "$") == []
    (overlay,) = load_overlays([path], load_registry())
    assert overlay.overlay_id == example["overlay_id"]


def test_the_overlay_schemas_vocabularies_are_the_loaders() -> None:
    schema = _overlay_schema()
    defs = schema["$defs"]

    assert schema["properties"]["registry_version"]["const"] == REGISTRY_VERSION
    assert set(defs["source"]["properties"]["jurisdiction"]["enum"]) == JURISDICTIONS
    assert set(defs["source"]["properties"]["document_class"]["enum"]) == DOCUMENT_CLASSES
    assert set(defs["gap"]["properties"]["reason"]["enum"]) == GAP_REASONS
    assert set(defs["verification"]["properties"]["status"]["enum"]) == VERIFICATION_STATUSES
    assert set(defs["fetch_policy"]["properties"]["outcome"]["enum"]) == FETCH_POLICY_OUTCOMES


def _drop(*path: str) -> Callable[[dict[str, Any]], None]:
    def mutate(document: dict[str, Any]) -> None:
        node: Any = document
        for key in path[:-1]:
            node = node[0] if key == "0" else node[key]
        del node[path[-1]]

    return mutate


def _set(value: object, *path: str) -> Callable[[dict[str, Any]], None]:
    def mutate(document: dict[str, Any]) -> None:
        node: Any = document
        for key in path[:-1]:
            node = node[0] if key == "0" else node[key]
        node[path[-1]] = value

    return mutate


_REJECTED_BY_BOTH: dict[str, Callable[[dict[str, Any]], None]] = {
    "no registry_version": _drop("registry_version"),
    "no overlay_id": _drop("overlay_id"),
    "no sources": _drop("sources"),
    "empty sources": _set([], "sources"),
    "free-text overlay id": _set("County X", "overlay_id"),
    "unknown registry version": _set("2.0", "registry_version"),
    **{
        f"source without {key}": _drop("sources", "0", key)
        for key in ("id", "jurisdiction", "document_class", "url", "authority", "notes")
    },
    "unknown jurisdiction": _set("XX", "sources", "0", "jurisdiction"),
    "unknown document class": _set("tattoo", "sources", "0", "document_class"),
    "plain http": _set("http://clerk.example-county.gov/x", "sources", "0", "url"),
    "a fragment": _set(OVERLAY_URL + "#fees", "sources", "0", "url"),
    "a non-slug id": _set("Clerk_Page", "sources", "0", "id"),
    "verified with no verification": _set({"status": "unverified"}, "sources", "0", "verification"),
    "unknown gap reason": _set("we-forgot", "gaps", "0", "reason"),
    "a gap naming no host": _set([], "gaps", "0", "hosts"),
    **{f"reserved id {slug}": _set(slug, "overlay_id") for slug in sorted(RESERVED_OVERLAY_IDS)},
}


@pytest.mark.parametrize("case", sorted(_REJECTED_BY_BOTH))
def test_a_document_the_overlay_schema_rejects_the_loader_rejects_too(
    tmp_path: Path, case: str
) -> None:
    document = copy.deepcopy(_valid_document())
    _REJECTED_BY_BOTH[case](document)
    schema = _overlay_schema()
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert _validate(document, schema, schema, "$") != [], f"{case!r} is not a schema rejection"
    with pytest.raises(RegistryError):
        load_overlay(path)


def test_a_near_miss_of_a_reserved_id_is_accepted_by_both(tmp_path: Path) -> None:
    """The reserved-id rule names real feeds; `us-zz` is no jurisdiction and is not refused."""
    document = _valid_document()
    document["overlay_id"] = "us-zz"
    schema = _overlay_schema()
    path = tmp_path / "near-miss.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert _validate(document, schema, schema, "$") == []
    assert load_overlay(path).overlay_id == "us-zz"
