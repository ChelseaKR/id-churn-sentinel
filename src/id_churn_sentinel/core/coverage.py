"""Coverage — every number this project says about itself, derived from the registry.

**The problem this solves is not arithmetic. It is honesty under maintenance.**

A README that says "131 sources across 50 of 52 jurisdictions" is a claim, and the moment
someone adds a source it becomes a *false* claim — silently, in the one document every
reader trusts most. Worse, the failure is asymmetric in exactly the direction that hurts:
nobody notices when the stated coverage is too *low*, and everybody acts on it when it is
too *high*. A project whose entire pitch is "we tell you what went stale" cannot be a
project whose own front page went stale.

So no number about coverage is written by hand. They are all derived from
`sources/registry.json` by :func:`coverage`, and `sentinel coverage --check-docs` re-derives
them and fails the build if any doc disagrees. This mirrors `gate-count` in the sibling repo
trans-docs-navigator, which does the same thing for the gate count: *a self-description is a
fact about the code, so compute it from the code.*

**And one invariant that is worth more than all the counting.** Every (state, core document
class) pair is either a watched source or a *named gap*, and the gate proves it both ways:

* a missing pair that is not a named gap **fails the build** — that is a silent thin spot,
  and it is exactly what the gap list exists to prevent;
* a named gap that is *not* actually missing also fails — a stale gap tells a consumer we
  are blind to something we now watch, and understating coverage is not a harmless error
  when someone is deciding whether to trust the feed's silence.

That invariant found two holes the day it was written: **DC** had no court-order name-change
source and **RI** had none either, and neither was in the hand-written gap list. They were
not decisions; they were omissions wearing the costume of decisions.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from id_churn_sentinel.core.registry import (
    CORE_STATE_DOCUMENT_CLASSES,
    JURISDICTIONS,
    Gap,
    Registry,
    Source,
)

__all__ = [
    "DOC_PATHS",
    "CoverageReport",
    "check_docs",
    "completeness_violations",
    "coverage",
    "repo_root",
]


def repo_root() -> Path:
    """The repo root (this file's great-grandparent)."""
    return Path(__file__).resolve().parents[3]


# The documents that describe this project to a reader. Every gated number in each of them
# must equal what the registry actually says. Add a doc that makes a coverage claim, add it
# here — a claim nobody checks is a claim that will eventually be wrong.
DOC_PATHS: tuple[str, ...] = (
    "README.md",
    "docs/ROADMAP.md",
    "docs/CONSUMERS.md",
    "docs/RESPONSIBLE-TECH-AUDITS.md",
    # The verifier's own instructions. It states how many sources are still unverified, which
    # is the number a volunteer decides whether to help on — and the number most likely to be
    # left stale *after someone has done the work*, which would quietly tell the next person
    # the queue was never started.
    "docs/VERIFYING.md",
    "sources/registry.json",
)

# The gated phrases. Deliberately few, deliberately rigid: a doc must state coverage in one
# of these exact shapes or not at all, so that "how many sources are there" has precisely
# one grammar and cannot be smuggled past the gate in a synonym.
#
# The corollary, which bit this gate's own author within an hour of writing it: **the gated
# grammar is reserved for LIVE CLAIMS about what we watch now.** Narrating history — "the gap
# list shrank from 21 holes to 12" — must not use it, and that is a feature rather than an
# irritation. A reader skimming a sentence containing the words "21 named gaps" cannot tell
# whether it is a current claim or a memoir; neither can the gate; so the two get different
# grammar, and the one that matters is the one that is checked.
_UNREACHABLE_RE = re.compile(
    r"\b(\d+) of the (\d+) registered sources cannot currently be fetched\b"
)
# The verification burn-down, in the gated grammar, for the same reason as every other number
# here: it is the claim a reader most needs to be true, it moves every time a human works the
# queue, and a README that still says "0 of 152 are human-verified" after forty have been
# verified is *understating* — which sounds harmless and is not, because the next maintainer
# reads it and concludes the work never started. It is scrubbed before `_SOURCES_RE` runs, so
# the "152 sources" inside it is not double-counted as a coverage claim.
_VERIFIED_RE = re.compile(r"\b(\d+) of (\d+) sources are human-verified\b")
_SOURCES_RE = re.compile(r"\b(\d+) sources\b")
_GAPS_RE = re.compile(r"\b(\d+) named gaps?\b")


def _jurisdictions_re(total: int) -> re.Pattern[str]:
    """`N of 52 jurisdictions` — anchored to OUR denominator, on purpose.

    The docs also quote *other people's* coverage ("Namesake fully supports 2 of 51
    jurisdictions"), and those are quoted claims about a third party with a different
    denominator — 51, because they do not carry a federal bucket. A gate that "corrected"
    Namesake's number to ours would be rewriting a citation to make our own arithmetic
    work, which is a considerably worse sin than the drift it is trying to prevent. So the
    total is baked in from the closed `JURISDICTIONS` set: a claim about *us* is a claim
    against 52, and anything else is somebody else's number and none of this gate's business.
    """
    return re.compile(rf"\b(\d+) of {total} jurisdictions\b")


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """What the registry actually contains. Nothing here is typed by a human."""

    sources_total: int
    jurisdictions_covered: int
    jurisdictions_total: int
    gaps_total: int
    unreachable_total: int
    unverified_total: int
    verified_total: int
    rejected_total: int
    by_document_class: tuple[tuple[str, int], ...]
    by_reason: tuple[tuple[str, int], ...]
    gaps: tuple[Gap, ...]
    unreachable: tuple[Source, ...]

    def lines(self) -> list[str]:
        """The human-readable summary `sentinel coverage` prints — including the burn-down,
        which is the number this project is currently least entitled to be quiet about."""
        out = [
            f"sources:        {self.sources_total}",
            f"jurisdictions:  {self.jurisdictions_covered} of {self.jurisdictions_total}",
            f"named gaps:     {self.gaps_total}  "
            f"({', '.join(f'{r}={n}' for r, n in self.by_reason)})",
            f"watched in name only (registered, unreachable): {self.unreachable_total}",
            "",
            "HUMAN VERIFICATION (the burn-down — see docs/VERIFYING.md):",
            f"  {self.verified_total} of {self.sources_total} sources are human-verified",
            f"  unverified (machine-checked only, NOT human-confirmed): {self.unverified_total}",
            f"  rejected by a human (wrong page — flagged for repair): {self.rejected_total}",
            f"  named gaps (deliberately not watched):                 {self.gaps_total}",
            "",
            "by document class:",
        ]
        out.extend(f"  {cls:<24} {count}" for cls, count in self.by_document_class)
        return out


def coverage(registry: Registry) -> CoverageReport:
    """Derive every coverage number this project publishes about itself."""
    by_class = Counter(source.document_class for source in registry.sources)
    by_reason = Counter(gap.reason for gap in registry.gaps)
    return CoverageReport(
        sources_total=len(registry.sources),
        jurisdictions_covered=len(registry.jurisdictions),
        jurisdictions_total=len(JURISDICTIONS),
        gaps_total=len(registry.gaps),
        unreachable_total=len(registry.unreachable),
        unverified_total=len(registry.unverified),
        verified_total=len(registry.verified_sources),
        rejected_total=len(registry.rejected),
        by_document_class=tuple(sorted(by_class.items())),
        by_reason=tuple(sorted(by_reason.items())),
        gaps=registry.gaps,
        unreachable=registry.unreachable,
    )


def completeness_violations(registry: Registry) -> list[str]:
    """Prove the gap list and the source list agree about what is not watched.

    Returns a list of violations; empty means the registry is honest about its own holes.
    Both directions are checked, and the second one matters more than it looks: a gap that
    claims we are blind to something we actually watch is a *false confession*, and a
    consumer who reads it will go looking elsewhere for information we already have.
    """
    watched: dict[str, set[str]] = {}
    for source in registry.sources:
        watched.setdefault(source.jurisdiction, set()).add(source.document_class)

    states = JURISDICTIONS - {"US"}  # federal classes are not owed by a state
    missing = {
        (jurisdiction, document_class)
        for jurisdiction in states
        for document_class in CORE_STATE_DOCUMENT_CLASSES
        if document_class not in watched.get(jurisdiction, set())
    }
    named = {(gap.jurisdiction, gap.document_class) for gap in registry.gaps}

    violations = [
        f"{jurisdiction}/{document_class} is not watched and is NOT a named gap — a silent "
        f"thin spot. Add a source, or add a gap that says who refused us and why."
        for jurisdiction, document_class in sorted(missing - named)
    ]
    violations.extend(
        f"{jurisdiction}/{document_class} is a named gap but IS watched — a stale gap tells "
        f"a consumer we are blind to something we can see. Delete the gap."
        for jurisdiction, document_class in sorted(named - missing)
    )
    return violations


def _check_one_doc(relative: str, text: str, report: CoverageReport) -> list[str]:
    """Every gated number in one document, checked against the registry."""
    drifts: list[str] = []
    found = False

    # The unreachable phrase contains the substring "registered sources", and the verified
    # phrase contains "N sources", so both are matched and blanked FIRST; otherwise
    # `_SOURCES_RE` would read their numbers and report a drift that is really an overlap in
    # our own regexes.
    for match in _UNREACHABLE_RE.finditer(text):
        found = True
        if (int(match.group(1)), int(match.group(2))) != (
            report.unreachable_total,
            report.sources_total,
        ):
            drifts.append(
                f"{relative}: {match.group(0)!r} — registry says "
                f"{report.unreachable_total} of {report.sources_total}"
            )
    for match in _VERIFIED_RE.finditer(text):
        found = True
        if (int(match.group(1)), int(match.group(2))) != (
            report.verified_total,
            report.sources_total,
        ):
            drifts.append(
                f"{relative}: {match.group(0)!r} — registry says "
                f"{report.verified_total} of {report.sources_total} sources are human-verified"
            )
    scrubbed = _VERIFIED_RE.sub("", _UNREACHABLE_RE.sub("", text))

    checks = (
        (_SOURCES_RE, report.sources_total, "registry has"),
        (
            _jurisdictions_re(report.jurisdictions_total),
            report.jurisdictions_covered,
            f"registry covers, of {report.jurisdictions_total},",
        ),
        (_GAPS_RE, report.gaps_total, "registry names"),
    )
    for pattern, expected, verb in checks:
        for match in pattern.finditer(scrubbed):
            found = True
            if int(match.group(1)) != expected:
                drifts.append(f"{relative}: {match.group(0)!r} — {verb} {expected}")

    if not found:
        # A doc that stops describing coverage at all is how this gate gets defeated without
        # anyone lying: delete the sentence, and the check has nothing left to check.
        drifts.append(
            f"{relative}: states no coverage numbers at all — it is in DOC_PATHS because "
            f"it is supposed to. Say what is watched, or take it out of DOC_PATHS."
        )
    return drifts


def check_docs(report: CoverageReport, root: Path | None = None) -> list[str]:
    """Re-derive the numbers and compare them against every doc that states one.

    Returns a list of drifts; empty means no doc lies about the registry.
    """
    base = root or repo_root()
    drifts: list[str] = []
    jurisdictions_re = _jurisdictions_re(report.jurisdictions_total)

    for relative in DOC_PATHS:
        path = base / relative
        if not path.exists():
            drifts.append(f"{relative}: missing — it is in DOC_PATHS but not on disk")
            continue
        drifts.extend(_check_one_doc(relative, path.read_text(encoding="utf-8"), report))

    readme = base / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        for label, pattern in (
            ("N sources", _SOURCES_RE),
            (f"N of {report.jurisdictions_total} jurisdictions", jurisdictions_re),
            ("N named gaps", _GAPS_RE),
            ("N of the N registered sources cannot currently be fetched", _UNREACHABLE_RE),
            # The fifth number, and the one this project is most tempted to leave out: how
            # many of these official-looking URLs a *person* has actually confirmed. A README
            # that states coverage and omits verification is describing a registry that does
            # not exist.
            ("N of N sources are human-verified", _VERIFIED_RE),
        ):
            if not pattern.search(text):
                drifts.append(
                    f"README.md: does not state {label!r} anywhere. The README is the "
                    f"document people believe; it must carry all five numbers."
                )
    return drifts
