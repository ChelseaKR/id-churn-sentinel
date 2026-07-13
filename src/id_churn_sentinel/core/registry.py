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
  official page it claims to be" — nothing in this codebase sets it. This mirrors the
  VERIFIERS discipline in trans-docs-navigator, where a record with a placeholder verifier
  is treated as unverified content rather than quietly served as fact.

Entries with `verified: false` are still watched. An unverified URL that changes is still
worth a human's attention; what it is *not* is evidence about the law.

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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from id_churn_sentinel.errors import RegistryError

__all__ = [
    "CORE_STATE_DOCUMENT_CLASSES",
    "DOCUMENT_CLASSES",
    "GAP_REASONS",
    "JURISDICTIONS",
    "REGISTRY_VERSION",
    "Gap",
    "Registry",
    "Source",
    "default_registry_path",
    "load_registry",
]

REGISTRY_VERSION = "1.0"

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
    }
)

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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

    @property
    def host(self) -> str:
        """The host, used for per-host politeness (robots.txt, crawl spacing)."""
        return urlparse(self.url).netloc

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
        """Entries no human has confirmed yet. Surfaced by `sentinel sources validate`
        as a standing count, so "seeded but unverified" can never quietly become "trusted"."""
        return tuple(s for s in self.sources if not s.verified)

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

    return Source(
        id=source_id,
        jurisdiction=jurisdiction,
        document_class=document_class,
        url=url,
        authority=authority,
        verified=verified,
        notes=_require_str(entry, "notes", where),
        checked=checked,
    )


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
