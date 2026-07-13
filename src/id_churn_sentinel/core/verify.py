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
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from id_churn_sentinel.core.fetch import Fetcher, FetchResult
from id_churn_sentinel.core.normalize import content_hash, excerpt, page_title
from id_churn_sentinel.core.registry import (
    REJECTED,
    VERIFIED,
    Registry,
    Source,
    Verification,
    dump_registry_text,
    load_registry,
)
from id_churn_sentinel.errors import RegistryError, VerificationError

__all__ = [
    "Candidate",
    "VerifyOutcome",
    "confirm",
    "pending",
    "reject",
    "review_card",
    "run_verification",
    "today",
]

# The gap reason a rejection uses when the entry is moved out of the registry entirely. It is
# a member of the closed `GAP_REASONS` vocabulary, and it means something none of the others
# do: nothing blocked us, the fetch was fine — a *human* looked and said this is the wrong
# page, and no replacement has been found yet.
WRONG_PAGE = "wrong-page"


def today() -> str:
    """The date a verification is recorded against, UTC. Not `datetime.now()` inline: a
    verification's date is part of the record, and a record's clock should be one thing."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One source, fetched, ready for a human to look at. Nothing here is a judgment."""

    source: Source
    ok: bool
    status: int | None
    title: str
    text: str
    error: str | None = None

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
            )
        _, normalized = content_hash(result.body, result.content_type)
        return cls(
            source=source,
            ok=True,
            status=result.status,
            title=page_title(result.body),
            text=excerpt(normalized),
        )


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    """What one `sentinel verify` session did. Counts, not opinions."""

    confirmed: int = 0
    rejected: int = 0
    skipped: int = 0
    remaining: int = 0

    def summary(self) -> str:
        return (
            f"{self.confirmed} confirmed, {self.rejected} rejected, {self.skipped} skipped; "
            f"{self.remaining} still unverified"
        )


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


def confirm(
    path: Path,
    source_id: str,
    *,
    verifier: str,
    at: str | None = None,
    note: str = "",
) -> Verification:
    """Record that a NAMED human confirmed this URL is the official page. The only writer of
    `verified: true` in this codebase, and it refuses to run without a name."""
    if not verifier.strip():
        raise VerificationError(
            "a verification requires the name of the human who did it. `verified: true` means "
            "'a person opened this URL and confirmed it is the official page' — with no name, "
            "it means nothing, and it is worse than `false` because it will be believed."
        )
    verification = Verification(
        status=VERIFIED, verifier=verifier.strip(), at=at or today(), note=note
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

    raw["sources"] = [s for s in raw.get("sources", []) if s is not entry]
    gaps = raw.setdefault("gaps", [])
    gaps.append(
        {
            "jurisdiction": jurisdiction,
            "document_class": document_class,
            "reason": WRONG_PAGE,
            "hosts": [str(entry["url"]).split("/")[2]],
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
    sentence is a sentence that can drift from the fields it describes."""
    block = {
        "status": verification.status,
        "verifier": verification.verifier,
        "at": verification.at,
    }
    if verification.note:
        block["note"] = verification.note
    return block


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
        return VerifyOutcome(remaining=total_unverified)

    say(
        f"verify: {len(queue)} source(s) to review "
        f"({total_unverified} of {len(registry)} in the registry are unverified).\n"
        f"You are answering ONE question per source: is this URL the official page for this\n"
        f"document class in this jurisdiction? You are not judging what the law says.\n"
        f"Keys: [y] yes  [n] no  [s] skip  [q] quit (progress is saved as you go)."
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
            try:
                recorded = confirm(path, source.id, verifier=name)
            except VerificationError as exc:
                say(f"  REFUSED: {exc}")
                skipped += 1
                continue
            confirmed += 1
            say(f"  recorded: {recorded.label}")
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

    remaining = len(load_registry(path).unverified)
    return VerifyOutcome(
        confirmed=confirmed, rejected=rejected, skipped=skipped, remaining=remaining
    )
