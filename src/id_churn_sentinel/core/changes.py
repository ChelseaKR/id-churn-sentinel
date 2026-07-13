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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from id_churn_sentinel.errors import ReviewError

__all__ = [
    "ChangeKind",
    "ChangeRecord",
    "ReviewStatus",
    "Significance",
]


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
        reviewed_at: datetime | None = None,
    ) -> ChangeRecord:
        """Apply a human's review. The *only* path to a non-`UNCLASSIFIED` significance.

        `reviewer` must be a non-empty name. This is not bureaucratic ceremony: the whole
        value proposition of the published feed is that a person stands behind every item
        in it, and an anonymous classification is indistinguishable from an automated one
        to the org consuming the feed.
        """
        if not reviewer.strip():
            raise ReviewError(
                "a review requires a named human reviewer — classification is not an "
                "automated capability of this tool"
            )
        if status is ReviewStatus.UNREVIEWED:
            raise ReviewError("a review cannot set the status back to 'unreviewed'")
        if status is ReviewStatus.CONFIRMED and significance is Significance.UNCLASSIFIED:
            raise ReviewError(
                "confirming a change requires classifying it as 'editorial' or 'substantive'"
            )
        return replace(
            self,
            significance=significance,
            review_status=status,
            reviewer=reviewer.strip(),
            reviewed_at=reviewed_at or datetime.now(UTC),
            review_note=note,
        )

    @property
    def publishable(self) -> bool:
        """The single predicate the publisher is allowed to consult.

        Confirmed *and* classified *and* signed by a human. Dismissed records (reviewed
        noise) stay out of the feed too — a consumer polling this feed should see only
        changes a person decided were worth someone's attention.
        """
        return (
            self.review_status is ReviewStatus.CONFIRMED
            and self.significance is not Significance.UNCLASSIFIED
            and bool(self.reviewer)
        )

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
            "diff_excerpt": self.diff_excerpt,
            "kind": str(self.kind),
            "significance": str(self.significance),
            "review_status": str(self.review_status),
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_note": self.review_note,
        }


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
    material = f"{source_id}\n{previous_hash}\n{new_hash}".encode()
    return hashlib.sha256(material).hexdigest()[:16]
