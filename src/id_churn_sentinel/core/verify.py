"""`sentinel verify` — the tooling that makes human verification cheap, and *recorded*.

**The problem this exists to solve.** The registry holds 152 official-looking URLs. Every one
has been fetched, its status recorded and its `<title>` read — and **not one of them has been
confirmed by a person**. That gap is the only thing standing between this project and being
trustworthy, because a published list of "the official birth-certificate page for each state"
is read as exactly that, and an entry that is subtly wrong sends a trans person to the wrong
office on the wrong day. Machine-checking cannot close it: `courts.oregon.gov` serves a soft
404 with HTTP 200, and `ecfr.gov` serves a bot-wall titled *"Request Access"* with HTTP 200
too. A socket cannot tell you it is looking at the wrong page. A person can.

So the honest move is not to fake the flag. It is to **make the human's job cheap enough that
it actually gets done**, and to record it in a way that names who did it:

* one screen per source — jurisdiction, document class, authority, URL, the page's own
  `<title>`, and a short excerpt of its normalized text;
* three keys — confirm, reject, skip;
* **a confirmation cannot be recorded without a name** (:class:`VerificationError`), and the
  registry itself will not *load* a `verified: true` entry that lacks one (`core/registry.py`);
* every decision is written to `sources/registry.json` immediately, so the work is resumable
  and a crash at source 90 does not cost the previous 89;
* prioritisation (`--federal-first`, `--jurisdiction`, `--document-class`) so the passport and
  Social Security pages — the highest-traffic, highest-consequence entries — can be done first
  rather than after forty state DMVs.

**What this module deliberately does not do.** It does not judge. It fetches the page, shows
the human what the page says about itself, and writes down what the human decided. There is no
scoring, no "likely correct" hint, no auto-confirm-if-the-title-matches — a heuristic that
pre-answers the question is a classification wearing a hat (`docs/RESPONSIBLE-TECH-AUDITS.md`
§B), and it would be right often enough to be trusted and wrong often enough to hurt someone.

**And one thing it has to do, because for a while it did not (issue #18).** A confirmation
written here has to be a confirmation the eligibility predicate will *accept*. It was not:
`_block()` wrote `status`, `verifier`, `at` and `note`, while `core/eligibility.py` also
requires an evidence reference and an in-date recheck expiry. Measured, the consequence was
that a volunteer could work all 152 sources — the README's "about three and a half hours, and
the most valuable three and a half hours anyone could spend on this repo" — and the attempt
denominator stayed at zero, with the published site headlining *all 152 sources are
human-verified* over a feed that would never fill. The work was real, it changed nothing, and
nothing in the tool said so.

So a confirmation now records all four facts, and two of them are produced rather than
demanded:

* the **evidence reference** points at a receipt this module writes at the moment of the
  decision — the URL fetched, when, the HTTP status, the page's own `<title>`, the excerpt the
  verifier actually read, and the content hash of the bytes behind it. That is a true record of
  what was in front of the human, which is what evidence means here; asking a volunteer to type
  a path to a file they have not made would produce a plausible string and no receipt. A source
  our crawler cannot fetch gets a receipt saying exactly that, claiming no title and no text,
  because the honest evidence for `ssa.gov` is "we could not see it; a person opened it
  themselves".
* the **recheck expiry** is dated forward from the decision by
  :data:`VERIFICATION_RECHECK_DAYS`, so a verification can go stale rather than standing
  forever on one afternoon's work.

What this module still cannot produce is the *fetch-policy* decision — whether a host's
robots.txt and terms permit watching it at all. That is a reading of somebody's terms of
service, it is not visible in a page's bytes, and `sentinel sources policy` records it from a
named reviewer rather than inferring it. Both are required before a source is watched, and the
queue now says which is missing instead of letting a verifier find out from an empty feed.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from id_churn_sentinel.core.eligibility import evaluate_source, parse_as_of
from id_churn_sentinel.core.fetch import Fetcher, FetchResult
from id_churn_sentinel.core.normalize import content_hash, excerpt, page_title
from id_churn_sentinel.core.registry import (
    FETCH_POLICY_ALLOW,
    FETCH_POLICY_DENY,
    REJECTED,
    VERIFIED,
    FetchPolicyDecision,
    Registry,
    Source,
    Verification,
    dump_registry_text,
    load_registry,
)
from id_churn_sentinel.errors import RegistryError, VerificationError

__all__ = [
    "DEFAULT_EVIDENCE_DIR",
    "FETCH_POLICY_RECHECK_DAYS",
    "VERIFICATION_RECHECK_DAYS",
    "Candidate",
    "VerifyOutcome",
    "confirm",
    "pending",
    "recheck_date",
    "record_fetch_policy",
    "reject",
    "residual_ineligibility",
    "review_card",
    "run_verification",
    "today",
    "unwatchable_after_confirmation",
    "write_verification_receipt",
]

# The gap reason a rejection uses when the entry is moved out of the registry entirely. It is
# a member of the closed `GAP_REASONS` vocabulary, and it means something none of the others
# do: nothing blocked us, the fetch was fine — a *human* looked and said this is the wrong
# page, and no replacement has been found yet.
WRONG_PAGE = "wrong-page"

# How long a human verification stands before the predicate wants it looked at again.
#
# 180 days is a starting guess, not a finding, and it is stated here as one. The two failure
# modes it sits between:
#
#   Too long, and a verification becomes a permanent claim about a page that can be
#   reorganised, redirected or retired at any time — which is the thing this whole project
#   exists to notice about *content*, and it would be odd to grant the URL itself an exemption.
#
#   Too short, and the queue never finishes: a volunteer who spends an afternoon on 152 sources
#   finds a third of them due again before the rest are done, and a queue that outruns the
#   people working it is one they stop working.
#
# Six months is roughly two of this repo's registry-expansion cycles. Re-derive it from real
# URL-rot data when there is any; it is a module constant and a CLI flag precisely so changing
# it is cheap.
VERIFICATION_RECHECK_DAYS = 180

# The same interval for a fetch-policy decision, named separately because the two answer
# different questions and will not stay in step. A stale verification means we may be watching
# a page that moved; a stale fetch-policy decision means we may be fetching a host that has
# since said not to — the second is a claim about somebody else's permission, and this project
# does not hold permissions it has not re-read. Also a starting guess.
FETCH_POLICY_RECHECK_DAYS = 180

# Where verification receipts are written by default. Under `var/`, which is gitignored, and
# deliberately: `docs/05-DATA-AND-EVIDENCE.md` says raw evidence is never *automatically*
# public, and a receipt carries an excerpt of whatever the page happened to be serving. The
# registry records the reference; the operator holds the artifact, exactly as they hold the
# snapshot store. `--evidence-dir` moves it for an operator who decides otherwise.
DEFAULT_EVIDENCE_DIR = Path("var/evidence/verification")


def today() -> str:
    """The date a verification is recorded against, UTC. Not `datetime.now()` inline: a
    verification's date is part of the record, and a record's clock should be one thing."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def recheck_date(at: str, *, days: int = VERIFICATION_RECHECK_DAYS) -> str:
    """The date this verification falls due, dated forward from when the human looked.

    Forward from `at` rather than from today, so re-recording an old decision cannot silently
    extend it, and so a backfilled verification expires on the schedule its evidence earned.
    """
    return (date.fromisoformat(at) + timedelta(days=days)).isoformat()


@dataclass(frozen=True, slots=True)
class Candidate:
    """One source, fetched, ready for a human to look at. Nothing here is a judgment."""

    source: Source
    ok: bool
    status: int | None
    title: str
    text: str
    error: str | None = None
    #: The hash of the normalized text the excerpt was taken from, and when it was fetched.
    #: Carried so the receipt written at decision time can name exactly which bytes the human
    #: was looking at — a receipt that records the excerpt but not what it was cut from is a
    #: paraphrase, and this project does not treat a paraphrase as evidence.
    content_sha256: str = ""
    fetched_at: str = ""

    @classmethod
    def of(cls, source: Source, result: FetchResult) -> Candidate:
        if not result.ok:
            return cls(
                source=source,
                ok=False,
                status=result.status,
                title="",
                text="",
                error=result.error,
                fetched_at=result.fetched_at.isoformat(),
            )
        digest, normalized = content_hash(result.body, result.content_type)
        return cls(
            source=source,
            ok=True,
            status=result.status,
            title=page_title(result.body),
            text=excerpt(normalized),
            content_sha256=digest,
            fetched_at=result.fetched_at.isoformat(),
        )


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    """What one `sentinel verify` session did. Counts, not opinions.

    `attempt_eligible` and `blocked_reasons` are the half that was missing (issue #18). A
    session used to end with "152 confirmed, 0 still unverified" and no way to learn that the
    watcher would still attempt nothing — the number a volunteer actually cares about, and the
    only one that says whether the afternoon changed anything.
    """

    confirmed: int = 0
    rejected: int = 0
    skipped: int = 0
    remaining: int = 0
    attempt_eligible: int = 0
    registry_total: int = 0
    blocked_reasons: tuple[tuple[str, int], ...] = ()

    def summary(self) -> str:
        return (
            f"{self.confirmed} confirmed, {self.rejected} rejected, {self.skipped} skipped; "
            f"{self.remaining} still unverified"
        )

    def eligibility_summary(self) -> str:
        """What the registry will actually watch after this session, and what is stopping it.

        Printed whether or not anything is blocked: a volunteer who finishes the queue needs to
        be told the number is now non-zero just as much as they need to be told it is not.
        """
        head = (
            f"{self.attempt_eligible} of {self.registry_total} source(s) are attempt-eligible "
            f"— the watcher will attempt exactly these."
        )
        if not self.blocked_reasons:
            return head
        lines = [
            head,
            "  Still blocked, and by what. Verification is one of two decisions a source needs;",
            "  the other is the dated robots/terms fetch-policy review, which is a person's",
            "  reading of somebody's terms and cannot be inferred from a page:",
        ]
        lines.extend(f"    {reason}: {count}" for reason, count in self.blocked_reasons)
        lines.append("  Record a fetch-policy decision with: sentinel sources policy --help")
        return "\n".join(lines)


def pending(
    registry: Registry,
    *,
    jurisdiction: str | None = None,
    document_class: str | None = None,
    federal_first: bool = False,
    limit: int | None = None,
) -> tuple[Source, ...]:
    """The queue: sources no human has ruled on yet, in the order they should be worked.

    Prioritisation is not a nicety. 152 sources is several hours, it will be done in sittings,
    and *which* sittings happen first decides what is trustworthy at the end of the first one.
    `--federal-first` puts passport and Social Security at the top because they are the
    entries every jurisdiction's readers depend on; `--jurisdiction` lets a volunteer who
    knows one state verify the state they know, which is the only kind of volunteer worth
    having here.
    """
    queue = [s for s in registry.sources if s.verification_status not in {VERIFIED, REJECTED}]
    if jurisdiction:
        key = jurisdiction.upper()
        queue = [s for s in queue if s.jurisdiction == key]
    if document_class:
        queue = [s for s in queue if s.document_class == document_class]

    def sort_key(source: Source) -> tuple[int, str, str, str]:
        federal = 0 if (federal_first and source.jurisdiction == "US") else 1
        return (federal, source.jurisdiction, source.document_class, source.id)

    queue.sort(key=sort_key)
    return tuple(queue[:limit] if limit else queue)


def residual_ineligibility(source: Source, *, as_of: date | None = None) -> tuple[str, ...]:
    """What would STILL keep this source out of the attempt denominator after a confirmation.

    Derived, never listed by hand: the block :func:`_block` would actually write is applied to
    a copy of the source and run through the canonical predicate, so this cannot drift from
    what the predicate enforces.

    This exists because working the whole queue does not make the tool watch anything, and
    nothing told the person who did the work (issue #18). `confirm()` writes `status`,
    `verifier`, `at` and `note` — by design; it is the only writer of `verified: true`. The
    predicate additionally requires a verification `evidence` reference and a recheck
    `expires_at`, plus a dated fetch-policy decision that nothing in `src/` writes at all. So a
    volunteer can burn down all 152, watch the site render "All 152 sources are human-verified",
    and still have an attempt denominator of zero and a feed that will stay empty.

    The predicate is behaving exactly as designed and is not relaxed here by one inch. What is
    added is the sentence the tooling owed the volunteer: *this is not the last step, and here
    is what is left.* A queue that cannot tell you it is not the last step wastes an afternoon.
    """
    confirmed = replace(
        source,
        verified=True,
        verification=Verification(
            status=VERIFIED,
            verifier=source.verification.verifier or "a named human",
            at=source.verification.at or today(),
            note=source.verification.note,
            evidence=source.verification.evidence,
            expires_at=source.verification.expires_at,
        ),
    )
    decision = evaluate_source(confirmed, as_of=as_of or parse_as_of(today()))
    return decision.reasons


def unwatchable_after_confirmation(
    sources: Iterable[Source], *, as_of: date | None = None
) -> dict[str, int]:
    """Reason → how many of `sources` a confirmation alone would leave blocked. Empty when
    confirming really is the last step, so the warning disappears by itself on the day it
    stops being true rather than being switched off by hand."""
    counts: dict[str, int] = {}
    for source in sources:
        for reason in residual_ineligibility(source, as_of=as_of):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def review_card(candidate: Candidate, *, position: int, total: int) -> str:
    """The screen a verifier reads. One question, and everything needed to answer it."""
    source = candidate.source
    lines = [
        "",
        "─" * 78,
        f"[{position}/{total}]  {source.jurisdiction} · {source.document_class}",
        f"  source id:   {source.id}",
        f"  authority:   {source.authority}",
        f"  URL:         {source.url}",
    ]
    if candidate.ok:
        lines.append(f"  HTTP status: {candidate.status}")
        lines.append(f"  PAGE TITLE:  {candidate.title or '(the page has no <title>)'}")
        lines.append("")
        lines.append("  --- what the page's normalized text begins with ---")
        lines.extend(f"  | {line}" for line in (candidate.text or "(no text)").split("\n"))
    else:
        # An unfetchable source can still be verified — `ssa.gov` 403s every client we own and
        # its URL is still the right URL. What the human loses is our evidence, so we say so
        # rather than quietly showing an empty card and letting them assume the page is blank.
        lines.append(f"  FETCH FAILED: {candidate.error}")
        lines.append("")
        lines.append("  We could not fetch this page, so there is no title and no text to show")
        lines.append("  you. That does NOT mean the URL is wrong (ssa.gov 403s every client we")
        lines.append("  have and its URL is correct). Open it in a browser before you answer.")
    lines.append("")
    lines.append(f"  notes:       {source.notes[:300]}")
    lines.append("")
    lines.append("  THE QUESTION: is this URL the official page for this document class in this")
    lines.append("  jurisdiction? You are NOT judging what the law says. See docs/VERIFYING.md.")
    return "\n".join(lines)


# ---- writing the decision back into the registry -----------------------------------------


def _load_raw(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RegistryError("registry must be a JSON object")
    return raw


def _entry(raw: dict[str, Any], source_id: str) -> dict[str, Any]:
    for entry in raw.get("sources", []):
        if isinstance(entry, dict) and entry.get("id") == source_id:
            return entry
    raise RegistryError(f"unknown source id: {source_id!r}")


def _write(path: Path, raw: dict[str, Any]) -> None:
    """Write, then *load it back through the validator*. A verification that leaves the
    registry unloadable would be discovered at the next `make verify` — after the verifier
    has done another forty of them."""
    path.write_text(dump_registry_text(raw), encoding="utf-8")
    load_registry(path)


def write_verification_receipt(
    candidate: Candidate,
    *,
    verifier: str,
    at: str,
    directory: Path = DEFAULT_EVIDENCE_DIR,
) -> Path:
    """Write the receipt of what one human was shown, and return the path to reference.

    This is the evidence a verification cites. It records the page as our crawler found it at
    the moment of the decision — not as we would summarise it later — and for a source we could
    not fetch it records *that*, with the literal error and no title and no text. An
    unfetchable source is still verifiable (`ssa.gov` 403s every client we own and its URL is
    correct); what changes is whose eyes the evidence came from, and the receipt has to say so
    rather than leaving a reader to assume we saw the page.
    """
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / f"{candidate.source.id}-{at}.json"
    payload: dict[str, Any] = {
        "receipt_version": "1.0",
        "kind": "source-verification",
        "source_id": candidate.source.id,
        "jurisdiction": candidate.source.jurisdiction,
        "document_class": candidate.source.document_class,
        "authority": candidate.source.authority,
        "url": candidate.source.url,
        "verifier": verifier,
        "verified_at": at,
        "fetched_at": candidate.fetched_at,
        "fetch_ok": candidate.ok,
        "http_status": candidate.status,
        "page_title": candidate.title,
        "normalized_text_excerpt": candidate.text,
        "content_sha256": candidate.content_sha256,
        "fetch_error": candidate.error or "",
        "statement": (
            f"{verifier} was shown this page's own title and the excerpt above on {at}, and "
            "confirmed the URL is the official page for this document class in this "
            "jurisdiction. It is a record of what was in front of a human. It is not a claim "
            "about what the law says."
            if candidate.ok
            else (
                f"Our crawler could not fetch this URL, so no title, text or hash was captured. "
                f"{verifier} confirmed it on {at} from outside this tool — a browser, or the "
                "authority directly. The evidence for this decision is theirs, not ours, and "
                "this receipt records that rather than implying we saw the page."
            )
        ),
    }
    receipt.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return receipt


def confirm(
    path: Path,
    source_id: str,
    *,
    verifier: str,
    evidence: str,
    expires_at: str = "",
    at: str | None = None,
    note: str = "",
) -> Verification:
    """Record that a NAMED human confirmed this URL is the official page.

    The only writer of `verified: true` in this codebase, and it refuses to run without a name
    — and now, for the same reason, without an evidence reference. A verification missing
    either one is not a weaker verification; it is one the eligibility predicate discards, so
    recording it would tell a volunteer their afternoon counted when it did not (issue #18).

    `expires_at` defaults to :func:`recheck_date` rather than to blank, because blank is the
    value the predicate refuses and "no expiry" is the value that would let one afternoon stand
    forever.
    """
    if not verifier.strip():
        raise VerificationError(
            "a verification requires the name of the human who did it. `verified: true` means "
            "'a person opened this URL and confirmed it is the official page' — with no name, "
            "it means nothing, and it is worse than `false` because it will be believed."
        )
    if not evidence.strip():
        raise VerificationError(
            "a verification requires an evidence reference — what was in front of the human "
            "when they decided. Without one the eligibility predicate discards the whole "
            "record, so the source stays unwatched and the registry claims otherwise. "
            "`sentinel verify` writes the receipt for you; pass its path here."
        )
    decided_at = at or today()
    verification = Verification(
        status=VERIFIED,
        verifier=verifier.strip(),
        at=decided_at,
        note=note,
        evidence=evidence.strip(),
        expires_at=expires_at.strip() or recheck_date(decided_at),
    )
    raw = _load_raw(path)
    entry = _entry(raw, source_id)
    entry["verified"] = True
    entry["verification"] = _block(verification)
    _write(path, raw)
    return verification


def reject(
    path: Path,
    source_id: str,
    *,
    verifier: str,
    reason: str,
    at: str | None = None,
    to_gap: bool = False,
) -> Verification:
    """Record that a named human found this URL is *not* the official page.

    Two outcomes, and the tool will not choose between them:

    * **flag for repair** (the default) — the entry stays, carrying its rejection, its reason
      and the name of whoever found it. It is rendered as REJECTED everywhere it is published,
      so a consumer cannot pick it up in the window before it is fixed. A wrong entry that is
      *known* to be wrong is far safer than one quietly deleted, because the deletion takes
      the finding with it.
    * **move to the gap list** (`--gap`) — for when there is no right page to substitute. The
      entry leaves `sources` and becomes a named `Gap` with reason `wrong-page`, which is what
      the gap list is for: *"we do not watch this, and here is why."*

    The gap move refuses if the (jurisdiction, document class) pair would still be watched by
    another source, because a gap that claims we are blind to something we can see is a false
    confession — and the completeness gate would (correctly) fail the build for it.
    """
    if not verifier.strip():
        raise VerificationError(
            "a rejection requires the name of the human who made it — the same rule as a "
            "confirmation, for the same reason: it is a judgment, and judgments are signed."
        )
    if not reason.strip():
        raise VerificationError(
            "a rejection requires a reason. 'Wrong page' with no explanation is not a finding, "
            "it is a shrug, and the next person to look at this entry has to redo the work."
        )
    verification = Verification(
        status=REJECTED, verifier=verifier.strip(), at=at or today(), note=reason.strip()
    )
    raw = _load_raw(path)
    entry = _entry(raw, source_id)

    if to_gap:
        _move_to_gap(raw, entry, verification)
    else:
        entry["verified"] = False
        entry["verification"] = _block(verification)
    _write(path, raw)
    return verification


def _move_to_gap(raw: dict[str, Any], entry: dict[str, Any], verification: Verification) -> None:
    jurisdiction = str(entry["jurisdiction"])
    document_class = str(entry["document_class"])
    still_watched = [
        other
        for other in raw.get("sources", [])
        if isinstance(other, dict)
        and other is not entry
        and other.get("jurisdiction") == jurisdiction
        and other.get("document_class") == document_class
    ]
    if still_watched:
        raise VerificationError(
            f"refusing to record {jurisdiction}/{document_class} as a GAP: "
            f"{len(still_watched)} other source(s) still watch that pair, so it is not a gap. "
            f"A gap that claims we are blind to something we can see is a false confession — "
            f"a consumer reading it goes looking elsewhere for information we already have. "
            f"Reject it for repair instead (drop --gap), or fix its URL."
        )

    host = urlsplit(str(entry["url"])).hostname
    if host is None:
        raise VerificationError(
            f"refusing to move {entry['url']!r} to a gap because it has no parseable host"
        )

    raw["sources"] = [s for s in raw.get("sources", []) if s is not entry]
    gaps = raw.setdefault("gaps", [])
    gaps.append(
        {
            "jurisdiction": jurisdiction,
            "document_class": document_class,
            "reason": WRONG_PAGE,
            "hosts": [host],
            "checked": verification.at,
            "detail": (
                f"Rejected by {verification.verifier} on {verification.at} during human "
                f"verification: {verification.note} The URL we had seeded was "
                f"{entry['url']} — it fetched, but it is not the official page for this "
                f"document class. No replacement has been found, so this pair is not watched."
            ),
        }
    )
    gaps.sort(key=lambda g: (str(g.get("jurisdiction")), str(g.get("document_class"))))


def _block(verification: Verification) -> dict[str, str]:
    """The on-disk shape. `statement` is derived at publish time, never stored — a stored
    sentence is a sentence that can drift from the fields it describes.

    `evidence` and `expires_at` are written whenever they are set, and a confirmation cannot be
    recorded without them (issue #18): this function was the only writer of a verification in
    the codebase and it omitted exactly the two fields `core/eligibility.py` requires, so every
    record it produced was refused by the predicate that decides whether a source is watched.
    """
    block = {
        "status": verification.status,
        "verifier": verification.verifier,
        "at": verification.at,
    }
    if verification.note:
        block["note"] = verification.note
    if verification.evidence:
        block["evidence"] = verification.evidence
    if verification.expires_at:
        block["expires_at"] = verification.expires_at
    return block


# ---- the fetch-policy decision (SRC-03) -----------------------------------------------------
#
# The second of the two decisions a source needs, and until now the one nothing in `src/` could
# write. `grep -rn fetch_policy src/` found it parsed in `registry.py`, read in `eligibility.py`
# and published in `publish.py` — eleven hits, no writer — so the only way to satisfy the
# predicate was to hand-edit `sources/registry.json`, which `docs/VERIFYING.md` never mentions
# and no volunteer would guess.


def record_fetch_policy(
    path: Path,
    source_id: str,
    *,
    outcome: str,
    reviewer: str,
    reason: str,
    evidence: str,
    at: str | None = None,
    expires_at: str = "",
) -> FetchPolicyDecision:
    """Record a NAMED human's dated robots/terms decision for one source.

    Why this is a writer and not a check: whether a host's robots.txt and terms of service
    permit a weekly watch is a reading of somebody else's document, and `HttpFetcher` obeying
    robots at request time is not that reading — it is one rule, mechanically applied, at one
    moment. `allow` here means a person read the policy and decided; the tool records who,
    when, on what evidence, for what stated reason, and until when. It never infers one, and an
    absent decision stays `unreviewed`, which the predicate refuses.

    Every field is required for a terminal outcome, and that is the registry's rule rather than
    this function's: `_validate_policy_decision` refuses a blank one on load, so a decision
    written without them would make the registry unloadable at the next `make verify` — after
    the reviewer had done another forty.
    """
    if outcome not in {FETCH_POLICY_ALLOW, FETCH_POLICY_DENY}:
        raise VerificationError(
            f"fetch-policy outcome must be {FETCH_POLICY_ALLOW!r} or {FETCH_POLICY_DENY!r}; "
            f"got {outcome!r}. `unreviewed` is not something to record — it is the absence of "
            "this decision, and it is what the registry already says."
        )
    missing = [
        name
        for name, value in (
            ("reviewer", reviewer),
            ("reason", reason),
            ("evidence", evidence),
        )
        if not value.strip()
    ]
    if missing:
        raise VerificationError(
            f"a fetch-policy decision requires {', '.join(missing)}. This is a human's reading "
            "of a host's robots.txt and terms, and a reading with no reader, no reason, or no "
            "evidence behind it is indistinguishable from an assumption — which is exactly what "
            "`unreviewed` already means."
        )
    decided_at = at or today()
    decision = FetchPolicyDecision(
        outcome=outcome,
        reviewer=reviewer.strip(),
        at=decided_at,
        expires_at=expires_at.strip() or recheck_date(decided_at, days=FETCH_POLICY_RECHECK_DAYS),
        evidence=evidence.strip(),
        reason=reason.strip(),
    )
    raw = _load_raw(path)
    entry = _entry(raw, source_id)
    entry["fetch_policy"] = decision.to_dict()
    _write(path, raw)
    return decision


# ---- the interactive session ---------------------------------------------------------------


def run_verification(
    registry: Registry,
    path: Path,
    fetcher: Fetcher,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    *,
    verifier: str = "",
    jurisdiction: str | None = None,
    document_class: str | None = None,
    federal_first: bool = False,
    limit: int | None = None,
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR,
    as_of: date | None = None,
) -> VerifyOutcome:
    """Work the queue, one source at a time. Resumable by construction: every decision is
    written to the registry the moment it is made, and a source with a decision is not offered
    again — so the session's state lives in the committed file rather than in a lockfile
    nobody would remember to clean up."""
    queue = pending(
        registry,
        jurisdiction=jurisdiction,
        document_class=document_class,
        federal_first=federal_first,
        limit=limit,
    )
    total_unverified = len(registry.unverified)
    if not queue:
        say("verify: nothing pending — every source in this selection has been ruled on.")
        return _outcome(path, remaining=total_unverified, as_of=as_of)

    say(
        f"verify: {len(queue)} source(s) to review "
        f"({total_unverified} of {len(registry)} in the registry are unverified).\n"
        f"You are answering ONE question per source: is this URL the official page for this\n"
        f"document class in this jurisdiction? You are not judging what the law says.\n"
        f"Keys: [y] yes  [n] no  [s] skip  [q] quit (progress is saved as you go).\n"
        f"Confirming writes a receipt of what you were shown to {evidence_dir}/ and records a\n"
        f"recheck date {VERIFICATION_RECHECK_DAYS} days out. Verifying a source is one of the\n"
        f"two decisions it needs before it is watched — the summary at the end says what is\n"
        f"still missing, and it is not a judgment you make here."
    )

    confirmed = rejected = skipped = 0
    for position, source in enumerate(queue, start=1):
        candidate = Candidate.of(source, fetcher.fetch(source.url))
        say(review_card(candidate, position=position, total=len(queue)))

        answer = ask("  official page for this document class? [y/n/s/q] ").strip().lower()
        if answer in {"q", "quit"}:
            say("verify: stopping. Everything decided so far is already written to the registry.")
            break
        if answer in {"", "s", "skip"}:
            skipped += 1
            continue

        if answer in {"y", "yes"}:
            name = verifier or ask("  your name (recorded in the registry, required): ")
            decided_at = today()
            try:
                # The receipt is written before the registry entry, and its path is what the
                # entry cites. The other order would let a crash between the two leave a
                # verification pointing at evidence that does not exist.
                receipt = write_verification_receipt(
                    candidate, verifier=name.strip(), at=decided_at, directory=evidence_dir
                )
                recorded = confirm(
                    path,
                    source.id,
                    verifier=name,
                    evidence=str(receipt),
                    at=decided_at,
                )
            except VerificationError as exc:
                say(f"  REFUSED: {exc}")
                skipped += 1
                continue
            confirmed += 1
            say(f"  recorded: {recorded.label}")
            say(f"    evidence: {recorded.evidence}")
            say(f"    recheck due: {recorded.expires_at}")
            continue

        if answer in {"n", "no"}:
            name = verifier or ask("  your name (recorded in the registry, required): ")
            reason = ask("  why is this not the official page? (required): ")
            gap = ask("  no right page exists to swap in — record as a GAP? [y/N] ").strip().lower()
            try:
                recorded = reject(
                    path,
                    source.id,
                    verifier=name,
                    reason=reason,
                    to_gap=gap in {"y", "yes"},
                )
            except VerificationError as exc:
                say(f"  REFUSED: {exc}")
                skipped += 1
                continue
            rejected += 1
            say(f"  recorded: {recorded.label}")
            continue

        say("  unrecognised answer — skipped. (y = yes, n = no, s = skip, q = quit)")
        skipped += 1

    reloaded = load_registry(path)
    return _outcome(
        path,
        confirmed=confirmed,
        rejected=rejected,
        skipped=skipped,
        remaining=len(reloaded.unverified),
        as_of=as_of,
        registry=reloaded,
    )


def _outcome(
    path: Path,
    *,
    confirmed: int = 0,
    rejected: int = 0,
    skipped: int = 0,
    remaining: int = 0,
    as_of: date | None = None,
    registry: Registry | None = None,
) -> VerifyOutcome:
    """Close a session with the number the volunteer came for: what is actually watched now.

    Derived from the registry as it stands on disk after the session, through the same
    predicate the watcher and publisher use — not from the session's own counters, which can
    only ever say how much work was done, never whether it landed (issue #18).
    """
    loaded = registry if registry is not None else load_registry(path)
    today_utc = as_of or datetime.now(UTC).date()
    decisions = [evaluate_source(source, as_of=today_utc) for source in loaded.sources]
    eligible = [decision for decision in decisions if decision.eligible]
    blocked = Counter(
        reason for decision in decisions if not decision.eligible for reason in decision.reasons
    )
    return VerifyOutcome(
        confirmed=confirmed,
        rejected=rejected,
        skipped=skipped,
        remaining=remaining,
        attempt_eligible=len(eligible),
        registry_total=len(loaded),
        blocked_reasons=tuple(sorted(blocked.items())),
    )
