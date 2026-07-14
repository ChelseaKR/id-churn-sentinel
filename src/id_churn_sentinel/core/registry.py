"""The source registry — the committed list of official watch targets.

This registry is the tool's entire claim about the world, and it is a deliberately small
one: *"this URL is the official page for this document class in this jurisdiction."* It
does **not** say what the law is, what the process costs, how long it takes, or whether
you qualify. Making a legal assertion is what the incumbent guidance projects do, and
doing it badly is a safety failure; this tool refuses the job entirely and watches URLs.

Two structural rules keep it honest:

* **Closed vocabularies.** `JURISDICTIONS` (50 states + DC + the `US` federal bucket) and
  `DOCUMENT_CLASSES` are closed sets. An entry naming a jurisdiction or document class
  outside them cannot load. There is no free-text jurisdiction field to typo into a
  silently-unwatched source.
* **`verified` starts false, and only a human flips it.** Every seeded entry ships
  `verified: false`. The flag means "a named human loaded this URL and confirmed it is the
  official page it claims to be" — no machine in this codebase decides it. This mirrors the
  VERIFIERS discipline in trans-docs-navigator, where a record with a placeholder verifier
  is treated as unverified content rather than quietly served as fact.

  **And a verification with no name on it is not a verification.** `verified: true` is only
  loadable alongside a `verification` block naming the human and the date
  (:class:`Verification`); a hand-edited `"verified": true` with nothing behind it does not
  load, it raises. That closes the one way this flag could have been flipped without anybody
  standing behind it — including by an agent editing the file. `sentinel verify` is the
  supported path, and it refuses to write a verification without a name.

Entries with `verified: false` are still watched. An unverified URL that changes is still
worth a human's attention; what it is *not* is evidence about the law — and every published
artifact that carries the source says so, in words, next to it.

**And the holes are data, not prose.** A registry that lists what it watches, and describes
what it does *not* watch in a paragraph of English, will drift — the paragraph is written
once and the file is edited forever. Rhode Island and DC were each missing a whole document
class that no gap paragraph mentioned, and nothing could have told us. So `gaps` is now a
structured list with a closed vocabulary of *reasons*, `sentinel coverage` derives every
published number from it, and `sentinel coverage --check-docs` fails the build when a doc
disagrees with the registry. The gap list is a promise; a promise nobody checks is a wish.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from id_churn_sentinel.errors import RegistryError

__all__ = [
    "CORE_STATE_DOCUMENT_CLASSES",
    "DOCUMENT_CLASSES",
    "FETCH_POLICY_ALLOW",
    "FETCH_POLICY_DENY",
    "FETCH_POLICY_OUTCOMES",
    "FETCH_POLICY_UNREVIEWED",
    "GAP_REASONS",
    "JURISDICTIONS",
    "REGISTRY_VERSION",
    "REJECTED",
    "UNVERIFIED",
    "VERIFICATION_STATUSES",
    "VERIFIED",
    "WITHDRAWN",
    "FetchPolicyDecision",
    "Gap",
    "Registry",
    "Source",
    "Verification",
    "default_registry_path",
    "dump_registry_text",
    "load_registry",
]

REGISTRY_VERSION = "1.0"

# The three states a registry entry can be in with respect to *a human having looked*, and a
# fourth that only a published artifact can be in.
#
# These are not decoration. The single most dangerous thing this project publishes is a list
# that reads as *"this is Ohio's official birth-certificate page"* when what it actually
# means is *"a socket returned 200 at a URL somebody plausibly believed was Ohio's official
# birth-certificate page."* Those are different claims, a trans person acting on the second
# one can be sent to the wrong office, and the only thing that converts one into the other is
# a named person opening the page.
UNVERIFIED = "unverified"  # machine-checked (fetched, title read). NOBODY has confirmed it.
VERIFIED = "verified"  # a named human opened the URL and confirmed it, on a given date.
REJECTED = "rejected"  # a named human opened the URL and found it is NOT the official page.
# Not a registry state: what `publish` says about a change record whose source has since left
# the registry. We will not silently omit the status, and we will not invent one.
WITHDRAWN = "withdrawn"

VERIFICATION_STATUSES: frozenset[str] = frozenset({UNVERIFIED, VERIFIED, REJECTED})

FETCH_POLICY_UNREVIEWED = "unreviewed"
FETCH_POLICY_ALLOW = "allow"
FETCH_POLICY_DENY = "deny"
FETCH_POLICY_OUTCOMES: frozenset[str] = frozenset(
    {FETCH_POLICY_UNREVIEWED, FETCH_POLICY_ALLOW, FETCH_POLICY_DENY}
)

# 50 states + DC + `US`, the federal bucket (passport, SSA, Selective Service, and the
# Federal Register itself). 52 keys, not 51: federal document policy is not a state.
JURISDICTIONS: frozenset[str] = frozenset(
    {
        "US",
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
        "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
        "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
        "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
        "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    }
)  # fmt: skip

# The six documents a US legal-name/gender-marker change actually touches. Closed on
# purpose: a seventh would be a product decision, not a config change.
DOCUMENT_CLASSES: frozenset[str] = frozenset(
    {
        "birth_certificate",
        "drivers_license",
        "court_order_name_change",
        "passport",
        "social_security",
        "selective_service",
    }
)

# The three document classes every state is expected to have a source for. Federal classes
# (passport, social_security, selective_service) live under the `US` bucket and are not
# owed by a state; a state missing one of THESE three is a hole, and a hole must be a named
# `Gap` or the coverage gate fails. This is the closed loop that catches a silent thin spot.
CORE_STATE_DOCUMENT_CLASSES: frozenset[str] = frozenset(
    {"birth_certificate", "drivers_license", "court_order_name_change"}
)

# Why we do not watch something. A closed vocabulary, because "we couldn't get it" is not a
# reason — it is an absence of one, and the difference between "their robots.txt forbids us"
# and "their TLS chain does not verify" and "the page is a JavaScript shell" is the whole
# question of whether the gap is ours to fix or theirs.
#
# Every one of these is a refusal we CHOSE. In each case there is a two-line change — spoof a
# browser User-Agent, pass verify=False, ignore robots.txt — that would "close" the gap today
# and make this tool a thing that lies to a government server, about who it is, on behalf of a
# population under surveillance. See docs/RESPONSIBLE-TECH-AUDITS.md §D.
GAP_REASONS: frozenset[str] = frozenset(
    {
        "robots-disallowed",  # their robots.txt forbids us, and we honour it without appeal
        "blocked-403",  # serves a browser, 403s our descriptive UA. We do not spoof one.
        "blocked-404",  # a WAF wearing a 404's clothes (odh.ohio.gov 404s its own site root)
        "blocked-200",  # a WAF wearing a 200's clothes — the nastiest, see below
        "tls-unverifiable",  # a chain our trust store cannot verify. We do not disable checks.
        "js-challenge",  # a JS interstitial to every non-browser client
        "spa-no-text",  # the HTML carries no policy text; hashing it watches a template
        "false-drift",  # the page is fine; watching it would cry wolf every week, forever
        "unreachable",  # completes no HTTP exchange at all
        "no-such-authority",  # the gap is in the world, not in the crawler
        # The only reason in this list that a MACHINE cannot produce: a named human opened the
        # seeded URL, found it is not the official page for that document class, and no right
        # page exists to swap in. Written by `sentinel verify --reject --gap`.
        "wrong-page",
    }
)

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class Verification:
    """Whether a **human** has confirmed this URL is the page the entry claims it is.

    Distinct from `Source.checked`, which records what a *socket* saw, and the distinction is
    the whole point. A 200 with a plausible `<title>` is evidence that a page exists; it is
    not evidence that it is *the official page for this document class in this jurisdiction*,
    and the gap between those two sentences is a person driving to the wrong office.

    `status` is the machine-readable field that travels with every published copy of the
    source. `label` and `statement` are the words a reader sees — a **word**, never a colour
    or an icon (WCAG 2.2 AA), because the reader most likely to be harmed by a wrong entry is
    the one least likely to see a red dot.
    """

    status: str = UNVERIFIED
    verifier: str = ""
    at: str = ""
    note: str = ""
    evidence: str = ""
    expires_at: str = ""

    @property
    def label(self) -> str:
        """The short status, status-word first. Goes in a table cell and in a feed item."""
        if self.status == VERIFIED:
            return f"VERIFIED — confirmed by {self.verifier} on {self.at}"
        if self.status == REJECTED:
            return f"REJECTED — {self.verifier} found this is not the official page ({self.at})"
        if self.status == WITHDRAWN:
            return "WITHDRAWN — this source is no longer in the registry"
        return "UNVERIFIED — machine-checked, not human-confirmed"

    @property
    def statement(self) -> str:
        """The full sentence. Said in-band, in every artifact, because a caveat that lives
        only in a README nobody re-reads is decoration."""
        if self.status == VERIFIED:
            return (
                f"VERIFIED — {self.verifier} opened this URL on {self.at} and confirmed it is "
                f"the official page for this document class in this jurisdiction. That is the "
                f"only claim it makes: it says nothing about what the law is."
            )
        if self.status == REJECTED:
            reason = f" Reason given: {self.note}" if self.note else ""
            return (
                f"REJECTED — {self.verifier} opened this URL on {self.at} and found it is NOT "
                f"the official page for this document class in this jurisdiction. It is flagged "
                f"for repair and must not be relied on.{reason}"
            )
        if self.status == WITHDRAWN:
            return (
                "WITHDRAWN — the source this record cites is no longer in the registry, so its "
                "verification status cannot be stated here. See sources.json."
            )
        return (
            "UNVERIFIED — machine-checked, not human-confirmed. A live fetch confirmed this URL "
            "answers and its title was read; NO HUMAN has confirmed that it is the official page "
            "for this document class in this jurisdiction. Do not rely on it as authoritative "
            "guidance."
        )

    def to_dict(self) -> dict[str, str]:
        """The closed v1 feed shape published alongside every source.

        Eligibility evidence and expiry remain registry-internal until a separately versioned
        public contract is introduced.  The published v1 schema rejects unknown properties,
        so emitting those fields here would break existing validating consumers.
        """
        return {
            "status": self.status,
            "verifier": self.verifier,
            "verified_at": self.at,
            "note": self.note,
            "statement": self.statement,
        }


WITHDRAWN_VERIFICATION = Verification(status=WITHDRAWN)


@dataclass(frozen=True, slots=True)
class FetchPolicyDecision:
    """The human-owned robots/terms/fetch decision used by V1 eligibility.

    Absence is represented as ``unreviewed`` rather than an implicit allow.  This makes the
    migration from the alpha registry fail closed without fabricating 152 legal or policy
    decisions.  A later iteration will wire the shared eligibility predicate into the watcher
    and publisher after the registry has been reviewed and migrated.
    """

    outcome: str = FETCH_POLICY_UNREVIEWED
    reviewer: str = ""
    at: str = ""
    expires_at: str = ""
    evidence: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "outcome": self.outcome,
            "reviewer": self.reviewer,
            "at": self.at,
            "expires_at": self.expires_at,
            "evidence": self.evidence,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Source:
    """One official watch target. Frozen: the registry is data, not state."""

    id: str
    jurisdiction: str
    document_class: str
    url: str
    authority: str
    verified: bool
    notes: str
    checked: Mapping[str, Any] = field(default_factory=dict)
    verification: Verification = field(default_factory=Verification)
    active: bool = True
    fetch_policy: FetchPolicyDecision = field(default_factory=FetchPolicyDecision)

    @property
    def host(self) -> str:
        """The host, used for per-host politeness (robots.txt, crawl spacing)."""
        return urlparse(self.url).netloc

    @property
    def verification_status(self) -> str:
        """`unverified` | `verified` | `rejected` — the field an integrator reads."""
        return self.verification.status

    @property
    def reachable(self) -> bool:
        """Whether our OWN fetcher could reach it when it was last machine-checked.

        A source that is in the registry and cannot be fetched is *watched in name only* —
        `ssa.gov` has 403'd every client we own since before this tool existed. It is not
        deleted, because deleting it would erase the fact that US Social Security is a
        document class we cannot watch; it is counted, and said out loud, instead.

        Absence of a `checked` block reads as reachable. That default is safe here (and only
        here) because `checked` is written by a live fetch, never by hand: an entry with no
        block has not been machine-checked, and claiming it is *unreachable* would be
        inventing a failure we never observed — the mirror of inventing a hash we never saw.
        """
        return bool(self.checked.get("reachable", True))


@dataclass(frozen=True, slots=True)
class Gap:
    """A (jurisdiction, document class) pair we do **not** watch, and why.

    A gap is a *commitment*: it says "the feed's silence about Vermont driver's licences
    means nothing at all, and here is the host that refused us." It is data rather than
    prose because prose does not get checked, and an unnamed hole is indistinguishable from
    coverage — which is the one thing this registry must never claim by accident.
    """

    jurisdiction: str
    document_class: str
    reason: str
    hosts: tuple[str, ...]
    checked: str
    detail: str


@dataclass(frozen=True, slots=True)
class Registry:
    """The loaded, validated registry."""

    version: str
    sources: tuple[Source, ...]
    gaps: tuple[Gap, ...] = ()

    def __iter__(self) -> Any:
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)

    def for_jurisdiction(self, jurisdiction: str) -> tuple[Source, ...]:
        """Filter by jurisdiction. An unknown jurisdiction is an error, not an empty list:
        `--jurisdiction XX` silently watching nothing is precisely the failure mode this
        tool exists to prevent."""
        key = jurisdiction.upper()
        if key not in JURISDICTIONS:
            raise RegistryError(f"unknown jurisdiction: {jurisdiction!r}")
        return tuple(s for s in self.sources if s.jurisdiction == key)

    def by_id(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise RegistryError(f"unknown source id: {source_id!r}")

    @property
    def unverified(self) -> tuple[Source, ...]:
        """Entries no human has looked at yet. Surfaced by `sentinel sources validate` as a
        standing count, so "seeded but unverified" can never quietly become "trusted" — and
        rendered next to the source in every published artifact, so a *reader* cannot make
        that slide either."""
        return tuple(s for s in self.sources if s.verification_status == UNVERIFIED)

    @property
    def verified_sources(self) -> tuple[Source, ...]:
        """Entries a named human opened and confirmed. The burn-down's numerator."""
        return tuple(s for s in self.sources if s.verification_status == VERIFIED)

    @property
    def rejected(self) -> tuple[Source, ...]:
        """Entries a named human opened and found to be the *wrong page*. They stay in the
        registry, flagged, rather than vanishing: an entry that was wrong and got quietly
        deleted takes the finding with it."""
        return tuple(s for s in self.sources if s.verification_status == REJECTED)

    def verification_of(self, source_id: str) -> Verification:
        """The verification status of a source id — or `WITHDRAWN` if it has left the
        registry. Never `None`, and never absent: a published record that cites a source must
        be able to say something true about that source's status, and "we don't know" is a
        thing we say out loud rather than by omission."""
        for source in self.sources:
            if source.id == source_id:
                return source.verification
        return WITHDRAWN_VERIFICATION

    @property
    def unreachable(self) -> tuple[Source, ...]:
        """Registered sources our own fetcher could not reach — *watched in name only*."""
        return tuple(s for s in self.sources if not s.reachable)

    @property
    def jurisdictions(self) -> frozenset[str]:
        """The jurisdictions we actually watch at least one source in."""
        return frozenset(s.jurisdiction for s in self.sources)


def default_registry_path() -> Path:
    """`sources/registry.json` relative to the repo root (this file's great-grandparent)."""
    return Path(__file__).resolve().parents[3] / "sources" / "registry.json"


def load_registry(path: Path | None = None) -> Registry:
    """Load and validate the committed registry. Any violation raises; there is no
    "skip the bad entry and carry on" path, because a skipped entry is an unwatched
    source, and an unwatched source is the exact silent failure this tool must not have.
    """
    registry_path = path or default_registry_path()
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"registry not found: {registry_path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"registry is not valid JSON: {registry_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RegistryError("registry must be a JSON object")

    version = raw.get("registry_version")
    if version != REGISTRY_VERSION:
        raise RegistryError(
            f"registry_version {version!r} is not the supported {REGISTRY_VERSION!r}"
        )

    entries = raw.get("sources")
    if not isinstance(entries, list) or not entries:
        raise RegistryError("registry.sources must be a non-empty list")

    sources = tuple(_parse_source(entry, index) for index, entry in enumerate(entries))
    _reject_duplicates(sources)

    raw_gaps = raw.get("gaps", [])
    if not isinstance(raw_gaps, list):
        raise RegistryError("registry.gaps must be a list")
    gaps = tuple(_parse_gap(entry, index) for index, entry in enumerate(raw_gaps))
    return Registry(version=version, sources=sources, gaps=gaps)


def _parse_source(entry: object, index: int) -> Source:
    where = f"sources[{index}]"
    if not isinstance(entry, dict):
        raise RegistryError(f"{where} must be an object")

    missing = {"id", "jurisdiction", "document_class", "url", "authority", "notes"} - set(entry)
    if missing:
        raise RegistryError(f"{where} is missing required field(s): {', '.join(sorted(missing))}")

    source_id = _require_str(entry, "id", where)
    if not _ID_RE.match(source_id):
        raise RegistryError(f"{where}.id {source_id!r} must be a lowercase-hyphen slug")

    jurisdiction = _require_str(entry, "jurisdiction", where)
    if jurisdiction not in JURISDICTIONS:
        raise RegistryError(f"{where}.jurisdiction {jurisdiction!r} is not a known jurisdiction")

    document_class = _require_str(entry, "document_class", where)
    if document_class not in DOCUMENT_CLASSES:
        raise RegistryError(
            f"{where}.document_class {document_class!r} is not one of: "
            f"{', '.join(sorted(DOCUMENT_CLASSES))}"
        )

    url = _require_str(entry, "url", where)
    _validate_official_url(url, where)

    authority = _require_str(entry, "authority", where)
    if not authority.strip():
        raise RegistryError(f"{where}.authority must name the issuing authority")

    # `verified` is optional in the file and defaults to false. Absence must never be
    # read as "verified": the default has to fail safe.
    verified = entry.get("verified", False)
    if not isinstance(verified, bool):
        raise RegistryError(f"{where}.verified must be a boolean")

    checked = entry.get("checked", {})
    if not isinstance(checked, dict):
        raise RegistryError(f"{where}.checked must be an object of MACHINE facts")

    active = entry.get("active", True)
    if not isinstance(active, bool):
        raise RegistryError(f"{where}.active must be a boolean")

    return Source(
        id=source_id,
        jurisdiction=jurisdiction,
        document_class=document_class,
        url=url,
        authority=authority,
        verified=verified,
        notes=_require_str(entry, "notes", where),
        checked=checked,
        verification=_parse_verification(entry.get("verification"), verified, where),
        active=active,
        fetch_policy=_parse_fetch_policy(entry.get("fetch_policy"), where),
    )


def _parse_verification(raw: object, verified: bool, where: str) -> Verification:
    """Parse the human-verification block — and refuse an unsigned verification.

    This is the load-bearing validation in this module, and it exists because of a specific,
    easy, tempting failure: someone (a maintainer in a hurry, a script, an AI agent asked to
    "make the registry look finished") sets `"verified": true` on 152 entries, and the
    project's central honesty claim evaporates without a single test going red.

    So `verified: true` is *unloadable* unless a named human and a date are attached to it.
    There is no path — not a hand edit, not a bulk `sed` — that produces a verified entry
    nobody signed. `sentinel verify` writes this block, and it refuses to write one without a
    name (see `core/verify.py`).
    """
    if raw is None or raw == {}:
        if verified:
            raise RegistryError(
                f"{where}.verified is true with no `verification` block. A verification with no "
                f"named human and no date is not a verification — it is an assertion nobody is "
                f"standing behind. Run `sentinel verify` (it records the name and the date), or "
                f"set verified: false."
            )
        return Verification()

    if not isinstance(raw, dict):
        raise RegistryError(f"{where}.verification must be an object")

    status = raw.get("status")
    if status not in VERIFICATION_STATUSES:
        raise RegistryError(
            f"{where}.verification.status {status!r} is not one of: "
            f"{', '.join(sorted(VERIFICATION_STATUSES))}"
        )
    if (status == VERIFIED) is not verified:
        raise RegistryError(
            f"{where}: `verified: {str(verified).lower()}` disagrees with "
            f"`verification.status: {status!r}`. The boolean and the block are two views of one "
            f"fact and they may never disagree — one of them is what a consumer will read."
        )

    values = _string_fields(
        raw,
        ("verifier", "at", "note", "evidence", "expires_at"),
        f"{where}.verification",
    )
    verifier = values["verifier"]
    at = values["at"]
    expires_at = values["expires_at"]
    _validate_human_verification(status, verifier, at, where)
    _validate_date_range(at, expires_at, f"{where}.verification")

    return Verification(
        status=status,
        verifier=verifier.strip(),
        at=at,
        note=values["note"],
        evidence=values["evidence"].strip(),
        expires_at=expires_at,
    )


def _parse_fetch_policy(raw: object, where: str) -> FetchPolicyDecision:
    """Parse an explicit policy decision; missing data is never treated as permission."""
    if raw is None or raw == {}:
        return FetchPolicyDecision()
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}.fetch_policy must be an object")

    outcome = raw.get("outcome")
    if outcome not in FETCH_POLICY_OUTCOMES:
        raise RegistryError(
            f"{where}.fetch_policy.outcome {outcome!r} is not one of: "
            f"{', '.join(sorted(FETCH_POLICY_OUTCOMES))}"
        )
    values = _string_fields(
        raw,
        ("reviewer", "at", "expires_at", "evidence", "reason"),
        f"{where}.fetch_policy",
    )

    if outcome in {FETCH_POLICY_ALLOW, FETCH_POLICY_DENY}:
        _validate_policy_decision(outcome, values, where)

    return FetchPolicyDecision(outcome=outcome, **values)


def _string_fields(raw: Mapping[str, Any], keys: tuple[str, ...], where: str) -> dict[str, str]:
    values = {key: raw.get(key, "") for key in keys}
    if not all(isinstance(value, str) for value in values.values()):
        raise RegistryError(f"{where} fields must be strings")
    return cast(dict[str, str], values)


def _validate_human_verification(status: object, verifier: str, at: str, where: str) -> None:
    if status not in {VERIFIED, REJECTED}:
        return
    if not verifier.strip():
        raise RegistryError(
            f"{where}.verification.status is {status!r} but no `verifier` is named. A human "
            f"judgment with no human attached is indistinguishable from a machine's."
        )
    if not at:
        raise RegistryError(
            f"{where}.verification.at must be an ISO date (YYYY-MM-DD) saying WHEN the "
            f"human looked — a verification with no date cannot go stale, which means it "
            f"can never be re-checked. Got {at!r}."
        )


def _validate_date_range(at: str, expires_at: str, where: str) -> None:
    at_date = _parse_registry_date(at, f"{where}.at") if at else None
    expiry_date = _parse_registry_date(expires_at, f"{where}.expires_at") if expires_at else None
    if at_date and expiry_date and expiry_date < at_date:
        raise RegistryError(f"{where}.expires_at cannot be before at")


def _validate_policy_decision(outcome: object, values: Mapping[str, str], where: str) -> None:
    for key in ("reviewer", "at", "expires_at", "evidence", "reason"):
        if not values[key].strip():
            raise RegistryError(f"{where}.fetch_policy.{key} is required for outcome {outcome!r}")
    _validate_date_range(values["at"], values["expires_at"], f"{where}.fetch_policy")


def _parse_registry_date(raw: str, where: str) -> date:
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise RegistryError(f"{where} must be an ISO date (YYYY-MM-DD); got {raw!r}") from exc
    if parsed.isoformat() != raw:
        raise RegistryError(f"{where} must be an ISO date (YYYY-MM-DD); got {raw!r}")
    return parsed


def _parse_gap(entry: object, index: int) -> Gap:
    """Parse one named hole. A gap with an unknown `reason` cannot load.

    The closed vocabulary is the point. "We don't have that one" is not a reason, and a
    free-text reason field would let a gap decay into a shrug — at which point the gap list
    stops being a record of *honest refusals* (robots, TLS, no UA spoofing) and becomes a
    record of things we forgot.
    """
    where = f"gaps[{index}]"
    if not isinstance(entry, dict):
        raise RegistryError(f"{where} must be an object")

    missing = {"jurisdiction", "document_class", "reason", "hosts", "checked", "detail"} - set(
        entry
    )
    if missing:
        raise RegistryError(f"{where} is missing required field(s): {', '.join(sorted(missing))}")

    jurisdiction = _require_str(entry, "jurisdiction", where)
    if jurisdiction not in JURISDICTIONS:
        raise RegistryError(f"{where}.jurisdiction {jurisdiction!r} is not a known jurisdiction")

    document_class = _require_str(entry, "document_class", where)
    if document_class not in DOCUMENT_CLASSES:
        raise RegistryError(f"{where}.document_class {document_class!r} is not a known class")

    reason = _require_str(entry, "reason", where)
    if reason not in GAP_REASONS:
        raise RegistryError(
            f"{where}.reason {reason!r} is not one of: {', '.join(sorted(GAP_REASONS))}"
        )

    hosts = entry.get("hosts")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) for h in hosts):
        raise RegistryError(
            f"{where}.hosts must be a non-empty list of the host(s) that refused us — a gap "
            f"with no host named is a shrug, not a finding"
        )

    detail = _require_str(entry, "detail", where)
    if not detail.strip():
        raise RegistryError(f"{where}.detail must say what was tried and what happened")

    return Gap(
        jurisdiction=jurisdiction,
        document_class=document_class,
        reason=reason,
        hosts=tuple(str(h) for h in hosts),
        checked=_require_str(entry, "checked", where),
        detail=detail,
    )


def _validate_official_url(url: str, where: str) -> None:
    """https, a real host, no credentials, no fragment. A watch target with a fragment is
    a lie: the fetch returns the whole document regardless, so the diff would not be
    scoped to the anchor a maintainer thought they were watching."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RegistryError(f"{where}.url must be https (got {parsed.scheme or 'no scheme'!r})")
    if not parsed.netloc:
        raise RegistryError(f"{where}.url has no host: {url!r}")
    if parsed.username or parsed.password:
        raise RegistryError(f"{where}.url must not embed credentials")
    if parsed.fragment:
        raise RegistryError(
            f"{where}.url must not carry a #fragment — the fetch cannot honour it, so the "
            f"watch would not be scoped the way the entry claims"
        )


def _require_str(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise RegistryError(f"{where}.{key} must be a string")
    return value


# `"checked": { ... }` and `"verification": { ... }` are written on ONE line in the committed
# file — they are leaf blocks of scalars, and exploding them over five lines each turns a
# 152-entry registry into a file nobody scrolls through. `json.dumps(indent=2)` cannot do
# this, so the writer re-collapses them, byte-for-byte in the committed style.
#
# Why this is worth thirty lines: `sentinel verify` REWRITES this file, once per source, up
# to 152 times. A writer that reformats the whole file on first use would bury the one line
# that changed — the human's name — under 800 lines of reflowed whitespace, and a diff nobody
# can read is a diff nobody reviews. The verifier's own audit trail has to stay legible.
_INLINE_BLOCK_RE = re.compile(r'"(checked|verification|fetch_policy)": \{\n[^{}]*?\n\s*\}')


def _inline_block(match: re.Match[str]) -> str:
    key = match.group(1)
    obj = json.loads("{" + match.group(0) + "}")[key]
    body = ", ".join(
        f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)}"
        for k, value in obj.items()
    )
    return f'"{key}": {{ {body} }}'


def dump_registry_text(raw: Mapping[str, Any]) -> str:
    """Serialize a raw registry mapping in the committed file's exact formatting."""
    text = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    return _INLINE_BLOCK_RE.sub(_inline_block, text)


def _reject_duplicates(sources: tuple[Source, ...]) -> None:
    """Two rules. Ids must be unique (they key the snapshot store — a collision would
    overwrite one source's history with another's). And a (jurisdiction, document_class,
    url) triple must be unique: the same page watched twice under two ids doubles every
    change record a reviewer sees, which is how a reviewer learns to ignore the feed."""
    seen_ids: set[str] = set()
    seen_triples: set[tuple[str, str, str]] = set()
    for source in sources:
        if source.id in seen_ids:
            raise RegistryError(f"duplicate source id: {source.id!r}")
        seen_ids.add(source.id)

        triple = (source.jurisdiction, source.document_class, source.url)
        if triple in seen_triples:
            raise RegistryError(
                f"duplicate watch target: {source.jurisdiction}/{source.document_class} "
                f"already watches {source.url}"
            )
        seen_triples.add(triple)
