"""`sentinel stale` — which of a consumer's own pages cite a source that has since changed.

The feed tells an organization that a government page changed. It does not tell them which of
*their* pages depend on it. A legal-aid clinic or a guidance site maintains a set of pages,
each citing government sources and each carrying a last-reviewed date, and turning "Texas DPS
changed on 2026-08-30" into "your Texas driver's-licence page, last reviewed 2026-06-01, cites
that source" is work every consumer would otherwise script by hand, once each, differently.

**No account, no subscriber list, no network.** The manifest stays on the consumer's machine;
this command reads a public artifact they already have a copy of. That is the same constraint
that rules out email notification here, honoured rather than worked around.

Three properties this module holds, each a test.

**A page whose citations are not in the registry is `unwatched`, never current.** The worst
possible output would be a clean report for a page citing a URL nobody watches: the consumer
would read our silence about their page as evidence, and it is the absence of evidence. Every
row says which of the three things it is.

**Staleness is measured against when the source changed, not when a human confirmed it.** A
change observed on 2026-05-01 and reviewed on 2026-07-01, against a page reviewed on
2026-06-01, is NOT stale — the consumer reviewed their page after the source moved. Comparing
against the review date instead would report a page that is fine as one that is not. Both dates
travel on every row so a reader can see which was used.

**An unreviewed, dismissed or withdrawn change can never reach the output.** `changes.json`
should never carry one, but `--changes` accepts any conforming file, so the publisher's
`publishable` predicate is re-asserted here on the wire format rather than assumed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..errors import SentinelError
from .registry import Registry, Source

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "MATCH_HOST_ONLY",
    "MATCH_KINDS",
    "MATCH_MATCHED",
    "MATCH_UNWATCHED",
    "ConsumerManifest",
    "ConsumerPage",
    "ManifestError",
    "PageReport",
    "StaleRow",
    "load_changes_document",
    "load_manifest",
    "normalize_url",
    "render_text",
    "staleness_report",
]

MANIFEST_SCHEMA_VERSION = "1.0"

MATCH_MATCHED = "matched"
MATCH_HOST_ONLY = "host_only"
MATCH_UNWATCHED = "unwatched"

#: Closed. A consumer may branch exhaustively on a citation's `match`.
MATCH_KINDS: frozenset[str] = frozenset({MATCH_MATCHED, MATCH_HOST_ONLY, MATCH_UNWATCHED})

_DEFAULT_PORTS = {"http": "80", "https": "443"}


class ManifestError(SentinelError):
    """A consumer manifest is malformed, or names something this tool refuses to read."""


# ---- URL identity ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """The identity two URLs are compared on. Deliberately conservative.

    Lowercases the scheme and host, drops a default port, and drops an empty fragment — every
    one of which is an identity the RFC already grants. It does **not** strip trailing slashes
    or query parameters, tempting as that is for tidiness: `/name-change` and `/name-change/`
    are the same page on most servers and different pages on some, and a normalizer that
    guesses would silently report a consumer's citation as watched when it is not. A citation
    that differs only by a slash is reported as `host_only`, which is true, rather than as
    `matched`, which would be a guess.
    """
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    port = parts.port
    if port is not None and _DEFAULT_PORTS.get(parts.scheme.lower()) != str(port):
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path, parts.query, ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# ---- the manifest ---------------------------------------------------------------------------

_PAGE_KEYS = frozenset({"id", "title", "url", "last_reviewed", "cites"})
_REQUIRED_PAGE_KEYS = ("id", "last_reviewed", "cites")
_MANIFEST_KEYS = frozenset({"schema_version", "site", "pages"})


@dataclass(frozen=True, slots=True)
class ConsumerPage:
    """One page a consumer maintains, and the government sources it cites."""

    id: str
    last_reviewed: date
    cites: tuple[str, ...]
    title: str = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class ConsumerManifest:
    schema_version: str
    site: str
    pages: tuple[ConsumerPage, ...]


def load_manifest(path: Path) -> ConsumerManifest:
    """Read and validate a consumer manifest. Any violation raises; nothing is skipped.

    A skipped page is a page reported on by omission, which is the failure this whole command
    exists to prevent one level up.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")

    unknown = set(raw) - _MANIFEST_KEYS
    if unknown:
        raise ManifestError(f"manifest has unknown field(s) {sorted(unknown)}")
    version = raw.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"manifest schema_version {version!r} is not the supported {MANIFEST_SCHEMA_VERSION!r}"
        )
    entries = raw.get("pages")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("manifest.pages must be a non-empty list")

    pages = tuple(_parse_page(entry, index) for index, entry in enumerate(entries))
    seen: set[str] = set()
    for page in pages:
        if page.id in seen:
            raise ManifestError(f"manifest names page id {page.id!r} twice")
        seen.add(page.id)
    return ConsumerManifest(schema_version=str(version), site=str(raw.get("site", "")), pages=pages)


def _parse_page(entry: object, index: int) -> ConsumerPage:
    where = f"pages[{index}]"
    if not isinstance(entry, dict):
        raise ManifestError(f"{where} must be an object")
    unknown = set(entry) - _PAGE_KEYS
    if unknown:
        raise ManifestError(f"{where} has unknown field(s) {sorted(unknown)}")
    missing = [key for key in _REQUIRED_PAGE_KEYS if key not in entry]
    if missing:
        raise ManifestError(f"{where} is missing {missing}")

    page_id = str(entry["id"]).strip()
    if not page_id:
        raise ManifestError(f"{where}.id is empty")
    cites = entry["cites"]
    if not isinstance(cites, list) or not cites:
        raise ManifestError(f"{where}.cites must be a non-empty list of https URLs")
    citations: list[str] = []
    for citation in cites:
        if not isinstance(citation, str):
            raise ManifestError(f"{where}.cites contains a non-string entry")
        if urlsplit(citation).scheme != "https":
            # The registry refuses a non-https source for the same reason (core/registry.py):
            # a plaintext fetch of a government page is observable and modifiable by anyone on
            # the path, and the people this feed exists for are the ones least able to afford
            # that.
            raise ManifestError(f"{where}.cites has a non-https URL: {citation!r}")
        citations.append(citation)

    page_url = str(entry.get("url", ""))
    if page_url and urlsplit(page_url).scheme != "https":
        raise ManifestError(f"{where}.url must be https: {page_url!r}")
    return ConsumerPage(
        id=page_id,
        last_reviewed=_parse_date(entry["last_reviewed"], f"{where}.last_reviewed"),
        cites=tuple(citations),
        title=str(entry.get("title", "")),
        url=page_url,
    )


def _parse_date(raw: object, where: str) -> date:
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ManifestError(f"{where} must be a YYYY-MM-DD date, got {raw!r}") from exc


# ---- the published feed ----------------------------------------------------------------------


def load_changes_document(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"changes document not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"changes document is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("changes"), list):
        raise ManifestError(f"{path} is not a changes document (no `changes` array)")
    return raw


def _publishable(change: Mapping[str, Any]) -> bool:
    """`ChangeRecord.publishable`, re-asserted on the wire format.

    `docs/changes.json` can never carry an unpublishable record — `publish()` refuses. But
    `--changes` takes any conforming file, including one a consumer built themselves, and an
    unreviewed hash change surfaced as "your page is out of date" is precisely the claim this
    project refuses to make.
    """
    if change.get("review_status") != "confirmed":
        return False
    if change.get("publication_status") in {"withdrawn", "corrected"}:
        return False
    if change.get("significance") == "substantive":
        return change.get("independent_review_status") == "confirmed"
    return change.get("significance") == "editorial"


# ---- the report --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StaleRow:
    """One confirmed change to a source one of the consumer's pages cites."""

    change_id: str
    source_id: str
    citation: str
    jurisdiction: str
    document_class: str
    significance: str
    observed_at: str
    reviewed_at: str
    reviewer: str
    independent_reviewer: str
    verification_status: str

    def to_dict(self) -> dict[str, str]:
        return {
            "change_id": self.change_id,
            "source_id": self.source_id,
            "citation": self.citation,
            "jurisdiction": self.jurisdiction,
            "document_class": self.document_class,
            "significance": self.significance,
            "observed_at": self.observed_at,
            "reviewed_at": self.reviewed_at,
            "reviewer": self.reviewer,
            "independent_reviewer": self.independent_reviewer,
            "verification_status": self.verification_status,
        }


@dataclass(frozen=True, slots=True)
class CitationStatus:
    url: str
    match: str
    source_id: str
    verification_status: str
    host_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": self.url,
            "match": self.match,
            "source_id": self.source_id,
            "verification_status": self.verification_status,
        }
        if self.host_sources:
            payload["same_host_sources"] = list(self.host_sources)
        return payload


@dataclass(frozen=True, slots=True)
class PageReport:
    page_id: str
    title: str
    last_reviewed: str
    citations: tuple[CitationStatus, ...]
    stale: tuple[StaleRow, ...]

    @property
    def unwatched(self) -> tuple[CitationStatus, ...]:
        return tuple(c for c in self.citations if c.match != MATCH_MATCHED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "last_reviewed": self.last_reviewed,
            "citations": [c.to_dict() for c in self.citations],
            "stale": [row.to_dict() for row in self.stale],
        }


_STATEMENT = (
    "A row says: a source this page cites was observed to change after the page's own "
    "last-reviewed date, and a named human confirmed the change. It does NOT say the page is "
    "wrong, and it says nothing about what the law is. A citation reported as `unwatched` or "
    "`host_only` is not watched by this registry at all — silence about it is not evidence."
)


def staleness_report(
    manifest: ConsumerManifest,
    document: Mapping[str, Any],
    registry: Registry,
) -> dict[str, Any]:
    """Per consumer page: what it cites, whether we watch it, and what has changed since."""
    by_url: dict[str, Source] = {}
    by_host: dict[str, list[str]] = {}
    for registered in registry.sources:
        by_url[normalize_url(registered.url)] = registered
        by_host.setdefault(_host(registered.url), []).append(registered.id)

    published: list[Mapping[str, Any]] = [
        entry for entry in document["changes"] if isinstance(entry, dict) and _publishable(entry)
    ]
    by_source: dict[str, list[Mapping[str, Any]]] = {}
    for entry in published:
        by_source.setdefault(str(entry.get("source_id", "")), []).append(entry)

    pages: list[PageReport] = []
    for page in manifest.pages:
        citations: list[CitationStatus] = []
        rows: list[StaleRow] = []
        for citation in page.cites:
            source = by_url.get(normalize_url(citation))
            if source is None:
                same_host = tuple(sorted(by_host.get(_host(citation), ())))
                citations.append(
                    CitationStatus(
                        url=citation,
                        match=MATCH_HOST_ONLY if same_host else MATCH_UNWATCHED,
                        source_id="",
                        verification_status="",
                        host_sources=same_host,
                    )
                )
                continue
            citations.append(
                CitationStatus(
                    url=citation,
                    match=MATCH_MATCHED,
                    source_id=source.id,
                    verification_status=source.verification_status,
                )
            )
            for change in by_source.get(source.id, ()):
                observed = str(change.get("observed_at", ""))
                if not _after(observed, page.last_reviewed):
                    continue
                rows.append(
                    StaleRow(
                        change_id=str(change.get("id", "")),
                        source_id=source.id,
                        citation=citation,
                        jurisdiction=str(change.get("jurisdiction", "")),
                        document_class=str(change.get("document_class", "")),
                        significance=str(change.get("significance", "")),
                        observed_at=observed,
                        reviewed_at=str(change.get("reviewed_at", "")),
                        reviewer=str(change.get("reviewer", "")),
                        independent_reviewer=str(change.get("independent_reviewer") or ""),
                        verification_status=source.verification_status,
                    )
                )
        pages.append(
            PageReport(
                page_id=page.id,
                title=page.title,
                last_reviewed=page.last_reviewed.isoformat(),
                citations=tuple(citations),
                stale=tuple(sorted(rows, key=lambda r: (r.observed_at, r.change_id))),
            )
        )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "statement": _STATEMENT,
        "site": manifest.site,
        "feed_generated_at": str(document.get("generated_at", "")),
        "compared_on": "observed_at",
        "summary": {
            "pages": len(pages),
            "pages_with_stale_citations": sum(1 for p in pages if p.stale),
            "citations": sum(len(p.citations) for p in pages),
            "citations_not_watched": sum(len(p.unwatched) for p in pages),
            "changes_considered": len(published),
            "changes_in_input": len(document["changes"]),
        },
        "pages": [page.to_dict() for page in pages],
    }


def _after(observed_at: str, reviewed: date) -> bool:
    """Was the source observed to change after this page was last reviewed?

    An unparseable or empty `observed_at` returns True. That is deliberate and is the only
    place this module rounds in a direction: a change whose date we cannot read is a change we
    cannot rule out, and dropping it would remove a real item from a staleness report on the
    strength of a malformed field.
    """
    try:
        return date.fromisoformat(observed_at[:10]) > reviewed
    except ValueError:
        return True


# ---- rendering -----------------------------------------------------------------------------


def render_text(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"consumer pages:            {summary['pages']}",
        f"pages with stale citations: {summary['pages_with_stale_citations']}",
        f"citations:                 {summary['citations']}",
        f"citations NOT watched:     {summary['citations_not_watched']}",
        f"confirmed changes read:    {summary['changes_considered']} "
        f"of {summary['changes_in_input']} in the input",
        "",
        report["statement"],
        "",
    ]
    pages: Sequence[Mapping[str, Any]] = report["pages"]
    for page in pages:
        lines.append(f"{page['page_id']} (last reviewed {page['last_reviewed']})")
        for citation in page["citations"]:
            if citation["match"] == MATCH_MATCHED:
                lines.append(
                    f"    watched  {citation['url']}  [{citation['source_id']}, "
                    f"{citation['verification_status']}]"
                )
            elif citation["match"] == MATCH_HOST_ONLY:
                lines.append(f"    HOST ONLY — this exact page is NOT watched: {citation['url']}")
            else:
                lines.append(f"    UNWATCHED — not in the registry at all: {citation['url']}")
        if not page["stale"]:
            lines.append("    no confirmed change to a watched source since this review date")
        for row in page["stale"]:
            lines.append(
                f"    CHANGED {row['observed_at']}  {row['change_id']}  "
                f"[{row['significance']}]  confirmed by {row['reviewer']} "
                f"on {row['reviewed_at']}"
            )
        lines.append("")
    return "\n".join(lines)
