"""`sentinel crosswalk` — which of an outside list of URLs this registry already covers.

Other projects watch the same class of government pages this registry does, and maintain
their own baselines of the URLs they cite. Those lists and this registry are built
independently, from opposite directions, and neither can say which of the other's URLs it
already covers, which host-level refusals they share, or where one has found a fetchable
official surface the other is missing.

Given a plain list of URLs, this reports for each one exactly which of four things it is.
**No network, no clock, no registry mutation.** The answer is a function of the committed
registry and the input list, which is what makes it byte-identical on repeat.

## The four kinds, and why `host_only` is not a near-miss

:data:`MATCH_KINDS` is closed, and the distinction that carries the weight is between
:data:`MATCH_SOURCE` and :data:`MATCH_HOST_ONLY`. A URL that matches a registered source
exactly is a page this registry watches. A URL on the same host as a registered source is
**not**: it is a different page on a host we have found fetchable, and the feed's silence
about it means nothing at all. Collapsing the two would be the most damaging thing this
module could do, because it is the reading a consumer wants to be true.

:data:`MATCH_GAP` is the kind that exists only because this registry records its holes as
data. A URL whose host appears in a named gap is one we have looked at and decided not to
watch, with a dated reason — which is a genuinely different fact from
:data:`MATCH_UNMATCHED`, a URL nobody here has considered at all. Reporting the two as one
would throw away the entire point of the gap register.

Precedence is source, then gap, then same-host source, then unmatched. A gap outranks a
same-host source because a gap is a recorded decision about that host and a same-host source
is an accident of hosting: when both are true, the reviewed statement is the more
informative one, and it names the reason.

## URL identity

The normalizer is :func:`id_churn_sentinel.core.staleness.normalize_url`, deliberately, and
not a second one written here. It lowercases the scheme and host and drops a default port —
identities the RFC already grants — and does **not** strip trailing slashes or query
parameters, because `/name-change` and `/name-change/` are the same page on most servers and
different pages on some. A citation differing only by a slash is reported as `host_only`,
which is true, rather than as `matched`, which would be a guess. Two normalizers that
disagreed would let `sentinel stale` and `sentinel crosswalk` give a consumer two different
answers about the same URL.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..errors import SentinelError
from .registry import JURISDICTIONS, Gap, Registry, Source
from .staleness import normalize_url

__all__ = [
    "CROSSWALK_SCHEMA_VERSION",
    "MATCH_GAP",
    "MATCH_HOST_ONLY",
    "MATCH_KINDS",
    "MATCH_SOURCE",
    "MATCH_UNMATCHED",
    "CrosswalkError",
    "CrosswalkReport",
    "CrosswalkRow",
    "crosswalk_document",
    "crosswalk_report",
    "dumps_crosswalk",
    "load_urls",
    "render_text",
]

CROSSWALK_SCHEMA_VERSION = "1.0"

MATCH_SOURCE = "source"
MATCH_GAP = "gap"
MATCH_HOST_ONLY = "host_only"
MATCH_UNMATCHED = "unmatched"

#: Closed. A consumer may branch exhaustively on a row's `match` and know it has not
#: silently been handed a fifth thing.
MATCH_KINDS: tuple[str, ...] = (MATCH_SOURCE, MATCH_GAP, MATCH_HOST_ONLY, MATCH_UNMATCHED)


class CrosswalkError(SentinelError):
    """The URL list is malformed, or names something this tool refuses to read."""


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# ---- the input ------------------------------------------------------------------------------

#: A baseline manifest may be large, but it is a local file the consumer already has.
#: The cap exists so a mistyped path at a multi-gigabyte file is an error rather than a hang.
_MAX_URL_LIST_BYTES = 8 * 1024 * 1024


def load_urls(path: Path) -> tuple[str, ...]:
    """Read a URL list, in whichever of the three shapes the consumer already has.

    Accepted, in order of what is tried:

    * a JSON object keyed by URL — the shape a per-URL baseline manifest usually takes,
      whose values are records this module does not read;
    * a JSON array of strings, or of objects carrying a `url` key;
    * one URL per line, `#` comments and blank lines ignored.

    Order is preserved and duplicates are collapsed, first occurrence winning, so a list
    that names the same page twice yields one row rather than two. The report is sorted
    anyway; preserving order here only makes the de-duplication predictable.

    Every entry must be an absolute http(s) URL. A bare hostname or a relative path is
    refused by name rather than silently becoming an `unmatched` row, because an input
    this tool could not parse and an input nobody watches are different answers and only
    one of them is about the registry.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise CrosswalkError(f"URL list not found: {path}") from exc
    except OSError as exc:
        raise CrosswalkError(f"URL list cannot be read: {path} ({exc})") from exc
    if len(raw) > _MAX_URL_LIST_BYTES:
        raise CrosswalkError(
            f"URL list is larger than {_MAX_URL_LIST_BYTES} bytes: {path}. "
            "This command reads a list of URLs, not a corpus."
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CrosswalkError(f"URL list is not valid UTF-8: {path}") from exc

    candidates = _parse_url_list(text, path)
    seen: dict[str, None] = {}
    for index, candidate in enumerate(candidates):
        _require_absolute_url(candidate, path, index)
        seen.setdefault(candidate, None)
    if not seen:
        raise CrosswalkError(
            f"URL list is empty: {path}. A crosswalk over no URLs would report a clean "
            "sheet, which reads exactly like a list nothing matched."
        )
    return tuple(seen)


def _parse_url_list(text: str, path: Path) -> list[str]:
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            document = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise CrosswalkError(f"URL list is not valid JSON: {path} ({exc})") from exc
        return _urls_from_json(document, path)
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _urls_from_json(document: Any, path: Path) -> list[str]:
    """Only reached for text beginning `{` or `[`, so the document is an object or an array.

    There is deliberately no third branch raising "expected an object or an array". It would
    be unreachable, and an unreachable refusal is indistinguishable from a refusal that does
    not work -- it reads as a guard in review and can never fire. A JSON scalar reaches the
    line-based path instead and is refused there, by name, as an unparseable entry.
    """
    if isinstance(document, Mapping):
        # The baseline-manifest shape: the keys are the URLs.
        return [str(key) for key in document]
    urls: list[str] = []
    for index, item in enumerate(document):
        if isinstance(item, str):
            urls.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("url"), str):
            urls.append(str(item["url"]))
        else:
            raise CrosswalkError(
                f"{path}: entry {index} is neither a URL string nor an object with a "
                f"`url` string; got {type(item).__name__}"
            )
    return urls


def _require_absolute_url(candidate: str, path: Path, index: int) -> None:
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise CrosswalkError(
            f"{path}: entry {index} is not an absolute http(s) URL: {candidate!r}. "
            "A URL this command cannot parse is refused rather than reported as unmatched, "
            "because `unmatched` is a statement about the registry."
        )


# ---- the report -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrosswalkRow:
    """One input URL, and exactly which of the four things it is."""

    url: str
    normalized_url: str
    host: str
    match: str
    source_id: str | None = None
    jurisdiction: str | None = None
    document_class: str | None = None
    verification_status: str | None = None
    gap_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Omit the fields this row has no value for, rather than emitting nulls.

        A `null` `source_id` on an `unmatched` row invites a reader to treat the field as
        present-but-empty. The `match` kind already says which fields a row carries.
        """
        payload: dict[str, Any] = {
            "url": self.url,
            "normalized_url": self.normalized_url,
            "host": self.host,
            "match": self.match,
        }
        for key, value in (
            ("source_id", self.source_id),
            ("jurisdiction", self.jurisdiction),
            ("document_class", self.document_class),
            ("verification_status", self.verification_status),
            ("gap_reason", self.gap_reason),
        ):
            if value is not None:
                payload[key] = value
        return payload


@dataclass(frozen=True, slots=True)
class CrosswalkReport:
    """Every input URL classified, with the denominators stated."""

    rows: tuple[CrosswalkRow, ...]
    jurisdiction: str | None = None

    @property
    def summary(self) -> dict[str, int]:
        """Counts per match kind, with **every** kind present even at zero.

        A summary that omitted the kinds that did not occur would make "no unmatched URLs"
        and "this build of the tool has no notion of unmatched" look the same to a reader
        diffing two reports.
        """
        counts = dict.fromkeys(MATCH_KINDS, 0)
        for row in self.rows:
            counts[row.match] += 1
        counts["total"] = len(self.rows)
        return counts


def crosswalk_report(
    registry: Registry,
    urls: Iterable[str],
    *,
    jurisdiction: str | None = None,
) -> CrosswalkReport:
    """Classify each URL against the committed registry.

    `jurisdiction` narrows the registry side, not the input side: with it, a URL is matched
    only against that jurisdiction's sources and gaps, and a URL this registry watches under
    a *different* jurisdiction reports `unmatched`. That is the honest reading of a filtered
    question, and it is why the filter is echoed into the document.
    """
    key = _checked_jurisdiction(jurisdiction)
    sources = registry.sources if key is None else registry.for_jurisdiction(key)
    gaps = (
        registry.gaps if key is None else tuple(g for g in registry.gaps if g.jurisdiction == key)
    )

    by_url: dict[str, Source] = {normalize_url(entry.url): entry for entry in sources}
    # `setdefault`, so a host serving several registered sources reports the first in
    # registry order rather than an arbitrary one -- a `host_only` row has to name SOME
    # neighbour, and which one it names must not depend on dict insertion luck.
    by_host: dict[str, Source] = {}
    for entry in sources:
        by_host.setdefault(_host(entry.url), entry)
    gap_by_host: dict[str, Gap] = {}
    for entry_gap in gaps:
        for gap_host in entry_gap.hosts:
            gap_by_host.setdefault(gap_host.lower(), entry_gap)

    rows: list[CrosswalkRow] = []
    for url in urls:
        normalized = normalize_url(url)
        host = _host(url)
        source = by_url.get(normalized)
        if source is not None:
            rows.append(
                CrosswalkRow(
                    url=url,
                    normalized_url=normalized,
                    host=host,
                    match=MATCH_SOURCE,
                    source_id=source.id,
                    jurisdiction=source.jurisdiction,
                    document_class=source.document_class,
                    verification_status=source.verification_status,
                )
            )
            continue
        gap = gap_by_host.get(host)
        if gap is not None:
            rows.append(
                CrosswalkRow(
                    url=url,
                    normalized_url=normalized,
                    host=host,
                    match=MATCH_GAP,
                    jurisdiction=gap.jurisdiction,
                    document_class=gap.document_class,
                    gap_reason=gap.reason,
                )
            )
            continue
        neighbour = by_host.get(host)
        if neighbour is not None:
            rows.append(
                CrosswalkRow(
                    url=url,
                    normalized_url=normalized,
                    host=host,
                    match=MATCH_HOST_ONLY,
                    source_id=neighbour.id,
                    jurisdiction=neighbour.jurisdiction,
                    document_class=neighbour.document_class,
                    verification_status=neighbour.verification_status,
                )
            )
            continue
        rows.append(
            CrosswalkRow(url=url, normalized_url=normalized, host=host, match=MATCH_UNMATCHED)
        )

    # Sorted by normalized URL so the document does not depend on the order the consumer
    # happened to list their citations in. `url` is the tiebreak, so two spellings of one
    # normalized URL still order deterministically.
    rows.sort(key=lambda row: (row.normalized_url, row.url))
    return CrosswalkReport(rows=tuple(rows), jurisdiction=key)


def _checked_jurisdiction(jurisdiction: str | None) -> str | None:
    """An unknown jurisdiction is an error, never an empty registry side.

    `--jurisdiction XY` silently matching nothing would report every input URL as
    `unmatched` — a clean-looking answer produced by a typo.
    """
    if jurisdiction is None:
        return None
    key = jurisdiction.upper()
    if key not in JURISDICTIONS:
        raise CrosswalkError(f"unknown jurisdiction: {jurisdiction!r}")
    return key


# ---- output ---------------------------------------------------------------------------------


def crosswalk_document(report: CrosswalkReport) -> dict[str, Any]:
    """The published shape, validated by `docs/schema/crosswalk-v1.schema.json`.

    Carries no `generated_at`. This report is a pure function of the registry revision and
    the input list, and a timestamp would make two identical answers differ — which is the
    property the byte-stability test exists to hold.
    """
    return {
        "schema_version": CROSSWALK_SCHEMA_VERSION,
        "jurisdiction": report.jurisdiction,
        "summary": report.summary,
        "rows": [row.to_dict() for row in report.rows],
    }


def dumps_crosswalk(document: Mapping[str, Any]) -> str:
    """Serialize with a trailing newline, exactly as every other artifact here is written."""
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


def render_text(report: CrosswalkReport) -> str:
    """The terminal view. Every kind is named even at zero, for the summary's reason."""
    counts = report.summary
    scope = report.jurisdiction or "all jurisdictions"
    lines = [
        f"crosswalk against the committed registry ({scope}): {counts['total']} URL(s)",
        f"  {counts[MATCH_SOURCE]} watched as a registered source",
        f"  {counts[MATCH_GAP]} on a host covered by a named gap",
        f"  {counts[MATCH_HOST_ONLY]} on a host we watch, but not this page",
        f"  {counts[MATCH_UNMATCHED]} not considered by this registry at all",
        "",
        "`host_only` is NOT coverage: it is a different page on a host we have found "
        "fetchable, and",
        "this registry's silence about it means nothing at all.",
    ]
    return "\n".join(lines) + "\n"
