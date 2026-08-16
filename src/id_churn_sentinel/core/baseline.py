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

**One consequence of holding only hashes.** A hash means nothing except relative to the
normalizer that produced it, and this file has no bytes to re-normalize — so when the
normalizer version moves, `sentinel watch` can re-derive its baselines and this command
structurally cannot. It therefore answers the same problem differently, and deliberately:
it **labels** rather than refuses. Refusing every hash recorded under an older contract
would leave a clean checkout unable to say anything at all, which is the exact hole this
file exists to fill — so a MOVED hash across a contract boundary is still reported, and is
reported *as* a comparison that may be measuring our normalizer rather than the page, on
the specific sources it applies to and nowhere else. The remedy is one command
(`sentinel watch && sentinel baseline write`), and the report names it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from id_churn_sentinel.core.fetch import Fetcher
from id_churn_sentinel.core.normalize import (
    CURRENT_CONTRACT,
    UNRECORDED_CONTRACT,
    ContentKind,
    content_hash,
    kind_for_content_type,
    passages,
    representation_contract,
)
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import SnapshotStore
from id_churn_sentinel.errors import RegistryError

__all__ = [
    "BASELINE_VERSION",
    "EMPTY_CONTENT_SHA256",
    "BaselineEntry",
    "BaselineReport",
    "BaselineWriteReport",
    "check_baselines",
    "default_baseline_path",
    "load_baselines",
    "write_baselines",
]

BASELINE_VERSION = "1.0"

# The sha256 of nothing at all. It is a perfectly valid digest and that is exactly the
# problem: it is what a JS shell, an empty 200 and a bot-wall all hash to once markup and
# scripts are stripped, so two unrelated blind sources share it (issue #19). Committed as a
# baseline it would make `sentinel baseline check` report a page nobody can read as one that
# "matches the committed baseline" — the most reassuring sentence this file can print, about
# the one condition it must never print it for. A hash of nothing is not a baseline.
EMPTY_CONTENT_SHA256 = hashlib.sha256(b"").hexdigest()

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
    "A SOURCE THAT ANSWERS WITH NO READABLE TEXT ALSO HAS NO BASELINE, and is listed under",
    "`unmeasurable`. sha256 of an empty normalized text is a real digest that every blind",
    "page shares — a JS shell, an empty 200, a bot-wall — so committing it would make",
    "`baseline check` report a page nobody can read as one that MATCHES. Two different facts,",
    "two different lists: `unreachable` means nothing answered; `unmeasurable` means",
    "something answered and there was nothing in it.",
    "",
    "EVERY HASH CARRIES THE NORMALIZER THAT PRODUCED IT (`normalizer_version` /",
    "`extractor_version`). A hash is only comparable against another hash from the same pair,",
    "and this file holds no bytes to re-normalize — so `sentinel baseline check` reports a",
    "cross-version comparison AS one, rather than presenting a normalization artifact as",
    "drift. An entry with no versions recorded predates this field. Either way the fix is",
    "the same: `sentinel watch && sentinel baseline write`.",
    "",
    "REGENERATE: `sentinel watch && sentinel baseline write`. Never hand-edit a hash — a",
    "hand-edited baseline is a claim that a page said something it may never have said.",
]


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """One committed hash, with the representation contract that produced it.

    The contract is carried rather than assumed because assuming it is the bug: two hashes
    from different normalizers are not comparable, and a file that records only the hash
    makes that impossible to notice. `UNRECORDED_CONTRACT` is what an entry written before
    this field existed loads as — an honest "we cannot tell", not a guess at v1.
    """

    sha256: str
    contract: str = UNRECORDED_CONTRACT


@dataclass(frozen=True, slots=True)
class BaselineWriteReport:
    """What `baseline write` put in the file, and what it refused to.

    Three counts rather than one, because the caller has to be able to say *why* a source
    carries no hash. "146 of 152 written" alone reads as a shortfall of the same kind in every
    case; it is not. `unreachable` is a host that never answered and `unmeasurable` is a host
    that answered with nothing readable, and an operator chasing the first when it is the
    second is looking at the wrong end of the wire.
    """

    written: int
    unreachable: int
    unmeasurable: int


@dataclass(slots=True)
class BaselineReport:
    """What one `baseline check` pass saw, against the committed hashes.

    `no_text` is not a drift bucket and not a match bucket (issue #19). A text/HTML source
    that fetches to zero passages has not been compared against anything: the comparison
    would be between the hash of nothing and whatever is committed, which answers no question
    a reader is asking. It is reported on its own, and it is deliberately *not* folded into
    `matched` — the case that made this necessary is a page whose committed baseline is
    itself a hash of nothing, where folding would print "matches the committed baseline" for
    a source nobody can read.
    """

    matched: list[str] = field(default_factory=list)
    moved: list[tuple[str, str, str]] = field(default_factory=list)
    unbaselined: list[str] = field(default_factory=list)
    no_text: list[tuple[str, str]] = field(default_factory=list)
    unreachable: list[tuple[str, str]] = field(default_factory=list)
    #: `source_id → the contract its committed hash was recorded under`, for the MOVED
    #: sources only, and only where that contract is not the one this build computes under.
    #: Never a bucket of its own: a MOVED hash is still reported, because refusing to report
    #: it would blind the one command a clean checkout has. It is *qualified*, not withheld.
    moved_across_contracts: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return (
            len(self.matched)
            + len(self.moved)
            + len(self.unbaselined)
            + len(self.no_text)
            + len(self.unreachable)
        )

    def summary(self) -> str:
        qualified = (
            f", {len(self.moved_across_contracts)} of them compared against a hash from a "
            f"different normalizer (may be a normalization artifact, not drift)"
            if self.moved_across_contracts
            else ""
        )
        no_text = (
            f", {len(self.no_text)} served NO extractable text (NOT compared, no drift claimed "
            f"either way)"
            if self.no_text
            else ""
        )
        return (
            f"{self.total} source(s): {len(self.matched)} match the committed baseline, "
            f"{len(self.moved)} MOVED{qualified}, {len(self.unbaselined)} have no committed "
            f"baseline{no_text}, {len(self.unreachable)} unreachable (not drift)"
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
) -> BaselineWriteReport:
    """Export the store's latest hash per source into the committed baseline file.

    Only sources the store has actually *seen* get a hash. A source that has never been
    fetched successfully — `ssa.gov`, which 403s us — is recorded by name under
    `unreachable`, with no hash, because inventing one would be a lie the rest of the
    pipeline would faithfully propagate.

    A source whose latest snapshot hashes to :data:`EMPTY_CONTENT_SHA256` gets no hash either,
    and is named under `unmeasurable` (issue #19). Today's watcher refuses to record such a
    snapshot at all, so this is a second, independent refusal covering stores written before
    that fix and any future writer that forgets — the same doubling the `changes` CHECK
    constraint applies to machine classification. It is a separate list from `unreachable`
    because the two facts are different: `unreachable` means nothing answered, `unmeasurable`
    means something answered and it was not readable, and a reader given one when the other is
    true will draw the wrong conclusion about which end of the pipe is broken.
    """
    baselines: dict[str, dict[str, str]] = {}
    unreachable: list[str] = []
    unmeasurable: list[str] = []
    for source in registry.sources:
        snapshot = store.latest_snapshot(source.id)
        if snapshot is None:
            unreachable.append(source.id)
            continue
        if snapshot.content_sha256 == EMPTY_CONTENT_SHA256:
            unmeasurable.append(source.id)
            continue
        baselines[source.id] = {
            "url": snapshot.url,
            "sha256": snapshot.content_sha256,
            "observed_at": snapshot.fetched_at.isoformat(),
            # Taken from the snapshot, never from this build's constants: the store may hold
            # a baseline recorded under an older normalizer, and stamping today's version on
            # yesterday's hash would fabricate exactly the provenance this field exists to
            # make checkable.
            "normalizer_version": snapshot.normalizer_version,
            "extractor_version": snapshot.extractor_version,
        }

    payload = {
        "baseline_version": BASELINE_VERSION,
        "_README": _README,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "registry_sources": len(registry),
        "unreachable": sorted(unreachable),
        "unmeasurable": sorted(unmeasurable),
        "baselines": dict(sorted(baselines.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return BaselineWriteReport(
        written=len(baselines),
        unreachable=len(unreachable),
        unmeasurable=len(unmeasurable),
    )


def load_baselines(path: Path | None = None) -> dict[str, BaselineEntry]:
    """Load the committed baseline file as `{source_id: BaselineEntry}`.

    Validated on the way in, and loudly: a malformed baseline file is worse than none,
    because it would silently compare a live page against nonsense and report drift that
    never happened.

    A missing `normalizer_version`/`extractor_version` is *not* an error and is *not*
    back-filled with a guess. The committed file predates the field, and "we do not know
    which normalizer produced this hash" is a true statement that the comparison downstream
    can act on; "it was probably v1" is a convenient one that it cannot.
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

    loaded: dict[str, BaselineEntry] = {}
    for source_id, entry in entries.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise RegistryError(f"baseline file: {source_id!r} has no sha256")
        if entry["sha256"] == EMPTY_CONTENT_SHA256:
            # Loudly, rather than by dropping the entry. A committed hash of nothing is a
            # false statement about a page in a file whose whole job is to be believed, and
            # every quieter handling of it ends with the check reporting something reassuring:
            # keep it and a blind page "matches"; drop it and the same page reads as merely
            # "no committed baseline", which is what an unwatched-but-fine source looks like.
            raise RegistryError(
                f"baseline file: {source_id!r} records the sha256 of empty content "
                f"({EMPTY_CONTENT_SHA256[:12]}…), which is not a baseline — it is what a page "
                "with no readable text hashes to. Remove the entry, or re-derive the file with "
                "`sentinel watch && sentinel baseline write`."
            )
        normalizer = entry.get("normalizer_version")
        extractor = entry.get("extractor_version")
        contract = (
            representation_contract(normalizer, extractor)
            if isinstance(normalizer, str) and isinstance(extractor, str)
            else UNRECORDED_CONTRACT
        )
        loaded[source_id] = BaselineEntry(sha256=entry["sha256"], contract=contract)
    return loaded


def check_baselines(
    sources: Iterable[Source],
    fetcher: Fetcher,
    baselines: dict[str, BaselineEntry],
) -> BaselineReport:
    """Fetch each source once and compare it against the committed baseline hash.

    The same disciplines apply here as in `watch()`, for the same reasons: **a fetch failure
    is never drift** (an unreachable source is reported as unreachable and nothing is
    concluded from it), and **nothing is classified** (a moved hash is a fact about bytes,
    and what it means is a human's call).

    And a third, added with the representation contract: **a hash is only comparable against
    a hash from the same normalizer.** `watch()` resolves that by re-deriving the baseline
    from retained bytes; this command has no bytes, so it resolves it by *saying so* — the
    MOVED source is still reported, and named in `moved_across_contracts` as one whose
    "movement" may be our normalizer rather than the page. Note what is not qualified: a
    hash that *matches* across two contracts needs no caveat, because two normalizers
    producing the same digest produced the same text.

    And a fourth, added with issue #19: **a page with no extractable text is not compared at
    all.** `watch` refuses that comparison because a hash of nothing means nothing; this
    command refuses it for the same reason and reports the source in its own `no_text` bucket.
    Checked before the committed hash is even consulted, so no outcome — match, move, or
    missing baseline — can be reported about a page we could not read.

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

        current, normalized = content_hash(result.body, result.content_type)
        if kind_for_content_type(result.content_type) != ContentKind.BINARY and not passages(
            normalized
        ):
            report.no_text.append((source.id, source.url))
            continue

        committed = baselines.get(source.id)
        if committed is None:
            report.unbaselined.append(source.id)
            continue

        if current == committed.sha256:
            report.matched.append(source.id)
            continue

        report.moved.append((source.id, committed.sha256, current))
        if committed.contract != CURRENT_CONTRACT:
            report.moved_across_contracts[source.id] = committed.contract
    return report
