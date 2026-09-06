"""Portable evidence bundles: the bytes behind one published change, checkable offline.

A published change record carries hashes and an excerpt. The bytes that prove it live in an
operator's SQLite store in `var/`, retained only while they are among the newest five
snapshots for that source, and a journalist or a legal-aid editor who wants to check the
claim six months from now has nothing but the operator's word for it. `docs/adr` says in
terms that a diff you cannot reproduce later is a claim, not evidence.

An evidence bundle is a directory an operator can hand over. It holds the raw bytes of both
sides, both normalized texts, the contract versions they were produced under, the fetch
receipts that recorded them, the re-derivable diff, the published change record, and a
manifest naming every file with its SHA-256. `verify` recomputes all of it from a clean
clone with no store and no network.

Three design rules, and each one exists because its opposite is the failure this repository
keeps finding elsewhere:

1. **Export fails closed, and writes nothing when it fails.** A bundle whose baseline has
   been pruned is half an argument; exporting it would put the operator's word back at the
   centre of the thing that exists to remove it. Every read happens before the first byte is
   written, so a refusal leaves no directory behind.

2. **A check that could not run reports `skipped`, and `skipped` is never a pass.** The
   cross-check against a published feed cannot run without a feed to check against, and the
   published-excerpt check cannot run when the excerpt carries a re-normalization note that
   is not a function of the two texts. Both say so, by name, in the output. Neither is
   silently dropped, and neither is counted as verified.

3. **The outcome vocabulary is closed and every check reports into it.** `verify` returns
   one result per check name in :data:`CHECK_NAMES`, always — a check that was not reached
   because an earlier one failed is `skipped` with the reason, not absent. A check that
   simply disappears from a report is indistinguishable from a check that passed, which is
   how a gate stops being one.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord
from id_churn_sentinel.core.detect import DIFF_CONTEXT_LINES, diff_excerpt
from id_churn_sentinel.core.normalize import (
    EXTRACTOR_VERSION,
    NORMALIZER_VERSION,
    content_evidence,
    passages,
    representation_contract,
)
from id_churn_sentinel.core.store import FetchAttempt, Snapshot, SnapshotStore
from id_churn_sentinel.errors import SentinelError

__all__ = [
    "BUNDLE_MANIFEST_VERSION",
    "CHECK_FAILED",
    "CHECK_NAMES",
    "CHECK_OK",
    "CHECK_OUTCOMES",
    "CHECK_SKIPPED",
    "EXIT_MISMATCH",
    "EXIT_UNREADABLE",
    "EXIT_VERIFIED",
    "BundleError",
    "BundleUnreadableError",
    "BundleVerification",
    "CheckResult",
    "export_bundle",
    "render_verification",
    "verify_bundle",
]

#: The manifest schema this module writes and reads. `docs/schema/evidence-bundle-v1.schema.json`.
BUNDLE_MANIFEST_VERSION = "1.0"

MANIFEST_NAME = "manifest.json"
CHANGE_NAME = "change.json"
DIFF_NAME = "diff.patch"
ATTEMPTS_NAME = "fetch-attempts.json"

#: `before` is the baseline the change was detected against; `after` is what replaced it.
SIDES = ("before", "after")

#: Which regime the source's detection hash was taken under. Not a guess: it is re-derived
#: from the retained snapshot at export time by finding which one reproduces the stored
#: hash, and re-derived again at verify time from the bundle's own bytes.
REGIME_NORMALIZED_TEXT = "normalized-text"
REGIME_RAW_BYTES = "raw-bytes"

EXIT_VERIFIED = 0
EXIT_MISMATCH = 1
EXIT_UNREADABLE = 2

CHECK_OK = "ok"
CHECK_FAILED = "failed"
CHECK_SKIPPED = "skipped"

#: The closed outcome vocabulary. Every check reports exactly one of these.
CHECK_OUTCOMES = frozenset({CHECK_OK, CHECK_FAILED, CHECK_SKIPPED})

#: Every check `verify` performs, in the order it performs them. The result always carries
#: one entry per name — see rule 3 in the module docstring.
CHECK_NAMES = (
    "manifest",
    "file-hashes",
    "bundle-inventory",
    "representation-contract",
    "renormalization",
    "hash-binding",
    "diff",
    "published-excerpt",
    "published-feed",
)


class BundleError(SentinelError):
    """An evidence bundle could not be exported, or could not be read at all."""


class BundleUnreadableError(BundleError):
    """The bundle is not a bundle: a missing, unparseable or structurally absent manifest.

    Kept apart from a mismatch on purpose. "These bytes do not match their hashes" is a
    finding about the evidence; "there is nothing here to check" is a finding about the
    handover, and a verifier that reported them with the same exit code would let a truncated
    copy read as a tampered one.
    """


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One named check and its outcome. `detail` is always populated, including on success."""

    name: str
    outcome: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.outcome == CHECK_OK

    def to_dict(self) -> dict[str, str]:
        return {"check": self.name, "outcome": self.outcome, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class BundleVerification:
    """The full outcome of `verify`, as a partition over :data:`CHECK_NAMES`."""

    bundle: Path
    checks: tuple[CheckResult, ...]

    @property
    def failed(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if check.outcome == CHECK_FAILED)

    @property
    def skipped(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if check.outcome == CHECK_SKIPPED)

    @property
    def exit_code(self) -> int:
        if any(check.name == "manifest" and check.outcome == CHECK_FAILED for check in self.checks):
            return EXIT_UNREADABLE
        return EXIT_MISMATCH if self.failed else EXIT_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle": str(self.bundle),
            "exit_code": self.exit_code,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class ExportResult:
    """What `export` wrote, so a caller can report it without re-reading the directory."""

    bundle: Path
    change_id: str
    files: tuple[str, ...]


# ---------------------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------------------


def export_bundle(
    store: SnapshotStore,
    change_id: str,
    out_dir: Path,
    *,
    now: datetime | None = None,
) -> ExportResult:
    """Write the evidence bundle for one change, or refuse and write nothing.

    Every read of the store happens before the first write, so a refusal cannot leave a
    partial bundle on disk for somebody to hand over by mistake.
    """

    change = store.get_change(change_id)
    before, after = _sides_for(store, change)
    attempts = _attempts_by_raw_hash(store, change.source_id)
    payload = _bundle_payload(
        change,
        before=before,
        after=after,
        attempts=attempts,
        exported_at=now or datetime.now(UTC),
    )
    _write_bundle(out_dir, payload)
    return ExportResult(bundle=out_dir, change_id=change.id, files=tuple(sorted(payload)))


def _sides_for(store: SnapshotStore, change: ChangeRecord) -> tuple[Snapshot, Snapshot]:
    """The retained snapshots behind a change, or a refusal naming which side is missing.

    A removal escalation is refused first and separately. It has no `after` bytes because
    nothing answered — that is the whole observation — so "the after snapshot is missing" is
    a true sentence that would tell the operator the wrong thing about why.
    """

    if change.kind is ChangeKind.POSSIBLY_REMOVED:
        raise BundleError(
            f"change {change.id} is a removal escalation: the source did not answer, so there "
            f"are no `after` bytes to export. Its evidence is the fetch-attempt record, which "
            f"`sentinel diff {change.id}` prints."
        )
    retained = store.snapshots(change.source_id)
    before = _snapshot_with_hash(retained, change.previous_hash)
    after = _snapshot_with_hash(retained, change.new_hash)
    if before is None or after is None:
        missing = [name for name, snap in (("before", before), ("after", after)) if snap is None]
        raise BundleError(
            f"change {change.id}: the {' and '.join(missing)} snapshot(s) are no longer in the "
            f"store — retention keeps the newest {len(retained)} for {change.source_id} and "
            f"these were pruned. A bundle carrying one side is not evidence, so nothing was "
            f"written. Export at review time to pin the bytes."
        )
    return before, after


def _snapshot_with_hash(retained: Iterable[Snapshot], content_sha256: str) -> Snapshot | None:
    if not content_sha256:
        return None
    for snapshot in retained:
        if snapshot.content_sha256 == content_sha256:
            return snapshot
    return None


def _attempts_by_raw_hash(store: SnapshotStore, source_id: str) -> dict[str, FetchAttempt]:
    """Fetch receipts for one source, keyed by the raw hash of the body they recorded.

    The raw hash is the join: `snapshots` does not carry a run id, and the content type a
    body was read under lives only on the attempt. Newest wins, which matters not at all —
    two attempts holding the same bytes recorded the same content type.
    """

    return {
        attempt.raw_sha256: attempt
        for attempt in store.fetch_attempts_for_source(source_id)
        if attempt.raw_sha256
    }


def _hashing_regime(snapshot: Snapshot) -> str:
    """Which hashing regime reproduces this snapshot's stored detection hash.

    Derived rather than assumed. `content_hash` hashes normalized text for text/HTML and raw
    bytes for anything binary, and the snapshot table records the answer without recording
    which question it answered.
    """

    if _sha256_text(snapshot.normalized_text) == snapshot.content_sha256:
        return REGIME_NORMALIZED_TEXT
    if _sha256_bytes(snapshot.raw_bytes) == snapshot.content_sha256:
        return REGIME_RAW_BYTES
    raise BundleError(
        f"snapshot {snapshot.snapshot_id} of {snapshot.source_id} holds a detection hash that "
        f"neither its raw bytes nor its normalized text reproduce. The store is inconsistent "
        f"with itself; nothing was written."
    )


def _side_manifest(
    side: str,
    snapshot: Snapshot,
    attempts: Mapping[str, FetchAttempt],
) -> dict[str, Any]:
    raw_sha256 = _sha256_bytes(snapshot.raw_bytes)
    attempt = attempts.get(raw_sha256)
    if attempt is None or not attempt.content_type:
        raise BundleError(
            f"change evidence for the `{side}` side cannot be exported: no fetch receipt in "
            f"the store records these bytes (raw SHA-256 {raw_sha256[:12]}…) with the content "
            f"type they were read under, so a verifier could not re-run normalization over "
            f"them. A bundle whose bytes cannot be re-normalized is an archive, not evidence. "
            f"Nothing was written."
        )
    return {
        "content_sha256": snapshot.content_sha256,
        "hashing_regime": _hashing_regime(snapshot),
        "content_type": attempt.content_type,
        "normalizer_version": snapshot.normalizer_version,
        "extractor_version": snapshot.extractor_version,
        "representation_contract": representation_contract(
            snapshot.normalizer_version, snapshot.extractor_version
        ),
        "fetched_at": snapshot.fetched_at.isoformat(),
        "http_status": snapshot.http_status,
        "raw_path": f"{side}/raw.bin",
        "normalized_path": f"{side}/normalized.txt",
    }


def _attempt_payload(attempt: FetchAttempt) -> dict[str, Any]:
    """The public projection of a fetch receipt.

    The fetcher's free-text `error` is deliberately absent: no published artifact carries it,
    and a bundle is a thing an operator hands to a third party. `error_class` is a closed
    vocabulary and says the same thing a reader of this bundle is entitled to know.
    """

    return {
        "run_id": attempt.run_id,
        "url": attempt.url,
        "attempted_at": attempt.attempted_at.isoformat(),
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "ok": attempt.ok,
        "http_status": attempt.http_status,
        "content_type": attempt.content_type,
        "final_url": attempt.final_url,
        "redirect_chain": [
            {"status": hop.status, "url": hop.url} for hop in attempt.redirect_chain
        ],
        "raw_sha256": attempt.raw_sha256,
        "normalized_sha256": attempt.normalized_sha256,
        "bytes_received": attempt.bytes_received,
        "byte_limit": attempt.byte_limit,
        "truncated": attempt.truncated,
        "extraction_outcome": attempt.extraction_outcome,
        "error_class": attempt.error_class,
        "normalizer_version": attempt.normalizer_version,
        "extractor_version": attempt.extractor_version,
    }


def _bundle_payload(
    change: ChangeRecord,
    *,
    before: Snapshot,
    after: Snapshot,
    attempts: Mapping[str, FetchAttempt],
    exported_at: datetime,
) -> dict[str, bytes]:
    """Every file the bundle will contain, in memory, before anything is written."""

    sides = {
        "before": _side_manifest("before", before, attempts),
        "after": _side_manifest("after", after, attempts),
    }
    full_diff = full_diff_text(before.normalized_text, after.normalized_text)
    rederived_excerpt = diff_excerpt(
        before.normalized_text,
        after.normalized_text,
        source_url=change.url,
        binary=sides["after"]["hashing_regime"] == REGIME_RAW_BYTES,
    )
    excerpt_rederivable = rederived_excerpt == change.diff_excerpt
    side_attempts = [
        _attempt_payload(attempts[key])
        for key in (_sha256_bytes(before.raw_bytes), _sha256_bytes(after.raw_bytes))
    ]

    files: dict[str, bytes] = {
        CHANGE_NAME: _json_bytes(change.to_dict()),
        DIFF_NAME: full_diff.encode("utf-8"),
        ATTEMPTS_NAME: _json_bytes({"attempts": side_attempts}),
        "before/raw.bin": before.raw_bytes,
        "before/normalized.txt": before.normalized_text.encode("utf-8"),
        "after/raw.bin": after.raw_bytes,
        "after/normalized.txt": after.normalized_text.encode("utf-8"),
    }
    manifest: dict[str, Any] = {
        "manifest_version": BUNDLE_MANIFEST_VERSION,
        "tool": "id-churn-sentinel",
        "exported_at": exported_at.isoformat(),
        "change_id": change.id,
        "source_id": change.source_id,
        "jurisdiction": change.jurisdiction,
        "document_class": change.document_class,
        "url": change.url,
        "observed_at": change.observed_at.isoformat(),
        "previous_hash": change.previous_hash,
        "new_hash": change.new_hash,
        "sides": sides,
        # Measured, not asserted. `diff_excerpt` prepends a provenance note when a baseline
        # had to be re-derived under a different contract, and truncates at a character
        # budget; neither is a function of the two texts alone. So export re-derives the
        # excerpt here and records whether it came out identical. When it did not, `verify`
        # reports that check as SKIPPED with this reason rather than failing an honest bundle
        # or, worse, passing one it never checked.
        "published_excerpt_is_rederivable": excerpt_rederivable,
        "published_excerpt_note": (
            ""
            if excerpt_rederivable
            else (
                "the published excerpt is not a pure function of the two normalized texts "
                "(a re-normalization note or a truncation budget is part of it), so it is "
                "not re-derived by `verify`"
            )
        ),
        "change_path": CHANGE_NAME,
        "diff_path": DIFF_NAME,
        "fetch_attempts_path": ATTEMPTS_NAME,
        "files": [
            {"path": path, "sha256": _sha256_bytes(body), "bytes": len(body)}
            for path, body in sorted(files.items())
        ],
    }
    return {MANIFEST_NAME: _json_bytes(manifest), **files}


def _write_bundle(out_dir: Path, payload: Mapping[str, bytes]) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise BundleError(
            f"{out_dir} already exists and is not empty. Export refuses to write into a "
            f"populated directory: a bundle is verified as a whole, and a half-overwritten "
            f"one would verify as a whole too."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, body in sorted(payload.items()):
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)


# ---------------------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------------------


def verify_bundle(bundle: Path, *, changes_path: Path | None = None) -> BundleVerification:
    """Recompute every claim a bundle makes about itself, offline, with no store."""

    try:
        manifest = _read_manifest(bundle)
    except BundleUnreadableError as exc:
        return _short_circuit(bundle, CheckResult("manifest", CHECK_FAILED, str(exc)))

    checks = [CheckResult("manifest", CHECK_OK, f"{MANIFEST_NAME} v{manifest['manifest_version']}")]
    hashes = _check_file_hashes(bundle, manifest)
    checks.append(hashes)
    checks.append(_check_inventory(bundle, manifest))
    if not hashes.ok:
        return _short_circuit_after(bundle, checks, reason="file-hashes failed")

    checks.append(_check_contract(manifest))
    checks.append(_check_renormalization(bundle, manifest))
    checks.append(_check_hash_binding(bundle, manifest))
    checks.append(_check_diff(bundle, manifest))
    checks.append(_check_published_excerpt(bundle, manifest))
    checks.append(_check_published_feed(bundle, manifest, changes_path))
    return BundleVerification(bundle=bundle, checks=tuple(checks))


def _short_circuit(bundle: Path, failure: CheckResult) -> BundleVerification:
    return _short_circuit_after(bundle, [failure], reason=f"{failure.name} failed")


def _short_circuit_after(
    bundle: Path, done: list[CheckResult], *, reason: str
) -> BundleVerification:
    """Fill the rest of the vocabulary with `skipped`, never with silence.

    A report that simply stops listing checks after the first failure reads, to anything that
    counts outcomes, exactly like a report in which those checks passed.
    """

    seen = {check.name for check in done}
    remaining = [
        CheckResult(name, CHECK_SKIPPED, f"not reached: {reason}")
        for name in CHECK_NAMES
        if name not in seen
    ]
    return BundleVerification(bundle=bundle, checks=tuple(done) + tuple(remaining))


def _read_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / MANIFEST_NAME
    if not path.is_file():
        raise BundleUnreadableError(f"{path} is missing: this directory is not an evidence bundle")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleUnreadableError(f"{path} could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise BundleUnreadableError(f"{path} is not a JSON object")
    missing = [
        key for key in ("manifest_version", "files", "sides", "change_id") if key not in loaded
    ]
    if missing:
        raise BundleUnreadableError(f"{path} is missing required key(s): {', '.join(missing)}")
    if loaded["manifest_version"] != BUNDLE_MANIFEST_VERSION:
        raise BundleUnreadableError(
            f"{path} declares manifest version {loaded['manifest_version']!r}; this build reads "
            f"{BUNDLE_MANIFEST_VERSION!r} and will not guess at another"
        )
    return dict(loaded)


def _check_file_hashes(bundle: Path, manifest: Mapping[str, Any]) -> CheckResult:
    """Every file the manifest lists, recomputed. The FIRST failure is named, in listed order."""

    entries = manifest["files"]
    if not entries:
        return CheckResult("file-hashes", CHECK_FAILED, "the manifest lists no files")
    for entry in entries:
        path = bundle / str(entry["path"])
        if not path.is_file():
            return CheckResult(
                "file-hashes", CHECK_FAILED, f"{entry['path']}: listed in the manifest, missing"
            )
        body = path.read_bytes()
        actual = _sha256_bytes(body)
        if actual != entry["sha256"]:
            return CheckResult(
                "file-hashes",
                CHECK_FAILED,
                f"{entry['path']}: SHA-256 is {actual}, manifest says {entry['sha256']}",
            )
        if len(body) != entry["bytes"]:
            return CheckResult(
                "file-hashes",
                CHECK_FAILED,
                f"{entry['path']}: {len(body)} bytes, manifest says {entry['bytes']}",
            )
    return CheckResult("file-hashes", CHECK_OK, f"{len(entries)} file(s) match the manifest")


def _check_inventory(bundle: Path, manifest: Mapping[str, Any]) -> CheckResult:
    """Nothing in the bundle is unaccounted for.

    Hash-checking only the listed files leaves a file nobody listed sitting in the handover,
    verified by nothing and read by whoever opens the directory.
    """

    listed = {str(entry["path"]) for entry in manifest["files"]} | {MANIFEST_NAME}
    present = {
        str(path.relative_to(bundle).as_posix()) for path in bundle.rglob("*") if path.is_file()
    }
    unlisted = sorted(present - listed)
    if unlisted:
        return CheckResult(
            "bundle-inventory",
            CHECK_FAILED,
            f"{len(unlisted)} file(s) in the bundle that the manifest does not list: {unlisted}",
        )
    return CheckResult("bundle-inventory", CHECK_OK, f"{len(present)} file(s), all listed")


def _check_contract(manifest: Mapping[str, Any]) -> CheckResult:
    """Fail closed on a contract this build does not implement, naming the version.

    Re-running normalization under a normalizer version this build does not have is not
    something to attempt on a best-effort basis: the answer would be produced by a different
    normalizer than the one that produced the evidence, and would be reported as agreement.
    """

    unknown = []
    for side in SIDES:
        recorded = manifest["sides"][side]
        if recorded["normalizer_version"] != NORMALIZER_VERSION:
            unknown.append(f"{side}: normalizer {recorded['normalizer_version']!r}")
        if recorded["extractor_version"] != EXTRACTOR_VERSION:
            unknown.append(f"{side}: extractor {recorded['extractor_version']!r}")
    if unknown:
        return CheckResult(
            "representation-contract",
            CHECK_FAILED,
            f"this build implements {representation_contract(NORMALIZER_VERSION, EXTRACTOR_VERSION)}"
            f" and cannot re-derive under — {'; '.join(unknown)}",
        )
    return CheckResult(
        "representation-contract",
        CHECK_OK,
        representation_contract(NORMALIZER_VERSION, EXTRACTOR_VERSION),
    )


def _check_renormalization(bundle: Path, manifest: Mapping[str, Any]) -> CheckResult:
    """Re-run normalization over the raw bytes and compare with the committed text."""

    for side in SIDES:
        recorded = manifest["sides"][side]
        raw = (bundle / str(recorded["raw_path"])).read_bytes()
        stored_text = (bundle / str(recorded["normalized_path"])).read_text(encoding="utf-8")
        evidence = content_evidence(raw, str(recorded["content_type"]))
        if evidence.normalized_text != stored_text:
            return CheckResult(
                "renormalization",
                CHECK_FAILED,
                f"{recorded['normalized_path']}: re-normalizing {recorded['raw_path']} under "
                f"{recorded['representation_contract']} does not reproduce the committed text",
            )
    return CheckResult("renormalization", CHECK_OK, "both sides re-normalize to the stored text")


def _check_hash_binding(bundle: Path, manifest: Mapping[str, Any]) -> CheckResult:
    """The bytes hash to the hashes the PUBLISHED record cites. This is the tie to the feed.

    Everything above proves the bundle is internally consistent. Only this proves it is a
    bundle about *that* change: `previous_hash` and `new_hash` are what `changes.json` says,
    and they are recomputed here from the raw bytes under the recorded regime.
    """

    published = {"before": manifest["previous_hash"], "after": manifest["new_hash"]}
    for side in SIDES:
        recorded = manifest["sides"][side]
        raw = (bundle / str(recorded["raw_path"])).read_bytes()
        text = (bundle / str(recorded["normalized_path"])).read_text(encoding="utf-8")
        regime = str(recorded["hashing_regime"])
        if regime == REGIME_NORMALIZED_TEXT:
            actual = _sha256_text(text)
        elif regime == REGIME_RAW_BYTES:
            actual = _sha256_bytes(raw)
        else:
            return CheckResult(
                "hash-binding",
                CHECK_FAILED,
                f"{side}: unknown hashing regime {regime!r}",
            )
        if actual != published[side] or actual != recorded["content_sha256"]:
            return CheckResult(
                "hash-binding",
                CHECK_FAILED,
                f"{recorded['raw_path']}: hashes to {actual} under {regime}; the change record "
                f"cites {published[side]}",
            )
    return CheckResult(
        "hash-binding", CHECK_OK, "both sides hash to the values the change record cites"
    )


def _check_diff(bundle: Path, manifest: Mapping[str, Any]) -> CheckResult:
    before = (bundle / str(manifest["sides"]["before"]["normalized_path"])).read_text(
        encoding="utf-8"
    )
    after = (bundle / str(manifest["sides"]["after"]["normalized_path"])).read_text(
        encoding="utf-8"
    )
    committed = (bundle / str(manifest["diff_path"])).read_text(encoding="utf-8")
    rederived = full_diff_text(before, after)
    if rederived != committed:
        return CheckResult(
            "diff",
            CHECK_FAILED,
            f"{manifest['diff_path']}: re-deriving the unified diff from the two normalized "
            f"texts does not reproduce the committed patch",
        )
    return CheckResult("diff", CHECK_OK, f"{manifest['diff_path']} re-derives from both texts")


def _check_published_excerpt(bundle: Path, manifest: Mapping[str, Any]) -> CheckResult:
    """Re-derive the excerpt the feed publishes — when it is a function of the two texts."""

    if not manifest.get("published_excerpt_is_rederivable", False):
        return CheckResult(
            "published-excerpt",
            CHECK_SKIPPED,
            str(manifest.get("published_excerpt_note", ""))
            or "the exporter recorded this excerpt as not re-derivable",
        )
    change = _read_change(bundle, manifest)
    before = (bundle / str(manifest["sides"]["before"]["normalized_path"])).read_text(
        encoding="utf-8"
    )
    after = (bundle / str(manifest["sides"]["after"]["normalized_path"])).read_text(
        encoding="utf-8"
    )
    rederived = diff_excerpt(
        before,
        after,
        source_url=str(manifest["url"]),
        binary=manifest["sides"]["after"]["hashing_regime"] == REGIME_RAW_BYTES,
    )
    if rederived != change.get("diff_excerpt"):
        return CheckResult(
            "published-excerpt",
            CHECK_FAILED,
            f"{manifest['change_path']}: the published excerpt is not what these two texts produce",
        )
    return CheckResult(
        "published-excerpt", CHECK_OK, "the published excerpt re-derives from both texts"
    )


def _check_published_feed(
    bundle: Path, manifest: Mapping[str, Any], changes_path: Path | None
) -> CheckResult:
    """Compare the bundle's change record with the one a published feed serves.

    Without a feed to compare against there is nothing to check, and this says so. It does
    not report agreement with a document it never opened.
    """

    if changes_path is None:
        return CheckResult(
            "published-feed",
            CHECK_SKIPPED,
            "no --changes document given, so the bundle was not compared with any published "
            "feed; pass the changes.json a consumer fetched to close that gap",
        )
    try:
        document = json.loads(changes_path.read_text(encoding="utf-8"))
        published = [
            item for item in document.get("changes", []) if item.get("id") == manifest["change_id"]
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        return CheckResult("published-feed", CHECK_FAILED, f"{changes_path}: unreadable ({exc})")
    if not published:
        return CheckResult(
            "published-feed",
            CHECK_FAILED,
            f"{changes_path} serves no change with id {manifest['change_id']}",
        )
    change = _read_change(bundle, manifest)
    differing = sorted(
        key for key in change if key in published[0] and published[0][key] != change[key]
    )
    if differing:
        return CheckResult(
            "published-feed",
            CHECK_FAILED,
            f"the bundle's record differs from {changes_path} on: {differing}",
        )
    return CheckResult(
        "published-feed", CHECK_OK, f"the record matches the entry served by {changes_path}"
    )


def _read_change(bundle: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    loaded = json.loads((bundle / str(manifest["change_path"])).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise BundleUnreadableError(f"{manifest['change_path']} is not a JSON object")
    return dict(loaded)


def render_verification(result: BundleVerification) -> str:
    """The human report. A skipped check is printed as SKIPPED with its reason, never elided."""

    lines = [f"evidence bundle: {result.bundle}"]
    for check in result.checks:
        lines.append(f"  [{check.outcome.upper():<8}] {check.name}: {check.detail}")
    if result.failed:
        lines.append(f"NOT VERIFIED — {len(result.failed)} check(s) failed.")
    elif result.skipped:
        names = ", ".join(check.name for check in result.skipped)
        lines.append(
            f"VERIFIED, with {len(result.skipped)} check(s) NOT RUN ({names}). A check that did "
            f"not run is not a check that passed."
        )
    else:
        lines.append("VERIFIED — every check ran and passed.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# shared derivations
# ---------------------------------------------------------------------------------------


def full_diff_text(previous_text: str, current_text: str) -> str:
    """The complete unified diff of the normalized passages — untruncated, unannotated.

    Deliberately not `detect.diff_excerpt`: that one is written for a reviewer's screen and
    carries provenance notes and a character budget. This is written to be recomputed.
    """

    return "\n".join(
        difflib.unified_diff(
            passages(previous_text),
            passages(current_text),
            fromfile="previous",
            tofile="current",
            lineterm="",
            n=DIFF_CONTEXT_LINES,
        )
    )


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
