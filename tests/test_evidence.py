"""Portable evidence bundles: export refuses half an argument, verify cannot quietly pass.

Two properties carry most of this file.

**Export is all-or-nothing.** A change whose baseline has been pruned out of the store, or a
removal escalation that never had an `after` side, is refused by name and leaves no directory
behind. The alternative — a bundle carrying one side — puts the operator's word back at the
centre of the artifact that exists to remove it.

**Verify's report is a partition over a closed vocabulary.** Every check in
`CHECK_NAMES` appears in every result exactly once, with an outcome from `CHECK_OUTCOMES`.
That assertion is here rather than a nicety because of a measured failure mode in this
portfolio: a check that stops being reported is indistinguishable, to anything counting
outcomes, from a check that passed. So a check that could not run says SKIPPED and says why,
and `render_verification` refuses to print the word VERIFIED on its own when one did.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.changes import (
    ChangeRecord,
    IndependentReviewStatus,
    ReviewStatus,
    Significance,
)
from id_churn_sentinel.core.detect import watch_registry
from id_churn_sentinel.core.evidence import (
    CHECK_FAILED,
    CHECK_NAMES,
    CHECK_OK,
    CHECK_OUTCOMES,
    CHECK_SKIPPED,
    EXIT_MISMATCH,
    EXIT_UNREADABLE,
    EXIT_VERIFIED,
    BundleError,
    BundleUnreadableError,
    CheckResult,
    export_bundle,
    render_verification,
    verify_bundle,
)
from id_churn_sentinel.core.normalize import (
    EXTRACTOR_VERSION,
    NORMALIZER_VERSION,
    content_hash,
    normalize_html,
)
from id_churn_sentinel.core.publish import changes_json
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import SnapshotStore

from .conftest import StubFetcher, eligible_source, simple_pdf

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 7, 13)

PAGE_A = b"<html><body><p>Bring a certified court order.</p></body></html>"
PAGE_B = b"<html><body><p>Bring a certified court order and a physician letter.</p></body></html>"
PAGE_C = (
    b"<html><body><p>Bring a certified court order, a physician letter and a fee.</p></body></html>"
)


def _one_source_registry(source: Source) -> Registry:
    return Registry(version="1.0", sources=(eligible_source(source),))


def _watch(registry: Registry, store: SnapshotStore, fetcher: StubFetcher, minute: int) -> None:
    moment = NOW.replace(minute=minute)
    watch_registry(
        registry,
        store,
        fetcher,
        as_of=AS_OF,
        started_at=moment,
        completed_at=moment,
    )


def _store_with_one_change(
    db: Path, source: Source, *, retention: int = 5, bodies: tuple[bytes, ...] = (PAGE_A, PAGE_B)
) -> SnapshotStore:
    """A store holding real snapshots, real fetch receipts and a real detected change.

    Built by running the production watcher against an offline fetcher rather than by
    inserting rows: the join an evidence export depends on — a snapshot's raw bytes to the
    fetch receipt that recorded their content type — is a property of what `watch` writes,
    and a hand-built store would test the join against a fixture of the join.
    """

    registry = _one_source_registry(source)
    store = SnapshotStore(db, retention=retention)
    fetcher = StubFetcher({source.url: (bodies[0], "text/html; charset=utf-8")})
    for index, body in enumerate(bodies):
        fetcher.set(source.url, body, "text/html; charset=utf-8")
        _watch(registry, store, fetcher, minute=index)
    return store


def _changes(store: SnapshotStore, source: Source) -> tuple[ChangeRecord, ...]:
    """Every recorded change for one source, newest first (the store's own order)."""

    return tuple(change for change in store.changes() if change.source_id == source.id)


def _sole_change(store: SnapshotStore, source: Source) -> ChangeRecord:
    recorded = _changes(store, source)
    assert recorded, "the watcher recorded no change; the fixture bodies must differ in text"
    return recorded[-1]


@pytest.fixture
def bundle(tmp_path: Path, source: Source) -> Path:
    with _store_with_one_change(tmp_path / "sentinel.db", source) as store:
        change = _sole_change(store, source)
        export_bundle(store, change.id, tmp_path / "bundle", now=NOW)
    return tmp_path / "bundle"


# ---------------------------------------------------------------------------------------
# the round trip
# ---------------------------------------------------------------------------------------


def test_export_then_verify_passes_on_a_real_change(bundle: Path) -> None:
    """The headline: a bundle produced by `export` verifies from its own bytes."""

    result = verify_bundle(bundle)
    assert result.exit_code == EXIT_VERIFIED, [check.to_dict() for check in result.failed]
    ran = {check.name: check.outcome for check in result.checks}
    assert ran["file-hashes"] == CHECK_OK
    assert ran["renormalization"] == CHECK_OK
    assert ran["hash-binding"] == CHECK_OK
    assert ran["diff"] == CHECK_OK
    assert ran["published-excerpt"] == CHECK_OK


def test_the_bundle_holds_both_sides_and_a_manifest_hashing_every_file(bundle: Path) -> None:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    present = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()}
    assert present == {entry["path"] for entry in manifest["files"]} | {"manifest.json"}
    assert (bundle / "before" / "raw.bin").read_bytes() == PAGE_A
    assert (bundle / "after" / "raw.bin").read_bytes() == PAGE_B
    assert "physician letter" in (bundle / "diff.patch").read_text(encoding="utf-8")


def test_the_bundle_is_verifiable_with_no_store_and_no_network(
    bundle: Path, tmp_path: Path
) -> None:
    """Moved somewhere else entirely, with the store deleted. This is the handover case."""

    handover = tmp_path / "handover"
    handover.mkdir()
    for item in bundle.rglob("*"):
        if item.is_file():
            target = handover / item.relative_to(bundle)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())
    (tmp_path / "sentinel.db").unlink()
    assert verify_bundle(handover).exit_code == EXIT_VERIFIED


# ---------------------------------------------------------------------------------------
# tampering
# ---------------------------------------------------------------------------------------


def test_flipping_one_byte_of_the_after_snapshot_fails_verify_at_that_file(bundle: Path) -> None:
    raw = bundle / "after" / "raw.bin"
    body = bytearray(raw.read_bytes())
    body[-20] = body[-20] ^ 0x01
    raw.write_bytes(bytes(body))

    result = verify_bundle(bundle)
    assert result.exit_code == EXIT_MISMATCH
    failure = next(check for check in result.checks if check.outcome == CHECK_FAILED)
    assert failure.name == "file-hashes"
    assert "after/raw.bin" in failure.detail


def test_a_file_the_manifest_does_not_list_is_a_failure(bundle: Path) -> None:
    """An unlisted file is verified by nothing and read by whoever opens the directory."""

    (bundle / "cover-letter.txt").write_text("trust me", encoding="utf-8")
    result = verify_bundle(bundle)
    assert result.exit_code == EXIT_MISMATCH
    inventory = next(check for check in result.checks if check.name == "bundle-inventory")
    assert inventory.outcome == CHECK_FAILED
    assert "cover-letter.txt" in inventory.detail


def test_a_bundle_whose_normalizer_version_is_unknown_fails_closed_with_that_version_named(
    bundle: Path,
) -> None:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sides"]["before"]["normalizer_version"] = "passage-text-v99"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = verify_bundle(bundle)
    assert result.exit_code == EXIT_MISMATCH
    contract = next(check for check in result.checks if check.name == "representation-contract")
    assert contract.outcome == CHECK_FAILED
    assert "passage-text-v99" in contract.detail


def test_a_manifest_this_build_does_not_read_is_unreadable_not_a_mismatch(bundle: Path) -> None:
    """Exit 2 and exit 1 answer different questions and a caller has to be able to tell."""

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_version"] = "99.0"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    assert verify_bundle(bundle).exit_code == EXIT_UNREADABLE


def test_a_directory_with_no_manifest_is_unreadable(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-bundle"
    empty.mkdir()
    result = verify_bundle(empty)
    assert result.exit_code == EXIT_UNREADABLE
    assert "is not an evidence bundle" in result.checks[0].detail


# ---------------------------------------------------------------------------------------
# the closed vocabulary — the assertion that keeps a check from quietly vanishing
# ---------------------------------------------------------------------------------------


def test_every_result_partitions_the_whole_check_vocabulary(bundle: Path, tmp_path: Path) -> None:
    """Every check name, exactly once, with an outcome from the closed set — in every result.

    Including the short-circuit paths. A report that simply stops listing checks after the
    first failure reads, to anything that counts outcomes, exactly like a report in which the
    rest passed; that is the shape of the bug this repository keeps finding, so it is asserted
    over the whole vocabulary rather than over the checks that happened to run.
    """

    unreadable = tmp_path / "empty"
    unreadable.mkdir()
    tampered = tmp_path / "tampered"
    tampered.mkdir()
    for item in bundle.rglob("*"):
        if item.is_file():
            target = tampered / item.relative_to(bundle)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())
    (tampered / "after" / "normalized.txt").write_text("rewritten", encoding="utf-8")

    for candidate in (bundle, tampered, unreadable):
        result = verify_bundle(candidate)
        names = [check.name for check in result.checks]
        assert names == list(CHECK_NAMES), f"{candidate.name}: {names}"
        assert len(names) == len(set(names))
        assert {check.outcome for check in result.checks} <= CHECK_OUTCOMES


def test_a_short_circuited_run_marks_the_rest_skipped_rather_than_omitting_them(
    bundle: Path,
) -> None:
    (bundle / "diff.patch").write_text("rewritten", encoding="utf-8")
    result = verify_bundle(bundle)
    later = {check.name: check for check in result.checks if check.name != "manifest"}
    assert later["file-hashes"].outcome == CHECK_FAILED
    assert later["hash-binding"].outcome == CHECK_SKIPPED
    assert "not reached" in later["hash-binding"].detail


def test_verify_never_prints_verified_on_its_own_when_a_check_did_not_run(bundle: Path) -> None:
    report = render_verification(verify_bundle(bundle))
    assert "SKIPPED" in report
    assert "NOT RUN" in report
    assert "VERIFIED — every check ran and passed." not in report


def test_a_bundle_with_every_check_run_says_so_plainly(bundle: Path, tmp_path: Path) -> None:
    """The other half of the same property: the unqualified word is reachable, and earned."""

    change = json.loads((bundle / "change.json").read_text(encoding="utf-8"))
    feed = tmp_path / "changes.json"
    feed.write_text(json.dumps({"changes": [change]}, indent=2), encoding="utf-8")
    report = render_verification(verify_bundle(bundle, changes_path=feed))
    assert "VERIFIED — every check ran and passed." in report
    assert "SKIPPED" not in report


# ---------------------------------------------------------------------------------------
# the published feed cross-check
# ---------------------------------------------------------------------------------------


def test_the_feed_cross_check_is_skipped_by_name_when_no_feed_is_given(bundle: Path) -> None:
    """Absence reported as absence. It is never reported as agreement with nothing."""

    check = next(c for c in verify_bundle(bundle).checks if c.name == "published-feed")
    assert check.outcome == CHECK_SKIPPED
    assert "no --changes document given" in check.detail


def test_the_feed_cross_check_matches_a_real_published_changes_document(
    tmp_path: Path, source: Source
) -> None:
    """Against the bytes `sentinel publish` actually writes, not a hand-shaped stand-in."""

    registry = _one_source_registry(source)
    with _store_with_one_change(tmp_path / "sentinel.db", source) as store:
        observed = _sole_change(store, source)
        confirmed = observed.reviewed_by(
            reviewer="Chelsea Kelly-Reif",
            significance=Significance.SUBSTANTIVE,
            status=ReviewStatus.CONFIRMED,
            public_copy="A physician letter was added to the requirements.",
        ).independently_reviewed_by(
            reviewer="Synthetic Independent Reviewer",
            status=IndependentReviewStatus.CONFIRMED,
            qualification_ref="tests/evidence/synthetic-independent-qualification.json",
            conflict_attestation_ref="tests/evidence/synthetic-independent-conflict.json",
        )
        store.update_change(confirmed)
        store.record_independent_review(confirmed)
        export_bundle(store, confirmed.id, tmp_path / "bundle", now=NOW)

    feed = tmp_path / "changes.json"
    feed.write_text(
        changes_json(
            [confirmed],
            feed_url="https://example.invalid/",
            generated_at=NOW,
            registry=registry,
            eligibility_as_of=AS_OF,
        ),
        encoding="utf-8",
    )
    result = verify_bundle(tmp_path / "bundle", changes_path=feed)
    assert result.exit_code == EXIT_VERIFIED, [check.to_dict() for check in result.failed]
    check = next(c for c in result.checks if c.name == "published-feed")
    assert check.outcome == CHECK_OK
    # The one legitimate asymmetry, named rather than passed over in silence:
    # `publish._change_payload` is `{**record.to_dict(), "source_verification": ...}`, so
    # every real feed entry carries exactly one field no bundle record does.
    assert "source_verification" in check.detail
    assert "does not attest" in check.detail


def test_a_feed_that_omits_a_field_the_bundle_attests_fails(bundle: Path, tmp_path: Path) -> None:
    """The defect this widening removes: the comparison was over the INTERSECTION.

    Measured on `origin/main` against this same exported bundle: deleting `new_hash`,
    `diff_excerpt` and `previous_hash` from the served entry left `published-feed: ok`,
    `exit_code` 0, and the report's last line reading "VERIFIED - every check ran and
    passed." Three of the fields the bundle exists to attest, never compared, certified
    as matching by the command a third party runs to check exactly that.
    """

    change = json.loads((bundle / "change.json").read_text(encoding="utf-8"))
    omitted = ["diff_excerpt", "new_hash", "previous_hash"]
    for key in omitted:
        assert key in change, f"the fixture no longer carries {key}; this proves nothing"
        del change[key]
    feed = tmp_path / "changes.json"
    feed.write_text(json.dumps({"changes": [change]}, indent=2), encoding="utf-8")

    result = verify_bundle(bundle, changes_path=feed)
    assert result.exit_code == EXIT_MISMATCH
    check = next(c for c in result.checks if c.name == "published-feed")
    assert check.outcome == CHECK_FAILED
    for key in omitted:
        assert key in check.detail
    assert "does not carry at all" in check.detail


def test_an_omission_and_a_mismatch_are_reported_as_two_causes(
    bundle: Path, tmp_path: Path
) -> None:
    """Two causes, two sentences: one is a feed that publishes something else, the other
    is a feed that publishes nothing there, and they send a reader to different places."""

    change = json.loads((bundle / "change.json").read_text(encoding="utf-8"))
    change["new_hash"] = "0" * 64
    del change["diff_excerpt"]
    feed = tmp_path / "changes.json"
    feed.write_text(json.dumps({"changes": [change]}, indent=2), encoding="utf-8")

    check = next(
        c for c in verify_bundle(bundle, changes_path=feed).checks if c.name == "published-feed"
    )
    assert check.outcome == CHECK_FAILED
    assert "differs on: ['new_hash']" in check.detail
    assert "does not carry at all: ['diff_excerpt']" in check.detail


def test_a_feed_entry_carrying_an_extra_field_still_passes_and_names_it(
    bundle: Path, tmp_path: Path
) -> None:
    """A field the bundle does not attest must not fail the check, and must not vanish.

    Failing on it would fail on every honest bundle, since `source_verification` is on
    every published entry. Ignoring it silently would let the entry grow three more
    fields with the report still saying only "matches".
    """

    change = json.loads((bundle / "change.json").read_text(encoding="utf-8"))
    change["source_verification"] = {"status": "unverified"}
    change["something_new"] = 1
    feed = tmp_path / "changes.json"
    feed.write_text(json.dumps({"changes": [change]}, indent=2), encoding="utf-8")

    result = verify_bundle(bundle, changes_path=feed)
    assert result.exit_code == EXIT_VERIFIED
    check = next(c for c in result.checks if c.name == "published-feed")
    assert check.outcome == CHECK_OK
    assert "something_new" in check.detail
    assert "source_verification" in check.detail


def test_a_feed_that_serves_a_different_record_for_this_id_fails(
    bundle: Path, tmp_path: Path
) -> None:
    change = json.loads((bundle / "change.json").read_text(encoding="utf-8"))
    change["new_hash"] = "0" * 64
    feed = tmp_path / "changes.json"
    feed.write_text(json.dumps({"changes": [change]}, indent=2), encoding="utf-8")

    result = verify_bundle(bundle, changes_path=feed)
    assert result.exit_code == EXIT_MISMATCH
    check = next(c for c in result.checks if c.name == "published-feed")
    assert check.outcome == CHECK_FAILED
    assert "new_hash" in check.detail


def test_a_feed_that_does_not_serve_this_change_at_all_fails(bundle: Path, tmp_path: Path) -> None:
    feed = tmp_path / "changes.json"
    feed.write_text(json.dumps({"changes": []}), encoding="utf-8")
    check = next(
        c for c in verify_bundle(bundle, changes_path=feed).checks if c.name == "published-feed"
    )
    assert check.outcome == CHECK_FAILED
    assert "serves no change with id" in check.detail


# ---------------------------------------------------------------------------------------
# export refuses rather than exporting half
# ---------------------------------------------------------------------------------------


def test_exporting_a_change_with_a_pruned_baseline_refuses_and_writes_nothing(
    tmp_path: Path, source: Source
) -> None:
    """Retention of two, three fetches: the first change's baseline is gone."""

    out = tmp_path / "bundle"
    with _store_with_one_change(
        tmp_path / "sentinel.db", source, retention=2, bodies=(PAGE_A, PAGE_B, PAGE_C)
    ) as store:
        first = _changes(store, source)[-1]
        assert first.previous_hash not in {
            snapshot.content_sha256 for snapshot in store.snapshots(source.id)
        }, "the fixture did not actually prune the baseline; the refusal below would be vacuous"
        with pytest.raises(BundleError) as raised:
            export_bundle(store, first.id, out, now=NOW)

    assert "before" in str(raised.value)
    assert not out.exists(), "a refused export left a directory behind"


def test_a_removal_escalation_is_refused_by_its_own_reason(tmp_path: Path, source: Source) -> None:
    """ "The after snapshot is missing" is true and tells the operator the wrong thing."""

    out = tmp_path / "bundle"
    with _store_with_one_change(tmp_path / "sentinel.db", source) as store:
        observed = _sole_change(store, source)
        removal = ChangeRecord.possibly_removed(
            source_id=source.id,
            jurisdiction=source.jurisdiction,
            document_class=source.document_class,
            url=source.url,
            last_known_hash=observed.new_hash,
            consecutive_failures=4,
            last_error="stubbed outage",
        )
        store.record_change(removal)
        with pytest.raises(BundleError) as raised:
            export_bundle(store, removal.id, out, now=NOW)

    assert "removal escalation" in str(raised.value)
    assert not out.exists()


def test_export_refuses_a_destination_that_already_holds_something(
    tmp_path: Path, source: Source
) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "leftover.txt").write_text("from last time", encoding="utf-8")
    with _store_with_one_change(tmp_path / "sentinel.db", source) as store:
        change = _sole_change(store, source)
        with pytest.raises(BundleError):
            export_bundle(store, change.id, out, now=NOW)
    assert {path.name for path in out.iterdir()} == {"leftover.txt"}


# ---------------------------------------------------------------------------------------
# the publication boundary
# ---------------------------------------------------------------------------------------


def test_no_internal_rationale_reaches_the_bundle(tmp_path: Path, source: Source) -> None:
    """A bundle is handed to a third party, so it carries the published shape and no more."""

    rationale = "internal note: our contact at the agency says this is temporary"
    with _store_with_one_change(tmp_path / "sentinel.db", source) as store:
        observed = _sole_change(store, source)
        reviewed = observed.reviewed_by(
            reviewer="Chelsea Kelly-Reif",
            significance=Significance.SUBSTANTIVE,
            status=ReviewStatus.CONFIRMED,
            note=rationale,
            public_copy="A physician letter was added to the requirements.",
        )
        store.update_change(reviewed)
        export_bundle(store, reviewed.id, tmp_path / "bundle", now=NOW)

    for path in (tmp_path / "bundle").rglob("*"):
        if path.is_file():
            assert rationale.encode("utf-8") not in path.read_bytes(), path


def test_the_fetch_receipts_carry_the_closed_error_class_and_not_the_fetcher_message(
    bundle: Path,
) -> None:
    receipts = json.loads((bundle / "fetch-attempts.json").read_text(encoding="utf-8"))
    assert receipts["attempts"], "the bundle recorded no fetch receipt"
    for attempt in receipts["attempts"]:
        assert "error_class" in attempt
        assert "error" not in attempt
        assert attempt["content_type"].startswith("text/html")


# ---------------------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------------------


def test_the_cli_round_trips_a_bundle_and_reports_the_skipped_check(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "sentinel.db"
    with _store_with_one_change(db, source) as store:
        change_id = _sole_change(store, source).id
    out = tmp_path / "bundle"

    assert main(["--db", str(db), "evidence", "export", change_id, "--out", str(out)]) == 0
    assert "written to" in capsys.readouterr().out

    assert main(["evidence", "verify", str(out)]) == EXIT_VERIFIED
    report = capsys.readouterr().out
    assert "[SKIPPED " in report
    assert "published-feed" in report


def test_the_cli_exits_2_on_a_refused_export_and_1_on_a_tampered_bundle(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "sentinel.db"
    with _store_with_one_change(db, source, retention=2, bodies=(PAGE_A, PAGE_B, PAGE_C)) as store:
        stale = _changes(store, source)[-1].id
        fresh = _changes(store, source)[0].id
    out = tmp_path / "bundle"

    assert (
        main(["--db", str(db), "evidence", "export", stale, "--out", str(out)]) == EXIT_UNREADABLE
    )
    assert not out.exists()
    capsys.readouterr()

    assert main(["--db", str(db), "evidence", "export", fresh, "--out", str(out)]) == 0
    capsys.readouterr()
    (out / "change.json").write_text("{}", encoding="utf-8")
    assert main(["evidence", "verify", str(out), "--json"]) == EXIT_MISMATCH
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == EXIT_MISMATCH
    assert [check["check"] for check in payload["checks"]] == list(CHECK_NAMES)


# ---------------------------------------------------------------------------------------
# the published manifest schema
# ---------------------------------------------------------------------------------------
#
# `docs/schema/evidence-bundle-v1.schema.json` is a promise to whoever receives a bundle, so
# it is tested against real exported bytes rather than asserted. The validator is the one
# `tests/test_schema.py` already hand-writes to keep the runtime dependency count at zero —
# imported rather than reimplemented, so a bundle manifest and a published feed are held to
# the same subset of JSON Schema and a hole in one is a hole in both.

from .test_schema import _validate  # noqa: E402 — read after the module's own fixtures

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "schema" / "evidence-bundle-v1.schema.json"
)


def test_a_real_exported_manifest_validates_against_the_published_schema(bundle: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert _validate(manifest, schema, schema, "manifest") == []


def test_the_schema_requires_every_key_the_exporter_actually_writes(bundle: Path) -> None:
    """Add a field to the manifest and forget the schema: this goes red.

    The reverse direction too. A schema that requires a key the exporter stopped writing
    would describe a document nobody produces, which is the same lie in the other direction.
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert set(schema["required"]) == set(manifest)
    assert set(schema["properties"]) == set(manifest)
    assert set(schema["$defs"]["side"]["required"]) == set(manifest["sides"]["before"])
    assert set(schema["$defs"]["file"]["required"]) == set(manifest["files"][0])


# ---------------------------------------------------------------------------------------
# every failure branch is reachable
# ---------------------------------------------------------------------------------------
#
# The tampering tests above all stop at `file-hashes`, which is correct behaviour and useless
# as coverage: a suite that only ever trips its first gate has proved nothing about the ones
# behind it, and a check nothing can reach is a check that cannot fail. `_retamper` repairs
# the manifest as it edits, so each later check has to catch the edit on its own terms.


def _retamper(bundle: Path, relative: str, body: bytes) -> None:
    """Rewrite one bundle file AND the manifest entry that describes it."""

    (bundle / relative).write_bytes(body)
    _edit_manifest(
        bundle,
        lambda manifest: [
            entry.update({"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
            for entry in manifest["files"]
            if entry["path"] == relative
        ],
    )


def _edit_manifest(bundle: Path, mutate: Callable[[dict[str, Any]], object]) -> None:
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _outcome(bundle: Path, name: str, *, changes_path: Path | None = None) -> CheckResult:
    result = verify_bundle(bundle, changes_path=changes_path)
    return next(check for check in result.checks if check.name == name)


def test_a_file_the_manifest_lists_but_the_bundle_lacks_is_named(bundle: Path) -> None:
    (bundle / "after" / "normalized.txt").unlink()
    check = _outcome(bundle, "file-hashes")
    assert check.outcome == CHECK_FAILED
    assert "after/normalized.txt" in check.detail
    assert "missing" in check.detail


def test_a_manifest_listing_no_files_fails_rather_than_verifying_nothing(bundle: Path) -> None:
    """The vacuous pass: zero files, zero mismatches, zero evidence."""

    _edit_manifest(bundle, lambda manifest: manifest.__setitem__("files", []))
    check = _outcome(bundle, "file-hashes")
    assert check.outcome == CHECK_FAILED
    assert "no files" in check.detail


def test_a_manifest_that_is_not_an_object_or_lacks_its_keys_is_unreadable(
    bundle: Path, tmp_path: Path
) -> None:
    (bundle / "manifest.json").write_text("[]", encoding="utf-8")
    assert verify_bundle(bundle).exit_code == EXIT_UNREADABLE

    (bundle / "manifest.json").write_text("{ not json", encoding="utf-8")
    assert verify_bundle(bundle).exit_code == EXIT_UNREADABLE

    (bundle / "manifest.json").write_text(
        json.dumps({"manifest_version": "1.0", "files": []}), encoding="utf-8"
    )
    result = verify_bundle(bundle)
    assert result.exit_code == EXIT_UNREADABLE
    assert "missing required key(s)" in result.checks[0].detail


def test_bytes_that_no_longer_normalize_to_the_committed_text_are_caught(bundle: Path) -> None:
    _retamper(bundle, "after/normalized.txt", b"a text nobody derived\n")
    check = _outcome(bundle, "renormalization")
    assert check.outcome == CHECK_FAILED
    assert "after/normalized.txt" in check.detail


def test_a_consistent_substitution_of_both_sides_still_fails_the_binding_to_the_record(
    bundle: Path,
) -> None:
    """The interesting tamper: internally consistent, and about a different document.

    Swap the raw bytes AND the normalized text together and every self-consistency check
    passes — which is exactly why `hash-binding` recomputes against the hashes the PUBLISHED
    record cites rather than against the manifest's own copy of them.
    """

    _retamper(bundle, "after/raw.bin", PAGE_C)
    _retamper(
        bundle, "after/normalized.txt", normalize_html(PAGE_C.decode("utf-8")).encode("utf-8")
    )
    result = verify_bundle(bundle)
    assert _outcome(bundle, "renormalization").outcome == CHECK_OK
    binding = next(check for check in result.checks if check.name == "hash-binding")
    assert binding.outcome == CHECK_FAILED
    assert "after/raw.bin" in binding.detail


def test_an_unknown_hashing_regime_is_a_failure_not_a_default(bundle: Path) -> None:
    _edit_manifest(
        bundle, lambda manifest: manifest["sides"]["before"].__setitem__("hashing_regime", "vibes")
    )
    check = _outcome(bundle, "hash-binding")
    assert check.outcome == CHECK_FAILED
    assert "vibes" in check.detail


def test_a_rewritten_patch_is_caught_by_re_deriving_it(bundle: Path) -> None:
    _retamper(bundle, "diff.patch", b"--- previous\n+++ current\n+nothing happened\n")
    check = _outcome(bundle, "diff")
    assert check.outcome == CHECK_FAILED
    assert "diff.patch" in check.detail


def test_a_rewritten_published_excerpt_is_caught(bundle: Path) -> None:
    change = json.loads((bundle / "change.json").read_text(encoding="utf-8"))
    change["diff_excerpt"] = "nothing of consequence changed"
    _retamper(bundle, "change.json", (json.dumps(change, indent=2) + "\n").encode("utf-8"))
    check = _outcome(bundle, "published-excerpt")
    assert check.outcome == CHECK_FAILED
    assert "change.json" in check.detail


def test_an_excerpt_the_exporter_measured_as_not_rederivable_is_skipped_with_that_reason(
    bundle: Path,
) -> None:
    """Skipped, with the exporter's own measured reason — not failed, and not passed."""

    _edit_manifest(
        bundle,
        lambda manifest: manifest.update(
            {
                "published_excerpt_is_rederivable": False,
                "published_excerpt_note": "the baseline was re-derived under another contract",
            }
        ),
    )
    check = _outcome(bundle, "published-excerpt")
    assert check.outcome == CHECK_SKIPPED
    assert "re-derived under another contract" in check.detail


def test_a_change_document_that_is_not_an_object_is_unreadable(bundle: Path) -> None:
    _retamper(bundle, "change.json", b"[]\n")
    with pytest.raises(BundleUnreadableError):
        verify_bundle(bundle)


def test_an_unreadable_changes_document_fails_the_cross_check(bundle: Path, tmp_path: Path) -> None:
    broken = tmp_path / "changes.json"
    broken.write_text("{ not json", encoding="utf-8")
    check = _outcome(bundle, "published-feed", changes_path=broken)
    assert check.outcome == CHECK_FAILED
    assert "unreadable" in check.detail

    check = _outcome(bundle, "published-feed", changes_path=tmp_path / "absent.json")
    assert check.outcome == CHECK_FAILED


def test_a_failing_report_says_not_verified(bundle: Path) -> None:
    (bundle / "diff.patch").write_text("rewritten", encoding="utf-8")
    report = render_verification(verify_bundle(bundle))
    assert "NOT VERIFIED" in report
    assert "VERIFIED — every check ran and passed." not in report


def test_a_snapshot_with_no_fetch_receipt_cannot_be_exported(
    tmp_path: Path, source: Source
) -> None:
    """A bundle whose bytes cannot be re-normalized is an archive, not evidence."""

    with SnapshotStore(tmp_path / "sentinel.db") as store:
        for body in (PAGE_A, PAGE_B):
            digest, text = content_hash(body, "text/html")
            store.record_snapshot(
                source_id=source.id,
                url=source.url,
                fetched_at=NOW,
                http_status=200,
                content_sha256=digest,
                raw_bytes=body,
                normalized_text=text,
                normalizer_version=NORMALIZER_VERSION,
                extractor_version=EXTRACTOR_VERSION,
            )
        change = ChangeRecord.observed(
            source_id=source.id,
            jurisdiction=source.jurisdiction,
            document_class=source.document_class,
            url=source.url,
            previous_hash=content_hash(PAGE_A, "text/html")[0],
            new_hash=content_hash(PAGE_B, "text/html")[0],
            diff_excerpt="-a\n+b",
            observed_at=NOW,
        )
        store.record_change(change)
        with pytest.raises(BundleError) as raised:
            export_bundle(store, change.id, tmp_path / "bundle", now=NOW)

    assert "no fetch receipt" in str(raised.value)
    assert not (tmp_path / "bundle").exists()


def test_a_snapshot_whose_hash_neither_side_reproduces_is_refused(
    tmp_path: Path, source: Source
) -> None:
    """A store inconsistent with itself is named as such, not exported around."""

    with _store_with_one_change(tmp_path / "sentinel.db", source) as store:
        change = _sole_change(store, source)
        store._conn.execute(
            "UPDATE snapshots SET normalized_text = ? WHERE content_sha256 = ?",
            ("a text that hashes to nothing this row claims", change.new_hash),
        )
        with pytest.raises(BundleError) as raised:
            export_bundle(store, change.id, tmp_path / "bundle", now=NOW)
    assert "neither its raw bytes nor its normalized text reproduce" in str(raised.value)


# ---------------------------------------------------------------------------------------
# the binary regime
# ---------------------------------------------------------------------------------------


def test_a_pdf_change_exports_and_verifies_under_the_raw_bytes_regime(
    tmp_path: Path, source: Source
) -> None:
    """PDFs are hashed over the whole file, losslessly, so the regime differs — and the
    bundle has to record which one produced the hash rather than assume the text one."""

    before = simple_pdf("Bring a court order.")
    after = simple_pdf("Bring a court order and a physician letter.")
    registry = _one_source_registry(source)
    with SnapshotStore(tmp_path / "sentinel.db") as store:
        fetcher = StubFetcher({source.url: (before, "application/pdf")})
        for index, body in enumerate((before, after)):
            fetcher.set(source.url, body, "application/pdf")
            _watch(registry, store, fetcher, minute=index)
        change = _sole_change(store, source)
        export_bundle(store, change.id, tmp_path / "bundle", now=NOW)

    manifest = json.loads((tmp_path / "bundle" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sides"]["after"]["hashing_regime"] == "raw-bytes"
    assert manifest["sides"]["after"]["content_type"] == "application/pdf"
    result = verify_bundle(tmp_path / "bundle")
    assert result.exit_code == EXIT_VERIFIED, [check.to_dict() for check in result.failed]
