"""Feed publication — `feed.xml` (RSS 2.0) and `changes.json` (versioned schema).

This is the artifact the incumbents consume. A4TE, Trans Lifeline, Namesake, and legal-aid
orgs do not need another website for trans people to read; they need a machine-readable
answer to *"what changed since I last looked?"* so their own, better-contextualized
guidance can be kept current. See `docs/CONSUMERS.md`.

Two properties are enforced here, and both are merge-blocking tests:

**Only reviewed change records are published.** :func:`publish` takes records, filters to
`publishable` (confirmed, classified, and signed by a named human), and then — belt and
braces — *re-asserts* the predicate on every record it is about to write, raising
:class:`~id_churn_sentinel.errors.PublishError` if one slips through. The filter is the
mechanism; the assertion is the proof. A future refactor that widens the query will not
quietly start publishing unreviewed drift; it will crash.

**The feed requires no account.** RSS and JSON over plain HTTP, no auth, no email capture,
no analytics beacon, no tracking pixel, no cookie. This is not a growth choice, it is a
safety one: a subscriber list for a trans-ID-law feed is a list of trans people and the
orgs that serve them, and the safest way to protect that list is to never create it. See
`docs/RESPONSIBLE-TECH-AUDITS.md` §C.

**And it is consumable one jurisdiction at a time.** A name-change clinic in Texas should
not have to parse fifty-one other states to find out that the DPS page moved, and telling
them to "just filter `changes.json`" is telling them to write code before they can read
their own state. So every jurisdiction gets its own `feed-us-tx.xml` and
`changes-us-tx.json`, published whether or not it has any items yet — a URL you can
subscribe to *today* and that will populate when something moves is worth far more than one
that appears the day of the emergency. `sources.json` publishes the whole inventory (and the
gaps) so an integrator can map their own pages to our `source_id`s without reading this
source code, and `index.html` is the human-readable front door.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord
from id_churn_sentinel.core.coverage import coverage
from id_churn_sentinel.core.registry import Registry
from id_churn_sentinel.core.site import feed_slug, render_site
from id_churn_sentinel.errors import PublishError

__all__ = [
    "FEED_SCHEMA_VERSION",
    "SOURCES_SCHEMA_VERSION",
    "PublishResult",
    "changes_json",
    "feed_xml",
    "publish",
    "sources_json",
]

# Bump on any breaking change to the `changes.json` shape. Consumers pin against it.
#
# The versioning promise, stated once and kept: **a bump means a break.** Adding a new
# OPTIONAL field is not a break and does not bump this — a consumer who ignores unknown keys
# (which every JSON consumer does) is unaffected. Removing a field, renaming one, changing a
# type, or narrowing an enum IS a break, and bumps it. The formal shape is
# `docs/schema/changes-v1.schema.json`, and a test asserts the schema and the code agree —
# a schema document that drifts from its implementation is worse than none, because an
# integrator builds against it.
FEED_SCHEMA_VERSION = "1.0"

# The inventory feed's own version, independent of the change feed's: what we watch and what
# we changed about *how* we publish are different questions on different clocks.
SOURCES_SCHEMA_VERSION = "1.0"

FEED_TITLE = "ID Churn Sentinel — reviewed changes to US transgender ID-document sources"
FEED_DESCRIPTION = (
    "Human-reviewed changes detected at official US state and federal pages governing "
    "name and gender-marker changes on identity documents. Each item cites the official "
    "source URL and the passage that changed. This feed reports that a source changed; "
    "it does not assert what the law is."
)


class PublishResult:
    """Paths written, and the count that reached the feed."""

    __slots__ = (
        "changes_path",
        "feed_path",
        "jurisdiction_feeds",
        "published",
        "site_path",
        "sources_path",
    )

    def __init__(
        self,
        *,
        feed_path: Path,
        changes_path: Path,
        published: int,
        jurisdiction_feeds: tuple[Path, ...] = (),
        site_path: Path | None = None,
        sources_path: Path | None = None,
    ) -> None:
        self.feed_path = feed_path
        self.changes_path = changes_path
        self.published = published
        self.jurisdiction_feeds = jurisdiction_feeds
        self.site_path = site_path
        self.sources_path = sources_path


def _guard(records: Iterable[ChangeRecord]) -> tuple[ChangeRecord, ...]:
    """Filter to publishable records, then prove it.

    The `if not record.publishable` branch below should be dead code — the comprehension
    above it already filtered. It is here anyway, because "should be dead" is a claim about
    today's code, and the cost of being wrong is publishing an unreviewed, machine-observed
    hash change to organizations who will reasonably read it as "the law changed."
    """
    selected = tuple(record for record in records if record.publishable)
    for record in selected:
        if not record.publishable:  # pragma: no cover — defensive; the filter guarantees this
            raise PublishError(
                f"refusing to publish change {record.id}: not reviewed and confirmed by a human"
            )
    return selected


def publish(
    records: Iterable[ChangeRecord],
    out_dir: Path,
    *,
    registry: Registry | None = None,
    feed_url: str = "https://github.com/ChelseaKR/id-churn-sentinel",
    now: datetime | None = None,
) -> PublishResult:
    """Write the whole published surface into `out_dir`. Reviewed records only.

    Always: `feed.xml` + `changes.json`, and one `feed-us-xx.xml` + `changes-us-xx.json` per
    jurisdiction. With a `registry` (the CLI always passes one): also `sources.json` — the
    inventory an integrator maps their own pages against — and `index.html`, the accessible
    front door that says what is watched, what is *not*, and why.

    The per-jurisdiction feeds are emitted for every jurisdiction **in the registry**, not
    merely for the ones that happen to have a published change. An org that serves one state
    needs a URL it can subscribe to now, and a feed that only springs into existence when
    something has already gone wrong is a feed nobody is subscribed to on the day it matters.
    """
    published = _guard(records)
    generated_at = now or datetime.now(UTC)

    out_dir.mkdir(parents=True, exist_ok=True)
    feed_path = out_dir / "feed.xml"
    changes_path = out_dir / "changes.json"

    feed_path.write_text(
        feed_xml(published, feed_url=feed_url, generated_at=generated_at), encoding="utf-8"
    )
    changes_path.write_text(
        changes_json(published, feed_url=feed_url, generated_at=generated_at), encoding="utf-8"
    )

    jurisdictions = (
        registry.jurisdictions
        if registry is not None
        else frozenset(record.jurisdiction for record in published)
    )
    feeds: list[Path] = []
    for jurisdiction in sorted(jurisdictions):
        slug = feed_slug(jurisdiction)
        scoped = tuple(r for r in published if r.jurisdiction == jurisdiction)
        jurisdiction_feed = out_dir / f"feed-{slug}.xml"
        jurisdiction_feed.write_text(
            feed_xml(
                scoped,
                feed_url=feed_url,
                generated_at=generated_at,
                jurisdiction=jurisdiction,
            ),
            encoding="utf-8",
        )
        (out_dir / f"changes-{slug}.json").write_text(
            changes_json(
                scoped,
                feed_url=feed_url,
                generated_at=generated_at,
                jurisdiction=jurisdiction,
            ),
            encoding="utf-8",
        )
        feeds.append(jurisdiction_feed)

    site_path: Path | None = None
    sources_path: Path | None = None
    if registry is not None:
        report = coverage(registry)
        sources_path = out_dir / "sources.json"
        sources_path.write_text(sources_json(registry, generated_at=generated_at), encoding="utf-8")
        site_path = out_dir / "index.html"
        site_path.write_text(
            render_site(registry, report, _sorted(published), generated_at=generated_at),
            encoding="utf-8",
        )

    return PublishResult(
        feed_path=feed_path,
        changes_path=changes_path,
        published=len(published),
        jurisdiction_feeds=tuple(feeds),
        site_path=site_path,
        sources_path=sources_path,
    )


def changes_json(
    records: Sequence[ChangeRecord],
    *,
    feed_url: str,
    generated_at: datetime,
    jurisdiction: str | None = None,
) -> str:
    """The documented, versioned JSON feed. Sorted newest-first; stable, so a consumer
    diffing two fetches sees only real movement.

    `jurisdiction` is present only on a per-jurisdiction file, and it is there so a consumer
    who has fetched `changes-us-tx.json` can tell, **from the document itself**, that its
    empty `changes` array is a statement about Texas and not about the United States. A
    scoped document that does not say what it is scoped to is a trap.
    """
    payload: dict[str, object] = {
        "schema_version": FEED_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "feed_url": feed_url,
    }
    if jurisdiction is not None:
        payload["jurisdiction"] = jurisdiction
    payload["disclaimer"] = (
        "This feed reports that an official source page changed, with the passage that "
        "changed. It does not assert what the law is, and it is not legal advice. Every "
        "item was reviewed by a named human before publication. An empty `changes` array "
        "means no human has confirmed a change yet — it is NOT a claim that nothing changed "
        "at any watched source."
    )
    payload["changes"] = [record.to_dict() for record in _sorted(records)]
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def sources_json(registry: Registry, *, generated_at: datetime) -> str:
    """The inventory: every watched source, and every named gap, as data.

    This is the document that answers an integrator's *first* question — not "what changed?"
    but "what do you watch, and what don't you?" — and it is the one that makes the mapping
    work described in `docs/CONSUMERS.md` possible without reading our source code. The gaps
    ship in the same file as the sources on purpose: an inventory that lists only what we
    cover invites a reader to infer that the rest is fine.
    """
    report = coverage(registry)
    payload = {
        "schema_version": SOURCES_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "coverage": {
            "sources": report.sources_total,
            "jurisdictions_covered": report.jurisdictions_covered,
            "jurisdictions_total": report.jurisdictions_total,
            "named_gaps": report.gaps_total,
            "watched_in_name_only": report.unreachable_total,
            "human_verified": 0,
        },
        "disclaimer": (
            "Every entry is machine-checked and NOT human-verified: a live fetch confirmed "
            "the URL answers and its title was read, which is a fact about a socket rather "
            "than a person confirming it is the right page. The feed's silence about any "
            "jurisdiction in `gaps` means nothing at all."
        ),
        "sources": [
            {
                "source_id": source.id,
                "jurisdiction": source.jurisdiction,
                "document_class": source.document_class,
                "url": source.url,
                "authority": source.authority,
                "human_verified": source.verified,
                "reachable_by_our_crawler": source.reachable,
                "notes": source.notes,
            }
            for source in sorted(registry.sources, key=lambda s: (s.jurisdiction, s.id))
        ],
        "gaps": [
            {
                "jurisdiction": gap.jurisdiction,
                "document_class": gap.document_class,
                "reason": gap.reason,
                "hosts": list(gap.hosts),
                "checked": gap.checked,
                "detail": gap.detail,
            }
            for gap in sorted(registry.gaps, key=lambda g: (g.jurisdiction, g.document_class))
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def feed_xml(
    records: Sequence[ChangeRecord],
    *,
    feed_url: str,
    generated_at: datetime,
    jurisdiction: str | None = None,
) -> str:
    """RSS 2.0. Hand-written rather than via a library: it is forty lines of stdlib string
    building, and it keeps the runtime dependency count at zero (see `pyproject.toml`).

    **An empty feed is a correct feed, and it says so.** With no reviewed changes there are
    no `<item>` elements — which is valid RSS 2.0 (`<item>` is optional) but is
    indistinguishable, to someone glancing at it, from a feed that is broken. That ambiguity
    matters here more than it would elsewhere: a consumer who decides our feed is broken
    stops looking at it, and a consumer who reads our silence as "nothing changed" when in
    fact we published nothing is exactly the wrong "no change" this repo exists to prevent.
    So an empty feed carries an XML comment saying which of the two it is. Readers ignore
    comments; humans debugging a pipeline at 2am do not.

    A per-jurisdiction feed says so in its `<title>` and its `<description>`, because the
    single most dangerous thing a scoped feed can do is look like an unscoped one: a Texas
    clinic reading an empty `feed-us-tx.xml` must not be able to mistake it for a statement
    about the whole country, and vice versa.
    """
    items = "\n".join(_item_xml(record) for record in _sorted(records))
    if not items:
        scope = f" for {jurisdiction}" if jurisdiction else ""
        items = (
            f"    <!-- No reviewed changes{scope} yet. This feed is EMPTY, not broken: every\n"
            "         change the watcher detects is held until a named human reviews and\n"
            "         confirms it, and none has been confirmed so far. An empty feed is\n"
            "         not a claim that nothing changed at any watched source — see the\n"
            "         disclaimer in changes.json and docs/CONSUMERS.md. -->"
        )
    title = FEED_TITLE if jurisdiction is None else f"{FEED_TITLE} — {jurisdiction}"
    description = (
        FEED_DESCRIPTION
        if jurisdiction is None
        else (
            f"{FEED_DESCRIPTION} This feed is scoped to {jurisdiction} ONLY: it says nothing "
            f"about any other jurisdiction, and its silence about {jurisdiction} is not "
            f"evidence that nothing changed there."
        )
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{_esc(title)}</title>\n"
        f"    <link>{_esc(feed_url)}</link>\n"
        f"    <description>{_esc(description)}</description>\n"
        "    <language>en-us</language>\n"
        f"    <lastBuildDate>{format_datetime(generated_at)}</lastBuildDate>\n"
        f"    <generator>id-churn-sentinel (schema {FEED_SCHEMA_VERSION})</generator>\n"
        f"{items}\n"
        "  </channel>\n"
        "</rss>\n"
    )


def _item_xml(record: ChangeRecord) -> str:
    # A published escalation must not read as a content change. "[TX] birth_certificate —
    # substantive change at <url>" would be actively misleading for a source that stopped
    # answering: a consumer would reasonably reword it as "Texas changed its page", when
    # what a human actually confirmed is that Texas's page cannot be reached any more.
    # Different observation, different sentence.
    removed = record.kind is ChangeKind.POSSIBLY_REMOVED
    what = "SOURCE UNREACHABLE (possibly removed)" if removed else "change"
    title = (
        f"[{record.jurisdiction}] {record.document_class} — "
        f"{record.significance} {what} at {record.url}"
    )
    detail = (
        "Source unreachable — human-reviewed escalation:"
        if removed
        else "Changed passages (unified diff of normalized text):"
    )
    body = (
        f"Jurisdiction: {record.jurisdiction}\n"
        f"Document class: {record.document_class}\n"
        f"Official source: {record.url}\n"
        f"Kind (machine-observed): {record.kind}\n"
        f"Significance (human-reviewed): {record.significance}\n"
        f"Reviewed by: {record.reviewer}\n"
        f"Reviewer note: {record.review_note or '(none)'}\n"
        f"Previous hash: {record.previous_hash}\n"
        f"New hash: {record.new_hash or '(none — the source could not be fetched)'}\n\n"
        f"{detail}\n{record.diff_excerpt}"
    )
    return (
        "    <item>\n"
        f"      <title>{_esc(title)}</title>\n"
        f"      <link>{_esc(record.url)}</link>\n"
        f'      <guid isPermaLink="false">{_esc(record.id)}</guid>\n'
        f"      <pubDate>{format_datetime(record.observed_at)}</pubDate>\n"
        f"      <category>{_esc(record.jurisdiction)}</category>\n"
        f"      <category>{_esc(record.document_class)}</category>\n"
        f"      <category>{_esc(record.kind)}</category>\n"
        f"      <description>{_esc(body)}</description>\n"
        "    </item>"
    )


def _sorted(records: Sequence[ChangeRecord]) -> list[ChangeRecord]:
    return sorted(records, key=lambda r: (r.observed_at, r.id), reverse=True)


def _esc(value: str) -> str:
    """XML text escaping. Five characters, no dependency, no XML parser in the process —
    which also means no XML parser attack surface (this module only ever *writes*)."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
