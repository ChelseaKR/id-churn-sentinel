"""Registry change events, derived from two revisions of `sources/registry.json`.

The registry is not a fixed list. Arizona was swapped for a deeper page, Nevada was removed
and later re-added through the Legislature's site, sixteen jurisdictions were closed through
statute pages, Michigan's SCAO form moved from a source to a named gap. Every one of those
was a real change to what the feed watches, and every one of them lived only in a commit
message and some README prose.

That matters because of what a consumer is subscribed to. `feed-us-az.xml` names a
jurisdiction and a document class; it does not name a URL. A clinic that subscribed in July
has no way to learn that the page behind "AZ · driver's licence" is not the page it was
then — and a source swap is precisely the event that turns a stale mental model into a wrong
assumption about what the feed's silence covers.

**What this module is, and what it is not.** It derives events from two *committed
revisions*, and only from what those two revisions say. It has no clock, reads no network,
and asks no question the registry files cannot answer between them. That constraint is what
makes the output byte-identical on repeat, and it is also why one event kind carries a caveat
rather than a claim — see :data:`EVENT_VERIFICATION_EXPIRED`.

Three properties hold, and each of them is a test:

* **The vocabulary is closed.** :data:`EVENT_KINDS` is the whole of it. A consumer can
  exhaustively branch on `kind` and know it has not silently been handed a fourteenth thing.
* **Every event carries its own revisions.** `from_revision` and `to_revision` sit on the
  event, not only on the document, so an event stays self-describing after a consumer filters
  the log down to one jurisdiction.
* **Nothing is dropped in silence.** :func:`diff_registries` re-reads its own inputs and
  raises if any field difference it can see is not accounted for by an emitted event or by
  the explicitly-listed, explicitly-reasoned exclusions in :data:`_NON_EVENTFUL_FIELDS`. A
  diff that quietly loses a change is worse than no diff, because it reads exactly like a
  registry that did not change.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import SentinelError
from .eligibility import registry_revision
from .registry import (
    GAP_REASONS,
    REJECTED,
    UNVERIFIED,
    VERIFIED,
    Gap,
    Registry,
    Source,
    load_registry,
)

__all__ = [
    "CHANGELOG_SCHEMA_VERSION",
    "DEFAULT_CHANGELOG_PATH",
    "EVENT_KINDS",
    "RegistryChangelogError",
    "RegistryEvent",
    "changelog_document",
    "default_changelog_path",
    "diff_registries",
    "dumps_changelog",
    "gap_key",
    "load_changelog",
    "read_registry_at",
    "reconcile",
    "seed_document",
]

CHANGELOG_SCHEMA_VERSION = "1.0"

#: Where the accumulating, committed log lives, beside the registry it describes.
DEFAULT_CHANGELOG_PATH = "sources/registry-changelog.json"


class RegistryChangelogError(SentinelError):
    """A changelog could not be derived, loaded, or reconciled with its registry."""


# ---- the closed vocabulary ----------------------------------------------------------------
#
# Thirteen kinds, and the count is deliberate. The proposal (issue #75) named nine; four more
# exist because without them a real transition would either be dropped or, worse, published
# under a label that does not describe it. Both of those are the failure this project is
# about, so the deviations are named here rather than buried:
#
#   * `gap_opened` — the proposal has `gap_closed` and `moved_to_gap` but no way to say a gap
#     appeared without a source moving into it. Twenty-one of the current gaps have no
#     predecessor source at all.
#   * `verification_rejected` — a named human opening a URL and finding it is NOT the official
#     page is not an expiry. Filing it under `verification_expired` would publish "this lapsed"
#     over the top of "a person found this was the wrong page".
#   * `verification_reset` and `reclassified` — the two remaining representable transitions.
#     A kind each, rather than a silent drop.

EVENT_ADDED = "added"
EVENT_REMOVED = "removed"
EVENT_URL_CHANGED = "url_changed"
EVENT_AUTHORITY_CHANGED = "authority_changed"
EVENT_RECLASSIFIED = "reclassified"
EVENT_VERIFIED = "verified"

#: A source that was `verified` in the earlier revision and is `unverified` in the later one.
#:
#: **It does not assert that a date passed.** `VERIFICATION_RECHECK_DAYS` expiry is evaluated
#: against an `as_of` (see `core/eligibility.py`), and a diff of two files has no `as_of` to
#: evaluate against. Feeding one in would make the same two revisions produce different events
#: on different days, which is the opposite of what a changelog is for. So this kind means
#: exactly what its `from`/`to` fields say — the entry stopped being human-verified — and the
#: recorded `expires_at` travels on the event so a reader can see the recheck date for
#: themselves rather than be told a conclusion nothing here computed.
EVENT_VERIFICATION_EXPIRED = "verification_expired"

EVENT_VERIFICATION_REJECTED = "verification_rejected"
EVENT_VERIFICATION_RESET = "verification_reset"
EVENT_MOVED_TO_GAP = "moved_to_gap"
EVENT_GAP_OPENED = "gap_opened"
EVENT_GAP_CLOSED = "gap_closed"
EVENT_POLICY_DECISION_RECORDED = "policy_decision_recorded"

EVENT_KINDS: frozenset[str] = frozenset(
    {
        EVENT_ADDED,
        EVENT_REMOVED,
        EVENT_URL_CHANGED,
        EVENT_AUTHORITY_CHANGED,
        EVENT_RECLASSIFIED,
        EVENT_VERIFIED,
        EVENT_VERIFICATION_EXPIRED,
        EVENT_VERIFICATION_REJECTED,
        EVENT_VERIFICATION_RESET,
        EVENT_MOVED_TO_GAP,
        EVENT_GAP_OPENED,
        EVENT_GAP_CLOSED,
        EVENT_POLICY_DECISION_RECORDED,
    }
)

SUBJECT_SOURCE = "source"
SUBJECT_GAP = "gap"
SUBJECTS: frozenset[str] = frozenset({SUBJECT_SOURCE, SUBJECT_GAP})

#: Source fields that deliberately produce no event, each with the reason it produces none.
#: This list is the *only* sanctioned way for a field difference to go unreported, and
#: :func:`_assert_nothing_dropped` reads it, so adding a field to the registry without adding
#: it here or to an event kind fails the diff instead of vanishing from the log.
_NON_EVENTFUL_FIELDS: Mapping[str, str] = {
    "notes": (
        "free-form operator rationale. The proposal is explicit that events carry ids, hashes, "
        "vocabulary reasons and dates and never free text; republishing a note as an event "
        "would put internal prose into a public artifact."
    ),
    "checked": (
        "what a socket saw on the last liveness pass. It changes most weeks, says nothing "
        "about what the registry claims, and would drown every real event in machine noise."
    ),
    "verified": (
        "the legacy boolean mirror of `verification.status`. The status transition is already "
        "an event; emitting both would double-count one change."
    ),
    "active": (
        "an internal flag with no writer in `src/`. It is reported by `reconcile` if it ever "
        "diverges rather than being given an event kind it has never needed."
    ),
}


@dataclass(frozen=True, slots=True)
class RegistryEvent:
    """One change to one registry entry, between two named revisions.

    Frozen and flat, for the same reason `source_payload` is: the fields a consumer filters on
    must be impossible to miss and trivial to query. `from_value`/`to_value` are serialized as
    `from`/`to`, which are not valid Python identifiers.
    """

    kind: str
    subject: str
    subject_id: str
    jurisdiction: str
    document_class: str
    from_revision: str
    to_revision: str
    from_value: str = ""
    to_value: str = ""
    reason: str = ""
    actor: str = ""
    at: str = ""
    expires_at: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise RegistryChangelogError(f"unknown event kind: {self.kind!r}")
        if self.subject not in SUBJECTS:
            raise RegistryChangelogError(f"unknown event subject: {self.subject!r}")
        if self.reason and self.reason not in GAP_REASONS:
            raise RegistryChangelogError(
                f"event reason {self.reason!r} is not in the closed gap vocabulary"
            )

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.subject, self.subject_id, self.kind)

    def to_dict(self) -> dict[str, str]:
        """The published shape. Optional fields are **omitted, never blanked**.

        An empty string in `reason` would read as a reason of ""; absence reads as absence,
        which is the whole discipline this repository is built on.
        """
        payload = {
            "kind": self.kind,
            "subject": self.subject,
            "subject_id": self.subject_id,
            "jurisdiction": self.jurisdiction,
            "document_class": self.document_class,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
        }
        for key, value in (
            ("from", self.from_value),
            ("to", self.to_value),
            ("reason", self.reason),
            ("actor", self.actor),
            ("at", self.at),
            ("expires_at", self.expires_at),
        ):
            if value:
                payload[key] = value
        return payload


def gap_key(jurisdiction: str, document_class: str) -> str:
    """The stable id of a gap, which the registry itself does not carry.

    A `Gap` is keyed by the pair it covers, so the changelog needs an id it can put in
    `subject_id` and a consumer can match on. `gap:` prefixes it so a gap id can never collide
    with a source id — source ids are `[a-z0-9-]+` and cannot contain a colon.
    """
    return f"gap:{jurisdiction.upper()}:{document_class}"


def default_changelog_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[3]
    return base / DEFAULT_CHANGELOG_PATH


# ---- reading a revision -------------------------------------------------------------------


def read_registry_at(reference: str, *, root: Path | None = None) -> Registry:
    """Load a registry from a file path or a git revision, through the **same validator**.

    A path that exists on disk is read as a file. Anything else is resolved as a git revision
    and read with `git show <rev>:sources/registry.json`.

    The bytes are then written to a temporary file and handed to :func:`load_registry`, rather
    than parsed here. That is not a detour: it means a historical revision is subject to every
    rule the current one is, so the diff can never be computed over a registry shape this
    version of the code does not actually understand.
    """
    base = root or Path(__file__).resolve().parents[3]
    candidate = Path(reference)
    if candidate.exists():
        return load_registry(candidate)

    spec = reference if ":" in reference else f"{reference}:{'sources/registry.json'}"
    try:
        completed = subprocess.run(  # noqa: S603 — argument vector, no shell
            ["git", "show", spec],  # noqa: S607 — resolved from PATH by design, as elsewhere
            cwd=base,
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git absent is an environment fault
        raise RegistryChangelogError("git is not available to resolve a revision") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise RegistryChangelogError(
            f"{reference!r} is neither a readable file nor a git revision holding "
            f"sources/registry.json: {detail}"
        ) from exc

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "registry.json"
        path.write_bytes(completed.stdout)
        return load_registry(path)


# ---- the diff -----------------------------------------------------------------------------


def diff_registries(before: Registry, after: Registry) -> tuple[RegistryEvent, ...]:
    """Every change between two registry revisions, as closed-vocabulary events.

    Deterministic: the result depends on the two registries and nothing else — no clock, no
    network, no environment — and is sorted by `(subject, subject_id, kind)` so the same pair
    of inputs always serializes to the same bytes.
    """
    from_revision = registry_revision(before)
    to_revision = registry_revision(after)

    before_sources = {source.id: source for source in before.sources}
    after_sources = {source.id: source for source in after.sources}
    before_gaps = {gap_key(g.jurisdiction, g.document_class): g for g in before.gaps}
    after_gaps = {gap_key(g.jurisdiction, g.document_class): g for g in after.gaps}

    events: list[RegistryEvent] = []
    moved_gap_keys: set[str] = set()

    for source_id in sorted(set(before_sources) - set(after_sources)):
        source = before_sources[source_id]
        key = gap_key(source.jurisdiction, source.document_class)
        successor = after_gaps.get(key)
        if successor is not None and key not in before_gaps:
            # The source did not merely go away: the pair it covered is now a *named* gap.
            # Emitting `removed` plus `gap_opened` would report one act as two, and would
            # lose the only fact a consumer needs — that the silence is now accounted for.
            moved_gap_keys.add(key)
            events.append(
                _event(
                    EVENT_MOVED_TO_GAP,
                    SUBJECT_SOURCE,
                    source_id,
                    source.jurisdiction,
                    source.document_class,
                    from_revision,
                    to_revision,
                    from_value=source.url,
                    reason=successor.reason,
                    at=successor.checked,
                )
            )
            continue
        events.append(
            _event(
                EVENT_REMOVED,
                SUBJECT_SOURCE,
                source_id,
                source.jurisdiction,
                source.document_class,
                from_revision,
                to_revision,
                from_value=source.url,
            )
        )

    for source_id in sorted(set(after_sources) - set(before_sources)):
        source = after_sources[source_id]
        events.append(
            _event(
                EVENT_ADDED,
                SUBJECT_SOURCE,
                source_id,
                source.jurisdiction,
                source.document_class,
                from_revision,
                to_revision,
                to_value=source.url,
            )
        )

    for source_id in sorted(set(before_sources) & set(after_sources)):
        events.extend(
            _source_events(
                before_sources[source_id],
                after_sources[source_id],
                from_revision=from_revision,
                to_revision=to_revision,
            )
        )

    for key in sorted(set(after_gaps) - set(before_gaps) - moved_gap_keys):
        gap = after_gaps[key]
        events.append(
            _event(
                EVENT_GAP_OPENED,
                SUBJECT_GAP,
                key,
                gap.jurisdiction,
                gap.document_class,
                from_revision,
                to_revision,
                reason=gap.reason,
                at=gap.checked,
            )
        )

    for key in sorted(set(before_gaps) - set(after_gaps)):
        gap = before_gaps[key]
        events.append(
            _event(
                EVENT_GAP_CLOSED,
                SUBJECT_GAP,
                key,
                gap.jurisdiction,
                gap.document_class,
                from_revision,
                to_revision,
                reason=gap.reason,
                at=gap.checked,
            )
        )

    ordered = tuple(sorted(events, key=lambda event: event.sort_key))
    _assert_nothing_dropped(before, after, ordered)
    return ordered


def _event(
    kind: str,
    subject: str,
    subject_id: str,
    jurisdiction: str,
    document_class: str,
    from_revision: str,
    to_revision: str,
    **extra: str,
) -> RegistryEvent:
    return RegistryEvent(
        kind=kind,
        subject=subject,
        subject_id=subject_id,
        jurisdiction=jurisdiction,
        document_class=document_class,
        from_revision=from_revision,
        to_revision=to_revision,
        **extra,
    )


_VERIFICATION_TRANSITIONS: Mapping[tuple[str, str], str] = {
    (UNVERIFIED, VERIFIED): EVENT_VERIFIED,
    (REJECTED, VERIFIED): EVENT_VERIFIED,
    (VERIFIED, UNVERIFIED): EVENT_VERIFICATION_EXPIRED,
    (UNVERIFIED, REJECTED): EVENT_VERIFICATION_REJECTED,
    (VERIFIED, REJECTED): EVENT_VERIFICATION_REJECTED,
    (REJECTED, UNVERIFIED): EVENT_VERIFICATION_RESET,
}


def _source_events(
    before: Source, after: Source, *, from_revision: str, to_revision: str
) -> list[RegistryEvent]:
    """Events for one source present in both revisions."""
    events: list[RegistryEvent] = []

    def make(kind: str, **extra: str) -> RegistryEvent:
        return _event(
            kind,
            SUBJECT_SOURCE,
            after.id,
            after.jurisdiction,
            after.document_class,
            from_revision,
            to_revision,
            **extra,
        )

    if (before.jurisdiction, before.document_class) != (after.jurisdiction, after.document_class):
        events.append(
            make(
                EVENT_RECLASSIFIED,
                from_value=f"{before.jurisdiction}/{before.document_class}",
                to_value=f"{after.jurisdiction}/{after.document_class}",
            )
        )
    if before.url != after.url:
        events.append(make(EVENT_URL_CHANGED, from_value=before.url, to_value=after.url))
    if before.authority != after.authority:
        events.append(
            make(
                EVENT_AUTHORITY_CHANGED,
                from_value=before.authority,
                to_value=after.authority,
            )
        )

    before_status = before.verification.status
    after_status = after.verification.status
    if before_status != after_status:
        kind = _VERIFICATION_TRANSITIONS.get((before_status, after_status))
        if kind is None:  # pragma: no cover - unreachable while the status set is closed
            raise RegistryChangelogError(
                f"{after.id}: no event kind covers the verification transition "
                f"{before_status!r} -> {after_status!r}"
            )
        source_of_record = after.verification if after_status == VERIFIED else before.verification
        events.append(
            make(
                kind,
                from_value=before_status,
                to_value=after_status,
                actor=after.verification.verifier or before.verification.verifier,
                at=after.verification.at or before.verification.at,
                expires_at=source_of_record.expires_at,
            )
        )

    if before.fetch_policy.to_dict() != after.fetch_policy.to_dict():
        events.append(
            make(
                EVENT_POLICY_DECISION_RECORDED,
                from_value=before.fetch_policy.outcome,
                to_value=after.fetch_policy.outcome,
                actor=after.fetch_policy.reviewer,
                at=after.fetch_policy.at,
                expires_at=after.fetch_policy.expires_at,
            )
        )
    return events


#: Which source field each kind is the report of. Read by :func:`_assert_nothing_dropped`.
_KIND_COVERS: Mapping[str, frozenset[str]] = {
    EVENT_URL_CHANGED: frozenset({"url"}),
    EVENT_AUTHORITY_CHANGED: frozenset({"authority"}),
    EVENT_RECLASSIFIED: frozenset({"jurisdiction", "document_class"}),
    EVENT_VERIFIED: frozenset({"verification"}),
    EVENT_VERIFICATION_EXPIRED: frozenset({"verification"}),
    EVENT_VERIFICATION_REJECTED: frozenset({"verification"}),
    EVENT_VERIFICATION_RESET: frozenset({"verification"}),
    EVENT_POLICY_DECISION_RECORDED: frozenset({"fetch_policy"}),
}


def _source_fields(source: Source) -> dict[str, Any]:
    return {
        "jurisdiction": source.jurisdiction,
        "document_class": source.document_class,
        "url": source.url,
        "authority": source.authority,
        "verified": source.verified,
        "notes": source.notes,
        "checked": dict(source.checked),
        "verification": source.verification.status,
        "active": source.active,
        "fetch_policy": source.fetch_policy.to_dict(),
    }


def _assert_nothing_dropped(
    before: Registry, after: Registry, events: Sequence[RegistryEvent]
) -> None:
    """Re-read the inputs and refuse to return a diff that lost a change.

    A changelog that silently omits an event is indistinguishable, to every consumer and every
    other test, from a registry that did not change — and "nothing to report" is the single
    claim this project may not make by accident. So the diff checks its own work: for each
    source present in both revisions, every field that differs must be covered by an emitted
    event kind or be named in :data:`_NON_EVENTFUL_FIELDS` with its reason.

    Note what this deliberately compares: the *verification status*, not the whole
    verification block. A verifier re-confirming an already-verified source updates `at` and
    `expires_at` without changing what the registry claims, and there is no transition to
    report.
    """
    covered: dict[str, set[str]] = {}
    for event in events:
        if event.subject != SUBJECT_SOURCE:
            continue
        covered.setdefault(event.subject_id, set()).update(
            _KIND_COVERS.get(event.kind, frozenset())
        )

    before_sources = {source.id: source for source in before.sources}
    unaccounted: list[str] = []
    for source in after.sources:
        previous = before_sources.get(source.id)
        if previous is None:
            continue
        old = _source_fields(previous)
        new = _source_fields(source)
        for field_name, new_value in new.items():
            if old[field_name] == new_value:
                continue
            if field_name in _NON_EVENTFUL_FIELDS:
                continue
            if field_name in covered.get(source.id, set()):
                continue
            unaccounted.append(f"{source.id}.{field_name}")

    if unaccounted:
        raise RegistryChangelogError(
            "the registry diff would have dropped "
            f"{len(unaccounted)} field change(s) with no event: {', '.join(sorted(unaccounted))}. "
            "Add an event kind that reports them, or name the field in _NON_EVENTFUL_FIELDS "
            "with the reason it is not an event. Do not let a change reach the log as silence."
        )


# ---- the document -------------------------------------------------------------------------

_UNRECORDED_STATEMENT = (
    "Events before this registry revision are UNRECORDED. This log begins at the revision "
    "named here; the registry changed before it, and those changes are absent from this file "
    "because they were never derived, not because they did not happen. Do not read the "
    "earliest event in this log as the registry's first change."
)

_DOCUMENT_STATEMENT = (
    "Every event here is derived from two committed revisions of sources/registry.json by "
    "`sentinel registry changelog`. An event says what the registry said on either side. It "
    "makes no claim about the page at the URL, and none about the law."
)


def changelog_document(
    events: Sequence[RegistryEvent], *, unrecorded_before: str
) -> dict[str, Any]:
    """The published document. No clock: two identical inputs give identical bytes."""
    return {
        "schema_version": CHANGELOG_SCHEMA_VERSION,
        "statement": _DOCUMENT_STATEMENT,
        "unrecorded_before": {
            "revision": unrecorded_before,
            "statement": _UNRECORDED_STATEMENT,
        },
        "events": [event.to_dict() for event in events],
    }


def seed_document(registry: Registry) -> dict[str, Any]:
    """An empty log that begins at `registry`'s revision — the honest starting state.

    Reconstructing the history that predates this file is explicitly out of scope (#75), and
    the alternative to saying so is a log whose first entry looks like a beginning.
    """
    return changelog_document((), unrecorded_before=registry_revision(registry))


def dumps_changelog(document: Mapping[str, Any]) -> str:
    """Serialize with a trailing newline, exactly as every other artifact here is written."""
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


def load_changelog(path: Path | None = None, *, root: Path | None = None) -> dict[str, Any]:
    """Read and structurally validate the committed log. Any violation raises."""
    target = path or default_changelog_path(root)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryChangelogError(f"registry changelog not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryChangelogError(f"registry changelog is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryChangelogError("registry changelog must be a JSON object")

    version = raw.get("schema_version")
    if version != CHANGELOG_SCHEMA_VERSION:
        raise RegistryChangelogError(
            f"registry changelog schema_version {version!r} is not the supported "
            f"{CHANGELOG_SCHEMA_VERSION!r}"
        )
    marker = raw.get("unrecorded_before")
    if not isinstance(marker, dict) or not isinstance(marker.get("revision"), str):
        raise RegistryChangelogError(
            "registry changelog must carry an `unrecorded_before.revision`: a log that does "
            "not say where it starts invites its first event to be read as the first change"
        )
    events = raw.get("events")
    if not isinstance(events, list):
        raise RegistryChangelogError("registry changelog `events` must be a list")
    for index, event in enumerate(events):
        _validate_event_shape(event, index)
    return raw


_REQUIRED_EVENT_KEYS = (
    "kind",
    "subject",
    "subject_id",
    "jurisdiction",
    "document_class",
    "from_revision",
    "to_revision",
)
_ALLOWED_EVENT_KEYS = frozenset(
    {*_REQUIRED_EVENT_KEYS, "from", "to", "reason", "actor", "at", "expires_at"}
)


def _validate_event_shape(event: object, index: int) -> None:
    where = f"events[{index}]"
    if not isinstance(event, dict):
        raise RegistryChangelogError(f"{where} must be an object")
    missing = set(_REQUIRED_EVENT_KEYS) - set(event)
    if missing:
        raise RegistryChangelogError(f"{where} is missing {sorted(missing)}")
    unknown = set(event) - _ALLOWED_EVENT_KEYS
    if unknown:
        raise RegistryChangelogError(f"{where} has unknown field(s) {sorted(unknown)}")
    if event["kind"] not in EVENT_KINDS:
        raise RegistryChangelogError(f"{where}.kind {event['kind']!r} is not a known event kind")
    if event["subject"] not in SUBJECTS:
        raise RegistryChangelogError(f"{where}.subject {event['subject']!r} is not known")
    reason = event.get("reason", "")
    if reason and reason not in GAP_REASONS:
        raise RegistryChangelogError(
            f"{where}.reason {reason!r} is not in the closed gap vocabulary"
        )


# ---- reconciliation with the registry -----------------------------------------------------


def reconcile(document: Mapping[str, Any], registry: Registry) -> list[str]:
    """Does this log describe *this* registry? Returns the violations; empty means yes.

    Two questions, and the gate (`sentinel coverage --check-docs`) asks both:

    * **Does every subject exist, or has its disappearance been reported?** A log naming a
      source that is not in the registry and was never `removed` or `moved_to_gap` is a log
      about some other registry — the hand-edit case the proposal names.
    * **Is every live entry's latest event consistent with what the registry now says?** A
      `url_changed` whose `to` is not the URL the registry carries means the log and the
      registry disagree about the present, and the log is the one that will be believed,
      because it is the one that looks like history.
    """
    violations: list[str] = []
    sources = {source.id: source for source in registry.sources}
    gaps = {gap_key(g.jurisdiction, g.document_class): g for g in registry.gaps}
    events = [event for event in document.get("events", []) if isinstance(event, dict)]

    departed = {
        str(event["subject_id"])
        for event in events
        if event.get("kind") in {EVENT_REMOVED, EVENT_MOVED_TO_GAP}
    }
    closed = {str(event["subject_id"]) for event in events if event.get("kind") == EVENT_GAP_CLOSED}

    for index, event in enumerate(events):
        subject_id = str(event.get("subject_id", ""))
        if event.get("subject") == SUBJECT_SOURCE:
            if subject_id not in sources and subject_id not in departed:
                violations.append(
                    f"events[{index}]: names source {subject_id!r}, which is not in the "
                    f"registry and which no `removed` or `moved_to_gap` event accounts for"
                )
        elif subject_id not in gaps and subject_id not in closed:
            violations.append(
                f"events[{index}]: names gap {subject_id!r}, which is not in the registry and "
                f"which no `gap_closed` event accounts for"
            )

    violations.extend(_reconcile_latest_values(events, sources, gaps))
    return violations


def _reconcile_latest_values(
    events: Sequence[Mapping[str, Any]],
    sources: Mapping[str, Source],
    gaps: Mapping[str, Gap],
) -> list[str]:
    """The last reported value for a live entry must be the value the registry carries."""
    violations: list[str] = []
    latest_url: dict[str, tuple[int, str]] = {}
    latest_reason: dict[str, tuple[int, str]] = {}
    for index, event in enumerate(events):
        subject_id = str(event.get("subject_id", ""))
        if event.get("kind") in {EVENT_URL_CHANGED, EVENT_ADDED} and event.get("to"):
            latest_url[subject_id] = (index, str(event["to"]))
        if event.get("kind") == EVENT_GAP_OPENED and event.get("reason"):
            latest_reason[subject_id] = (index, str(event["reason"]))

    for subject_id, (index, url) in sorted(latest_url.items()):
        source = sources.get(subject_id)
        if source is not None and source.url != url:
            violations.append(
                f"events[{index}]: the last reported URL for {subject_id!r} is {url!r}, but the "
                f"registry now carries {source.url!r}. Derive the missing event rather than "
                f"editing either file to agree."
            )
    for subject_id, (index, reason) in sorted(latest_reason.items()):
        gap = gaps.get(subject_id)
        if gap is not None and gap.reason != reason:
            violations.append(
                f"events[{index}]: the last reported reason for {subject_id!r} is {reason!r}, "
                f"but the registry now carries {gap.reason!r}."
            )
    return violations
