"""The committed baseline — what every watched page hashed to, checked into the repo.

The SQLite snapshot store in `var/` is the tool's memory, and it is deliberately *not*
committed: it holds megabytes of retained government HTML that grows every week, and a git
history is the wrong place for it. But that leaves a hole. On a clean checkout the store is
empty, every source is a first sighting, every first sighting is a baseline rather than a
change (correctly — see `core/detect.py`), and the tool therefore **cannot tell you that
anything has moved**. It has to watch for a week before it can say anything at all.

That is a bad property for a repo whose whole claim is "we can tell you what went stale."

So the hashes — and only the hashes — are committed, exactly as
`trans-docs-navigator/corpus/source-hashes.json` commits them. A clean checkout can then
run :func:`check_baselines`, fetch each source once, and answer "which of these pages is
not what it was when this baseline was taken?" with no store, no history, and no week of
waiting.

**What this is not.** It is not the snapshot store and it is not a substitute for one. A
hash tells you *that* a page moved; it cannot tell you *what* moved, because the text it
was computed from is not here. `sentinel watch` — which retains the bytes — is what
produces a reviewable passage diff, and it remains the thing that feeds the human review
gate. This file is the cheap, portable, auditable answer to a narrower question, and the
honest limit is stated in the file itself.

**And it is not a claim about the law.** A baseline hash records what a URL served on a
date. Nothing more.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from id_churn_sentinel.core.fetch import Fetcher
from id_churn_sentinel.core.normalize import content_hash
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import SnapshotStore
from id_churn_sentinel.errors import RegistryError

__all__ = [
    "BASELINE_VERSION",
    "BaselineReport",
    "check_baselines",
    "default_baseline_path",
    "load_baselines",
    "write_baselines",
]

BASELINE_VERSION = "1.0"

_README = [
    "COMMITTED BASELINE HASHES — the sha256 of the NORMALIZED TEXT each watched source",
    "served, on the date given. Mirrors trans-docs-navigator/corpus/source-hashes.json.",
    "",
    "WHY THIS FILE EXISTS. The snapshot store (var/sentinel.db) is not committed — it holds",
    "megabytes of retained government HTML and grows every week. Without these hashes, a",
    "clean checkout has no memory at all: every source is a first sighting, a first sighting",
    "is a baseline and not a change, and the tool cannot tell you that anything moved until",
    "it has watched for a week. With them, `sentinel baseline check` answers 'which of these",
    "pages is not what it was?' in one pass, from a fresh clone, with no store.",
    "",
    "WHAT A HASH IS AND IS NOT. It is the sha256 of the normalized text (markup stripped,",
    "whitespace collapsed, lowercased — see core/normalize.py), so a cosmetic re-deploy does",
    "not move it. It records what a URL served on a date. It is NOT a claim about the law, it",
    "is NOT a human verification of the URL (see `verified` in registry.json, which is still",
    "false for every entry), and it canNOT produce a diff: the text it was computed from is",
    "not in this file. `sentinel watch`, which retains the bytes, is what produces the",
    "reviewable passage diff that a human actually reviews.",
    "",
    "A SOURCE WE CANNOT FETCH HAS NO BASELINE, and is listed under `unreachable` rather than",
    "given a fake one. A hash we did not observe is not a hash.",
    "",
    "REGENERATE: `sentinel watch && sentinel baseline write`. Never hand-edit a hash — a",
    "hand-edited baseline is a claim that a page said something it may never have said.",
]


@dataclass(slots=True)
class BaselineReport:
    """What one `baseline check` pass saw, against the committed hashes."""

    matched: list[str] = field(default_factory=list)
    moved: list[tuple[str, str, str]] = field(default_factory=list)
    unbaselined: list[str] = field(default_factory=list)
    unreachable: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.moved) + len(self.unbaselined) + len(self.unreachable)

    def summary(self) -> str:
        return (
            f"{self.total} source(s): {len(self.matched)} match the committed baseline, "
            f"{len(self.moved)} MOVED, {len(self.unbaselined)} have no committed baseline, "
            f"{len(self.unreachable)} unreachable (not drift)"
        )


def default_baseline_path() -> Path:
    """`sources/baseline-hashes.json`, alongside the registry it mirrors."""
    return Path(__file__).resolve().parents[3] / "sources" / "baseline-hashes.json"


def write_baselines(
    store: SnapshotStore,
    registry: Registry,
    path: Path,
    *,
    now: datetime | None = None,
) -> int:
    """Export the store's latest hash per source into the committed baseline file.

    Only sources the store has actually *seen* get a hash. A source that has never been
    fetched successfully — `ssa.gov`, which 403s us — is recorded by name under
    `unreachable`, with no hash, because inventing one would be a lie the rest of the
    pipeline would faithfully propagate.
    """
    baselines: dict[str, dict[str, str]] = {}
    unreachable: list[str] = []
    for source in registry.sources:
        snapshot = store.latest_snapshot(source.id)
        if snapshot is None:
            unreachable.append(source.id)
            continue
        baselines[source.id] = {
            "url": snapshot.url,
            "sha256": snapshot.content_sha256,
            "observed_at": snapshot.fetched_at.isoformat(),
        }

    payload = {
        "baseline_version": BASELINE_VERSION,
        "_README": _README,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "registry_sources": len(registry),
        "unreachable": sorted(unreachable),
        "baselines": dict(sorted(baselines.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(baselines)


def load_baselines(path: Path | None = None) -> dict[str, str]:
    """Load the committed baseline file as `{source_id: sha256}`.

    Validated on the way in, and loudly: a malformed baseline file is worse than none,
    because it would silently compare a live page against nonsense and report drift that
    never happened.
    """
    baseline_path = path or default_baseline_path()
    try:
        raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"baseline file not found: {baseline_path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"baseline file is not valid JSON: {baseline_path}: {exc}") from exc

    if not isinstance(raw, dict) or raw.get("baseline_version") != BASELINE_VERSION:
        raise RegistryError(
            f"baseline_version {raw.get('baseline_version') if isinstance(raw, dict) else None!r} "
            f"is not the supported {BASELINE_VERSION!r}"
        )
    entries = raw.get("baselines")
    if not isinstance(entries, dict):
        raise RegistryError("baseline file: `baselines` must be an object")

    loaded: dict[str, str] = {}
    for source_id, entry in entries.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise RegistryError(f"baseline file: {source_id!r} has no sha256")
        loaded[source_id] = entry["sha256"]
    return loaded


def check_baselines(
    sources: Iterable[Source],
    fetcher: Fetcher,
    baselines: dict[str, str],
) -> BaselineReport:
    """Fetch each source once and compare it against the committed baseline hash.

    The same disciplines apply here as in `watch()`, for the same reasons: **a fetch failure
    is never drift** (an unreachable source is reported as unreachable and nothing is
    concluded from it), and **nothing is classified** (a moved hash is a fact about bytes,
    and what it means is a human's call).

    What this cannot do is show you the passage that changed — the previous text is not in
    the baseline file, only its hash. `sentinel watch` is the command that answers that, and
    the report says so rather than pretending.
    """
    report = BaselineReport()
    for source in sources:
        result = fetcher.fetch(source.url)
        if not result.ok:
            report.unreachable.append((source.id, result.error or "unknown error"))
            continue

        committed = baselines.get(source.id)
        if committed is None:
            report.unbaselined.append(source.id)
            continue

        current, _ = content_hash(result.body, result.content_type)
        if current == committed:
            report.matched.append(source.id)
        else:
            report.moved.append((source.id, committed, current))
    return report
