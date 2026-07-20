"""The change record — and the human-in-the-loop gate that guards it.

A :class:`ChangeRecord` says one modest, checkable thing: *"the bytes at this official URL
were X, they are now Y, and here are the passages that differ."* That is a claim about a
web page. It is **not** a claim that the law changed, that a process changed, or that
anything about the change matters. Those are legal judgments, and this tool does not make
legal judgments — a machine asserting "Texas substantively changed its gender-marker policy"
on the strength of a hash comparison would be exactly the harm the portfolio's standards
forbid, and it would be believed by the people least able to absorb being wrong.

So classification is a *human action*, enforced structurally:

* Detection can only ever mint a record at ``significance=UNCLASSIFIED`` /
  ``review_status=UNREVIEWED``. There is no argument to the constructor used by the
  detector that could produce anything else — :func:`ChangeRecord.observed` takes no
  significance parameter at all.
* Moving off ``UNCLASSIFIED`` requires :meth:`ChangeRecord.reviewed_by`, which *requires a
  named reviewer*. A classification without a human name attached is unrepresentable,
  and the store's schema declares the same constraint independently
  (``CHECK`` on the ``changes`` table), so the invariant survives someone bypassing this
  module and writing SQL by hand.
* Only ``CONFIRMED`` records are publishable (:attr:`ChangeRecord.publishable`). Unreviewed
  drift and dismissed noise never reach a consumer.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from unicodedata import category, normalize

from id_churn_sentinel.errors import ReviewError

__all__ = [
    "DEFAULT_PUBLIC_COPY",
    "ChangeKind",
    "ChangeRecord",
    "IndependentReviewStatus",
    "PublicationStatus",
    "ReviewStatus",
    "Significance",
    "actor_identity",
    "canonical_actor",
    "governance_reference_is_safe",
    "observation_fields_are_valid",
    "private_text_is_safe",
    "public_copy_is_safe",
]


DEFAULT_PUBLIC_COPY = (
    "The cited official source visibly changed. Review the cited passage and conduct your "
    "own legal and editorial review."
)
LIFECYCLE_REASONS = (
    "source_evidence_error",
    "review_error",
    "duplicate_record",
    "privacy_or_safety",
    "superseded_observation",
    "other_governed_reason",
)
PRIVACY_WITHDRAWAL_COPY = (
    "Withdrawn for privacy or safety. Prior public observation text is no longer displayed."
)
PRIVACY_WITHDRAWAL_DIFF = "Content withheld following a privacy-or-safety withdrawal."
_PROHIBITED_PUBLIC_CLAIM = re.compile(r"\b(?:operative|requires|permits|effective)\b", re.I)
_CHANGE_ID = re.compile(r"^[0-9a-f]{16}$")


class ChangeKind(StrEnum):
    """*What the machine observed*, which is a strictly different axis from what a human
    concluded (:class:`Significance`). Both values here are things a socket can establish
    on its own; neither is a judgment.

    * ``CONTENT_DRIFT`` — we fetched the page and its normalized text hashed differently
      than last time.
    * ``POSSIBLY_REMOVED`` — we *could not fetch it at all*, N times in a row.

    The second one exists because silence was previously the wrong answer to it. An
    unreachable source carries its old baseline forward (correct — an outage is not a
    content change), but a page that has been **taken down** looks identical to a brief
    outage, forever, and quietly keeps serving a stale baseline nobody is told is stale.
    A government page about trans identity documents *disappearing* is itself a policy
    signal, and the appropriate response to it is not silence.

    Note carefully what ``POSSIBLY_REMOVED`` does **not** say. It does not say the page was
    removed. It says: we failed to fetch this N consecutive times, here is the literal error
    we got each time, a human needs to look. A 404 and a WAF block and a fortnight of
    timeouts all land here, and telling them apart is a human's job — which is why the
    escalation record carries the raw error string rather than our interpretation of it.
    """

    CONTENT_DRIFT = "content_drift"
    POSSIBLY_REMOVED = "possibly_removed"


class Significance(StrEnum):
    """What a *human* concluded the change means. `UNCLASSIFIED` is the only value this
    codebase can produce on its own."""

    UNCLASSIFIED = "unclassified"
    EDITORIAL = "editorial"
    SUBSTANTIVE = "substantive"


class ReviewStatus(StrEnum):
    """Where the record is in the human review queue."""

    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class IndependentReviewStatus(StrEnum):
    """The independent decision required before a substantive item can publish."""

    CONFIRMED = "confirmed"
    RETURNED = "returned"


class PublicationStatus(StrEnum):
    """Append-only public lifecycle of a previously publishable observation."""

    ACTIVE = "active"
    CORRECTED = "corrected"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    """One observed change at one watched source. Frozen — a review produces a *new*
    record rather than mutating the observed one, so the observation is never edited
    after the fact."""

    id: str
    source_id: str
    jurisdiction: str
    document_class: str
    url: str
    observed_at: datetime
    previous_hash: str
    new_hash: str
    diff_excerpt: str
    kind: ChangeKind = ChangeKind.CONTENT_DRIFT
    significance: Significance = Significance.UNCLASSIFIED
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    review_note: str = ""
    internal_rationale: str = ""
    independent_review_status: IndependentReviewStatus | None = None
    independent_reviewer: str | None = None
    independent_reviewed_at: datetime | None = None
    independent_rationale: str = ""
    independent_qualification_ref: str = ""
    independent_conflict_attestation_ref: str = ""
    publication_status: PublicationStatus = PublicationStatus.ACTIVE
    superseded_by: str | None = None
    lifecycle_reason: str = ""
    lifecycle_actor: str | None = None
    lifecycle_at: datetime | None = None

    @classmethod
    def observed(
        cls,
        *,
        source_id: str,
        jurisdiction: str,
        document_class: str,
        url: str,
        previous_hash: str,
        new_hash: str,
        diff_excerpt: str,
        observed_at: datetime | None = None,
    ) -> ChangeRecord:
        """Mint a record from a detected hash change.

        Note what this signature does *not* accept: `significance` and `review_status`.
        The detector is not offered the vocabulary to classify, so "the tool auto-flagged
        it as substantive" is not a bug that can be introduced by a careless caller — it
        is a sentence that cannot be typed.
        """
        return cls(
            id=change_id(source_id, previous_hash, new_hash),
            source_id=source_id,
            jurisdiction=jurisdiction,
            document_class=document_class,
            url=url,
            observed_at=observed_at or datetime.now(UTC),
            previous_hash=previous_hash,
            new_hash=new_hash,
            diff_excerpt=diff_excerpt,
            kind=ChangeKind.CONTENT_DRIFT,
            significance=Significance.UNCLASSIFIED,
            review_status=ReviewStatus.UNREVIEWED,
        )

    @classmethod
    def possibly_removed(
        cls,
        *,
        source_id: str,
        jurisdiction: str,
        document_class: str,
        url: str,
        last_known_hash: str,
        consecutive_failures: int,
        last_error: str,
        observed_at: datetime | None = None,
    ) -> ChangeRecord:
        """Mint a record for a source that has failed to fetch N consecutive times.

        Like :meth:`observed`, this signature deliberately does **not** accept
        `significance`, `review_status` or `reviewer`. The escalation is an observation
        about our own failed sockets, not a conclusion about a state's intentions, and the
        detector is given no vocabulary to make it one. "The tool decided Texas took its
        page down" is, once again, a sentence that cannot be typed.

        `new_hash` is empty because there *is* no new content — that is the whole point of
        the record, and inventing a hash for bytes we never received would be a lie the
        rest of the pipeline would faithfully propagate.

        The id is deterministic in (source, last-known-hash, ""), so a source that stays
        unreachable for months re-derives the *same* record every week instead of filling
        the reviewer's queue with one identical alarm per run — and a human's review of it
        survives (`ON CONFLICT (change_id) DO NOTHING`).
        """
        return cls(
            id=change_id(source_id, last_known_hash, ""),
            source_id=source_id,
            jurisdiction=jurisdiction,
            document_class=document_class,
            url=url,
            observed_at=observed_at or datetime.now(UTC),
            previous_hash=last_known_hash,
            new_hash="",
            diff_excerpt=_removal_excerpt(url, consecutive_failures, last_error),
            kind=ChangeKind.POSSIBLY_REMOVED,
            significance=Significance.UNCLASSIFIED,
            review_status=ReviewStatus.UNREVIEWED,
        )

    def reviewed_by(
        self,
        *,
        reviewer: str,
        significance: Significance,
        status: ReviewStatus,
        note: str = "",
        public_copy: str = DEFAULT_PUBLIC_COPY,
        reviewed_at: datetime | None = None,
    ) -> ChangeRecord:
        """Apply a human's review. The *only* path to a non-`UNCLASSIFIED` significance.

        `reviewer` must be a non-empty name. This is not bureaucratic ceremony: the whole
        value proposition of the published feed is that a person stands behind every item
        in it, and an anonymous classification is indistinguishable from an automated one
        to the org consuming the feed.
        """
        if self.review_status is not ReviewStatus.UNREVIEWED or self.reviewer is not None:
            raise ReviewError("a first review decision is already recorded")
        if not reviewer.strip():
            raise ReviewError(
                "a review requires a named human reviewer — classification is not an "
                "automated capability of this tool"
            )
        actor = _named_actor(reviewer, "a review")
        if status is ReviewStatus.UNREVIEWED:
            raise ReviewError("a review cannot set the status back to 'unreviewed'")
        if status is ReviewStatus.CONFIRMED and significance is Significance.UNCLASSIFIED:
            raise ReviewError(
                "confirming a change requires classifying it as 'editorial' or 'substantive'"
            )
        constrained_copy = ""
        if status is ReviewStatus.CONFIRMED:
            constrained_copy = _validate_public_copy(public_copy)
        decision_at = _canonical_event_time(
            reviewed_at or datetime.now(UTC),
            "first review",
            not_before=self.observed_at,
        )
        return replace(
            self,
            significance=significance,
            review_status=status,
            reviewer=actor,
            reviewed_at=decision_at,
            review_note=constrained_copy,
            internal_rationale=_bounded_private_text(note, "review rationale"),
        )

    def independently_reviewed_by(
        self,
        *,
        reviewer: str,
        status: IndependentReviewStatus,
        qualification_ref: str,
        conflict_attestation_ref: str,
        rationale: str = "",
        reviewed_at: datetime | None = None,
    ) -> ChangeRecord:
        """Record a distinct qualified decision for a substantive first confirmation.

        This is intentionally a second immutable fact, not an edit to the first review.
        Qualification and conflict references identify external governance evidence; their
        presence is enforced here, while the policy owners still decide whether that evidence
        is actually sufficient.
        """

        actor = _named_actor(reviewer, "an independent review")
        if (
            self.review_status is not ReviewStatus.CONFIRMED
            or self.significance is not Significance.SUBSTANTIVE
            or not self.reviewer
        ):
            raise ReviewError(
                "independent review requires a first-confirmed substantive observation"
            )
        if actor_identity(actor) == actor_identity(self.reviewer):
            raise ReviewError("the independent reviewer must be different from the first reviewer")
        if self.independent_review_status is not None:
            raise ReviewError("an independent review decision is already recorded")
        if status is IndependentReviewStatus.RETURNED and not rationale.strip():
            raise ReviewError("returning a high-impact item requires an internal rationale")
        decision_at = _canonical_event_time(
            reviewed_at or datetime.now(UTC),
            "independent review",
            not_before=self.reviewed_at,
        )
        return replace(
            self,
            independent_review_status=status,
            independent_reviewer=actor,
            independent_reviewed_at=decision_at,
            independent_rationale=_bounded_private_text(rationale, "independent-review rationale"),
            independent_qualification_ref=_bounded_reference(
                qualification_ref, "qualification reference"
            ),
            independent_conflict_attestation_ref=_bounded_reference(
                conflict_attestation_ref, "conflict-attestation reference"
            ),
        )

    def corrected_by(
        self,
        *,
        replacement_id: str,
        actor: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> ChangeRecord:
        """Project an immutable correction link; the store persists it as a new event."""

        if not self.publishable or self.publication_status is not PublicationStatus.ACTIVE:
            raise ReviewError("only an active publishable observation can be corrected")
        if not _CHANGE_ID.fullmatch(replacement_id) or replacement_id == self.id:
            raise ReviewError("a correction requires a different valid replacement change id")
        decision_at = _canonical_event_time(
            decided_at or datetime.now(UTC),
            "correction",
            not_before=self.independent_reviewed_at or self.reviewed_at,
        )
        return replace(
            self,
            publication_status=PublicationStatus.CORRECTED,
            superseded_by=replacement_id,
            lifecycle_reason=_lifecycle_reason(reason),
            lifecycle_actor=_named_actor(actor, "a correction"),
            lifecycle_at=decision_at,
        )

    def withdrawn_by(
        self,
        *,
        actor: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> ChangeRecord:
        """Project an immutable withdrawal without erasing the published history."""

        if not self.publishable or self.publication_status is not PublicationStatus.ACTIVE:
            raise ReviewError("only an active publishable observation can be withdrawn")
        decision_at = _canonical_event_time(
            decided_at or datetime.now(UTC),
            "withdrawal",
            not_before=self.independent_reviewed_at or self.reviewed_at,
        )
        return replace(
            self,
            publication_status=PublicationStatus.WITHDRAWN,
            superseded_by=None,
            lifecycle_reason=_lifecycle_reason(reason),
            lifecycle_actor=_named_actor(actor, "a withdrawal"),
            lifecycle_at=decision_at,
        )

    @property
    def observation_valid(self) -> bool:
        """Whether immutable machine-observation fields satisfy the public contract."""
        return observation_fields_are_valid(
            self.id,
            self.source_id,
            self.observed_at,
            self.previous_hash,
            self.new_hash,
            self.diff_excerpt,
            self.kind,
        )

    @property
    def first_review_valid(self) -> bool:
        """Whether the immutable first decision is complete and chronologically bound."""

        if self.review_status not in {ReviewStatus.CONFIRMED, ReviewStatus.DISMISSED}:
            return False
        if (
            self.significance is Significance.UNCLASSIFIED
            and self.review_status is ReviewStatus.CONFIRMED
        ):
            return False
        if (
            not _public_actor_is_safe(self.reviewer)
            or not _event_time_is_valid(self.reviewed_at, not_before=self.observed_at)
            or not private_text_is_safe(self.internal_rationale)
        ):
            return False
        if self.review_status is ReviewStatus.CONFIRMED:
            return _public_copy_is_safe(self.review_note)
        return self.review_note == ""

    @property
    def independent_review_valid(self) -> bool:
        """Whether a substantive second decision is complete, distinct, and ordered."""

        if (
            self.significance is not Significance.SUBSTANTIVE
            or self.independent_review_status
            not in {IndependentReviewStatus.CONFIRMED, IndependentReviewStatus.RETURNED}
            or not _public_actor_is_safe(self.independent_reviewer)
            or actor_identity(self.independent_reviewer) == actor_identity(self.reviewer)
            or not _event_time_is_valid(
                self.independent_reviewed_at,
                not_before=self.reviewed_at,
            )
            or not governance_reference_is_safe(self.independent_qualification_ref)
            or not governance_reference_is_safe(self.independent_conflict_attestation_ref)
            or not private_text_is_safe(self.independent_rationale)
        ):
            return False
        return not (
            self.independent_review_status is IndependentReviewStatus.RETURNED
            and not self.independent_rationale.strip()
        )

    @property
    def lifecycle_valid(self) -> bool:
        """Whether the append-only public lifecycle projection is internally coherent."""

        return (
            (
                self.publication_status is PublicationStatus.ACTIVE
                and self.superseded_by is None
                and not self.lifecycle_reason
                and self.lifecycle_actor is None
                and self.lifecycle_at is None
            )
            or (
                self.publication_status is PublicationStatus.CORRECTED
                and isinstance(self.superseded_by, str)
                and bool(_CHANGE_ID.fullmatch(self.superseded_by))
                and self.superseded_by != self.id
                and self.lifecycle_reason in LIFECYCLE_REASONS
                and _public_actor_is_safe(self.lifecycle_actor)
                and _event_time_is_valid(
                    self.lifecycle_at,
                    not_before=self.independent_reviewed_at or self.reviewed_at,
                )
            )
            or (
                self.publication_status is PublicationStatus.WITHDRAWN
                and self.superseded_by is None
                and self.lifecycle_reason in LIFECYCLE_REASONS
                and _public_actor_is_safe(self.lifecycle_actor)
                and _event_time_is_valid(
                    self.lifecycle_at,
                    not_before=self.independent_reviewed_at or self.reviewed_at,
                )
            )
        )

    @property
    def publishable(self) -> bool:
        """The single predicate the publisher is allowed to consult.

        Confirmed *and* classified *and* signed by a human. Dismissed records (reviewed
        noise) stay out of the feed too — a consumer polling this feed should see only
        changes a person decided were worth someone's attention.
        """
        first_review_ready = (
            self.review_status is ReviewStatus.CONFIRMED and self.first_review_valid
        )
        independent_empty = (
            self.independent_review_status is None
            and self.independent_reviewer is None
            and self.independent_reviewed_at is None
            and self.independent_rationale == ""
            and self.independent_qualification_ref == ""
            and self.independent_conflict_attestation_ref == ""
        )
        independent_ready = (self.significance is Significance.EDITORIAL and independent_empty) or (
            self.significance is Significance.SUBSTANTIVE
            and self.independent_review_status is IndependentReviewStatus.CONFIRMED
            and self.independent_review_valid
        )
        return (
            self.observation_valid
            and first_review_ready
            and independent_ready
            and self.lifecycle_valid
        )

    @property
    def public_review_note(self) -> str:
        if (
            self.publication_status is PublicationStatus.WITHDRAWN
            and self.lifecycle_reason == "privacy_or_safety"
        ):
            return PRIVACY_WITHDRAWAL_COPY
        return self.review_note

    @property
    def public_diff_excerpt(self) -> str:
        if (
            self.publication_status is PublicationStatus.WITHDRAWN
            and self.lifecycle_reason == "privacy_or_safety"
        ):
            return PRIVACY_WITHDRAWAL_DIFF
        return self.diff_excerpt

    def to_dict(self) -> dict[str, Any]:
        """The published JSON shape. Documented in `docs/CONSUMERS.md`; versioned by
        `FEED_SCHEMA_VERSION` in `publish.py`."""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "jurisdiction": self.jurisdiction,
            "document_class": self.document_class,
            "url": self.url,
            "observed_at": self.observed_at.isoformat(),
            "previous_hash": self.previous_hash,
            "new_hash": self.new_hash,
            "diff_excerpt": self.public_diff_excerpt,
            "kind": str(self.kind),
            "significance": str(self.significance),
            "review_status": str(self.review_status),
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_note": self.public_review_note,
            "independent_review_status": (
                str(self.independent_review_status)
                if self.independent_review_status is not None
                else None
            ),
            "independent_reviewer": self.independent_reviewer,
            "independent_reviewed_at": (
                self.independent_reviewed_at.isoformat() if self.independent_reviewed_at else None
            ),
            "publication_status": str(self.publication_status),
            "superseded_by": self.superseded_by,
            "lifecycle_reason": self.lifecycle_reason,
            "lifecycle_actor": self.lifecycle_actor,
            "lifecycle_at": self.lifecycle_at.isoformat() if self.lifecycle_at else None,
        }


def canonical_actor(value: str | None) -> str:
    """Return the bounded NFKC display form accepted at every public actor boundary."""

    if not isinstance(value, str) or _has_unsafe_public_control(value):
        return ""
    actor = " ".join(normalize("NFKC", value).split())
    encoded_size = _utf8_size(actor)
    if not actor or encoded_size is None or encoded_size > 200:
        return ""
    return actor


def actor_identity(value: str | None) -> str:
    """Canonical reviewer identity used by Python and SQLite integrity checks."""

    return canonical_actor(value).casefold()


def _named_actor(value: str, action: str) -> str:
    actor = canonical_actor(value)
    if not value.strip():
        raise ReviewError(f"{action} requires a named human actor")
    if not actor:
        raise ReviewError(f"{action} actor is not a valid bounded name")
    return actor


def _bounded_private_text(value: str, label: str) -> str:
    if not private_text_is_safe(value):
        raise ReviewError(f"{label} exceeds the private-text safety limit")
    return value


def _lifecycle_reason(value: str) -> str:
    if value not in LIFECYCLE_REASONS:
        raise ReviewError("lifecycle reason must use the controlled reason vocabulary")
    return value


def _bounded_reference(value: str, label: str) -> str:
    reference = value.strip()
    encoded_size = _utf8_size(reference)
    if (
        not reference
        or encoded_size is None
        or encoded_size > 512
        or _has_unsafe_public_control(reference)
    ):
        raise ReviewError(f"{label} must be a non-empty bounded reference")
    return reference


def _validate_public_copy(value: str) -> str:
    copy = value.strip()
    encoded_size = _utf8_size(copy)
    if not copy or encoded_size is None or encoded_size > 1000 or _has_unsafe_public_control(copy):
        raise ReviewError(
            "public copy must be non-empty, no more than 1000 UTF-8 bytes, and free of "
            "control, format, or malformed Unicode characters"
        )
    match = _PROHIBITED_PUBLIC_CLAIM.search(copy)
    if match is not None:
        raise ReviewError(
            f"public copy contains prohibited legal-claim term {match.group(0)!r}; "
            "keep the rationale private and describe only visible source movement"
        )
    return copy


def _has_unsafe_public_control(value: str) -> bool:
    """Reject controls and malformed code points that can corrupt public records."""

    return any(category(character) in {"Cc", "Cf", "Cs"} for character in value)


def _utf8_size(value: str) -> int | None:
    """Return strict UTF-8 size, or ``None`` for malformed Unicode such as surrogates."""

    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _public_multiline_is_safe(value: str) -> bool:
    encoded_size = _utf8_size(value)
    if not value or encoded_size is None or encoded_size > 16_000:
        return False
    return not any(
        category(character) in {"Cf", "Cs"}
        or (category(character) == "Cc" and character not in "\n\r\t")
        for character in value
    )


def _public_copy_is_safe(value: str) -> bool:
    try:
        return _validate_public_copy(value) == value
    except ReviewError:
        return False


def public_copy_is_safe(value: object) -> bool:
    """SQLite-compatible public-copy predicate that never raises on hostile input."""

    return isinstance(value, str) and _public_copy_is_safe(value)


def _public_actor_is_safe(value: str | None) -> bool:
    return isinstance(value, str) and canonical_actor(value) == value


def governance_reference_is_safe(value: object) -> bool:
    """Return true only for the exact canonical bounded governance reference."""

    if not isinstance(value, str):
        return False
    try:
        return _bounded_reference(value, "governance reference") == value
    except ReviewError:
        return False


def private_text_is_safe(value: object) -> bool:
    if not isinstance(value, str) or "\x00" in value:
        return False
    encoded_size = _utf8_size(value)
    return encoded_size is not None and encoded_size <= 4096


def observation_fields_are_valid(
    identifier: object,
    source_id: object,
    observed_at: object,
    previous_hash: object,
    new_hash: object,
    diff_excerpt: object,
    kind: object,
) -> bool:
    """Shared Python/SQLite validator for the append-only observation row."""

    if (
        not isinstance(identifier, str)
        or not isinstance(source_id, str)
        or not isinstance(previous_hash, str)
        or not isinstance(new_hash, str)
        or not isinstance(diff_excerpt, str)
        or not isinstance(kind, str)
    ):
        return False
    if _utf8_size(source_id) is None:
        return False
    if isinstance(observed_at, str):
        try:
            observed = datetime.fromisoformat(observed_at)
        except ValueError:
            return False
    elif isinstance(observed_at, datetime):
        observed = observed_at
    else:
        return False
    try:
        observed_kind = ChangeKind(kind)
    except (TypeError, ValueError):
        return False
    hashes_valid = bool(re.fullmatch(r"[0-9a-f]{64}", previous_hash)) and (
        (
            observed_kind is ChangeKind.CONTENT_DRIFT
            and bool(re.fullmatch(r"[0-9a-f]{64}", new_hash))
            and previous_hash != new_hash
        )
        or (observed_kind is ChangeKind.POSSIBLY_REMOVED and new_hash == "")
    )
    try:
        expected_identifier = change_id(source_id, previous_hash, new_hash)
    except ReviewError:
        return False
    return (
        bool(source_id)
        and observed.utcoffset() is not None
        and _public_multiline_is_safe(diff_excerpt)
        and hashes_valid
        and bool(_CHANGE_ID.fullmatch(identifier))
        and identifier == expected_identifier
    )


def _event_time_is_valid(
    value: datetime | None,
    *,
    not_before: datetime | None,
) -> bool:
    if (
        not isinstance(value, datetime)
        or value.utcoffset() is None
        or not isinstance(not_before, datetime)
        or not_before.utcoffset() is None
    ):
        return False
    return value.astimezone(UTC) >= not_before.astimezone(UTC)


def _canonical_event_time(
    value: datetime,
    label: str,
    *,
    not_before: datetime | None,
) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ReviewError(f"{label} time must be timezone-aware")
    canonical = value.astimezone(UTC)
    if not_before is not None and (
        not isinstance(not_before, datetime)
        or not_before.utcoffset() is None
        or canonical < not_before.astimezone(UTC)
    ):
        raise ReviewError(f"{label} cannot precede the event it reviews")
    return canonical


def _removal_excerpt(url: str, consecutive_failures: int, last_error: str) -> str:
    """What the reviewer reads when a source stops answering.

    Every word here is chosen to state the observation and refuse the conclusion. It gives
    the reviewer the two facts that actually discriminate between the cases — the streak
    length and the *literal* error string — and then explicitly enumerates the readings,
    rather than picking one. `HTTP 404` and `HTTP 403` and `unreachable: timed out` are
    three very different worlds, and the machine is not entitled to choose between them.
    """
    return (
        f"(no content diff — this source could not be fetched at all.)\n\n"
        f"Consecutive failed fetches: {consecutive_failures}\n"
        f"Last error: {last_error}\n"
        f"Official URL: {url}\n\n"
        f"This is NOT a detected content change, and it is NOT an assertion that the page "
        f"was taken down. It is an escalation: a source that has been unreachable this many "
        f"times in a row is no longer plausibly a transient outage, and its baseline has "
        f"been silently held all that time. A human must open the URL and determine which "
        f"of these it is:\n"
        f"  - the page was REMOVED or moved (a policy signal in its own right — a state "
        f"scrubbing a page is a change worth publishing);\n"
        f"  - we are being BLOCKED (a 403/429 from a WAF or a bot filter — the page is fine "
        f"and we are not welcome);\n"
        f"  - the host is simply DOWN or unroutable from where we run.\n"
        f"Read the last error above before deciding. The tool will not decide for you."
    )


def change_id(source_id: str, previous_hash: str, new_hash: str) -> str:
    """A deterministic id for a (source, before, after) transition.

    Deterministic on purpose: re-running `watch` over the same drift must produce the same
    id, so a re-run cannot duplicate a change a human already reviewed, and a change id
    cited in an email six months ago still resolves. Sixteen hex chars is ~64 bits, which
    is ample for a corpus that will never exceed a few thousand records and is short enough
    to paste into a message.
    """
    identity = f"{source_id}\n{previous_hash}\n{new_hash}"
    if _utf8_size(identity) is None:
        raise ReviewError("change identity components must be well-formed Unicode")
    material = identity.encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]
