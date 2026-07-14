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

**It is published from `docs/`, and that is a deployment fact with a reason.** GitHub Pages,
on the free branch-based path, will serve exactly two source paths: the repository root or
`/docs`. The Actions-based Pages deploy — which could serve any directory — is unavailable
here, because this account has an account-wide Actions spending limit and a site that only
exists once somebody else's billing system agrees to run a workflow is a site that does not
exist. So the published surface lives in `docs/`, committed, servable straight from the
branch with no build step and no CI run. See `docs/README.md`.

Two consequences are load-bearing in this module. First, **every link this code emits must be
relative** — Pages serves the site under `/<repo-name>/`, so a link beginning with `/` would
resolve to the wrong origin path and 404 for every consumer. Second, `.nojekyll` is written
alongside the artifacts: without it Pages runs Jekyll over the directory, and Jekyll silently
drops files and directories whose names begin with an underscore. Neither failure is loud.
Both are tested (`tests/test_site.py`).

**A source never travels without its verification status.** This is the third merge-blocking
property, and it is newer and more load-bearing than it looks. Every artifact here lists a
source per (jurisdiction, document class), which a reader will reasonably take to mean *"this
is Ohio's official birth-certificate page."* Today **no human has confirmed a single one of
them** — they are machine-checked candidates, and a socket returning 200 at a plausible URL is
not a person confirming it is the right page. If one of them is wrong, that implicit claim
sends a trans person to the wrong office.

So the status is not a footnote on the front page; it is a field on the source, in every
document that carries the source, in words a screen reader can read (`unverified` ·
`verified` · `rejected` — never a colour). And `registry` is a **required** argument to
:func:`publish`: there is no way to write an artifact from this module without holding the
registry that knows each source's verification status, which is what makes "a source cannot
appear in a published artifact without its status alongside it" a structural fact rather than
a promise. `tests/test_source_labelling.py` asserts it on the published bytes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord
from id_churn_sentinel.core.coverage import coverage
from id_churn_sentinel.core.registry import (
    UNVERIFIED,
    Registry,
    Source,
    Verification,
)
from id_churn_sentinel.core.site import REPO_URL, feed_slug, render_site
from id_churn_sentinel.errors import PublishError

__all__ = [
    "FEED_SCHEMA_VERSION",
    "SOURCES_SCHEMA_VERSION",
    "PublishResult",
    "changes_json",
    "feed_xml",
    "publish",
    "source_payload",
    "sources_json",
]


# Bump on any breaking change to the `changes.json` shape. Consumers pin against it.
#
# The versioning promise, stated once and kept: **a bump means a break.** Adding a new
# The contract is closed: its schema rejects unknown properties, so adding even an optional
# field can break a validating consumer and requires a separately versioned contract. Removing
# a field, renaming one, changing a type, or narrowing an enum is likewise a break. The shape is
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

# Said in every artifact, next to the sources themselves, because the registry's status is not
# a detail about our process — it is the thing that decides how much weight a reader may put on
# any given URL in it.
REGISTRY_DISCLAIMER = (
    "THE SOURCE REGISTRY IS NOT HUMAN-VERIFIED. Each entry is a CANDIDATE official URL: our "
    "own crawler fetched it and read its title, and its `verification_status` says whether a "
    "named human has since confirmed it is the official page for that document class in that "
    "jurisdiction. `unverified` means nobody has. A machine cannot tell a state's real "
    "birth-certificate page from a convincing-looking one — it cannot even tell a live page "
    "from a bot-wall served with HTTP 200 — so do not rely on an unverified entry as "
    "authoritative guidance, and do not present it to anyone as one. What this tool does "
    "claim: this URL changed, and here is what changed in it. What it never claims: what the "
    "law is."
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


# Written into the published directory, and it is not a formality. GitHub Pages runs the output
# through Jekyll unless this file exists, and Jekyll **silently drops** any file or directory
# whose name begins with an underscore, rewrites what it feels like, and reports none of it. The
# published surface here is data an organisation acts on; a build step that quietly removes files
# from it is exactly the kind of unwitnessed failure this project exists to refuse. So the file
# is written by the publisher rather than left to a human to remember once.
def _write_nojekyll(out_dir: Path) -> None:
    """Turn Jekyll off over the published output. The file is empty by convention; its
    existence is the whole signal."""
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")


def publish(
    records: Iterable[ChangeRecord],
    out_dir: Path,
    *,
    registry: Registry,
    feed_url: str = REPO_URL,
    now: datetime | None = None,
) -> PublishResult:
    """Write the whole published surface into `out_dir` (in this repo, `docs/`). Reviewed
    records only.

    `feed.xml` + `changes.json`; one `feed-us-xx.xml` + `changes-us-xx.json` per jurisdiction;
    `sources.json`, the inventory an integrator maps their own pages against; `index.html`, the
    accessible front door that says what is watched, what is *not*, and why; and `.nojekyll`,
    which is not decoration — see :func:`_write_nojekyll`.

    **`registry` is required, and that is the gate.** It used to be optional, defaulting to
    "publish the changes and skip the inventory" — which meant it was possible to write a feed
    citing `source_id: oh-odh-vital-records` with no way of saying whether a human had ever
    confirmed that entry. A source that appears in an artifact without its verification status
    is an implicit claim of authority nobody made. There is now no code path that can produce
    one: you cannot call this function without the registry that knows.

    The per-jurisdiction feeds are emitted for every jurisdiction **in the registry**, not
    merely for the ones that happen to have a published change. An org that serves one state
    needs a URL it can subscribe to now, and a feed that only springs into existence when
    something has already gone wrong is a feed nobody is subscribed to on the day it matters.
    """
    published = _guard(records)
    generated_at = now or datetime.now(UTC)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_nojekyll(out_dir)
    feed_path = out_dir / "feed.xml"
    changes_path = out_dir / "changes.json"

    feed_path.write_text(
        feed_xml(published, feed_url=feed_url, generated_at=generated_at, registry=registry),
        encoding="utf-8",
    )
    changes_path.write_text(
        changes_json(published, feed_url=feed_url, generated_at=generated_at, registry=registry),
        encoding="utf-8",
    )

    feeds: list[Path] = []
    for jurisdiction in sorted(registry.jurisdictions):
        slug = feed_slug(jurisdiction)
        scoped = tuple(r for r in published if r.jurisdiction == jurisdiction)
        jurisdiction_feed = out_dir / f"feed-{slug}.xml"
        jurisdiction_feed.write_text(
            feed_xml(
                scoped,
                feed_url=feed_url,
                generated_at=generated_at,
                registry=registry,
                jurisdiction=jurisdiction,
            ),
            encoding="utf-8",
        )
        (out_dir / f"changes-{slug}.json").write_text(
            changes_json(
                scoped,
                feed_url=feed_url,
                generated_at=generated_at,
                registry=registry,
                jurisdiction=jurisdiction,
            ),
            encoding="utf-8",
        )
        feeds.append(jurisdiction_feed)

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


def source_payload(source: Source) -> dict[str, Any]:
    """One source, as an integrator receives it — **status included, always**.

    Flat rather than nested, because the field that matters must be impossible to miss and
    trivial to filter on: `jq '.sources[] | select(.verification_status != "verified")'` is a
    query an integrator can write in ten seconds, and one they will write once and keep.
    """
    return {
        "source_id": source.id,
        "jurisdiction": source.jurisdiction,
        "document_class": source.document_class,
        "url": source.url,
        "authority": source.authority,
        "verification_status": source.verification_status,
        "human_verified": source.verified,
        "verified_by": source.verification.verifier,
        "verified_at": source.verification.at,
        "verification_statement": source.verification.statement,
        "reachable_by_our_crawler": source.reachable,
        "notes": source.notes,
    }


def _verification_summary(sources: Sequence[Source], *, scope: str) -> dict[str, Any]:
    """The counts, plus a sentence that says what they mean. Machine-readable *and* readable:
    an integrator who looks only at the numbers still gets the number that matters (0), and
    one who looks only at the prose still gets told not to trust the list."""
    verified = sum(1 for s in sources if s.verified)
    rejected = sum(1 for s in sources if s.verification_status == "rejected")
    unverified = sum(1 for s in sources if s.verification_status == UNVERIFIED)
    return {
        "scope": scope,
        "sources": len(sources),
        "human_verified": verified,
        "unverified": unverified,
        "rejected": rejected,
        "statement": REGISTRY_DISCLAIMER,
    }


def changes_json(
    records: Sequence[ChangeRecord],
    *,
    feed_url: str,
    generated_at: datetime,
    registry: Registry,
    jurisdiction: str | None = None,
) -> str:
    """The documented, versioned JSON feed. Sorted newest-first; stable, so a consumer
    diffing two fetches sees only real movement.

    `jurisdiction` is present only on a per-jurisdiction file, and it is there so a consumer
    who has fetched `changes-us-tx.json` can tell, **from the document itself**, that its
    empty `changes` array is a statement about Texas and not about the United States. A
    scoped document that does not say what it is scoped to is a trap.

    **The sources travel with the changes**, scoped the same way, each carrying its
    `verification_status`. Two reasons, and the second is the important one. First, a Texas
    clinic that subscribed to `changes-us-tx.json` should not need a second request to learn
    which Texas pages we watch. Second — and this is the whole point — **the feed is currently
    empty**, so a consumer who only ever reads this file would otherwise learn *nothing* about
    the registry's status, and would go on believing that whatever we publish about Texas
    comes from a verified list of Texas's official pages. It does not. Now the file says so,
    in a field, before it says anything else.
    """
    scoped_sources = tuple(
        source
        for source in sorted(registry.sources, key=lambda s: (s.jurisdiction, s.id))
        if jurisdiction is None or source.jurisdiction == jurisdiction
    )
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
    payload["registry_verification"] = _verification_summary(
        scoped_sources, scope=jurisdiction or "all jurisdictions"
    )
    payload["changes"] = [
        _change_payload(record, registry.verification_of(record.source_id))
        for record in _sorted(records)
    ]
    payload["sources"] = [source_payload(source) for source in scoped_sources]
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _change_payload(record: ChangeRecord, verification: Verification) -> dict[str, Any]:
    """A published change, with the verification status of the source it cites.

    Without this field, an item reading *"[OH] birth_certificate changed at <url>"* is an
    implicit assertion that `<url>` **is** Ohio's birth-certificate page — which nobody has
    checked. The status rides along with every single item, including into the RSS body, so
    there is no surface on which the claim travels naked. If the cited source has since left
    the registry, the status is `withdrawn` and says so; it is never omitted, and it is never
    guessed.
    """
    return {**record.to_dict(), "source_verification": verification.to_dict()}


def sources_json(registry: Registry, *, generated_at: datetime) -> str:
    """The inventory: every watched source, and every named gap, as data.

    This is the document that answers an integrator's *first* question — not "what changed?"
    but "what do you watch, and what don't you?" — and it is the one that makes the mapping
    work described in `docs/CONSUMERS.md` possible without reading our source code. The gaps
    ship in the same file as the sources on purpose: an inventory that lists only what we
    cover invites a reader to infer that the rest is fine.

    And every source carries `verification_status`, because there is a *second* inference this
    document invites and must refuse: that a list of one official URL per (jurisdiction,
    document class) is a list somebody checked. It is not, yet. The status is on each entry,
    the counts are in `coverage`, and the sentence is in `disclaimer` — three places, because
    an integrator will read exactly one of them and we do not get to choose which.
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
            # Derived, not hard-coded. It was literally `0` in the source of this file — true
            # on the day it was written, and a number that would have silently stayed 0 while
            # a human burned down the whole queue. A count nobody recomputes is a claim that
            # will eventually be false in whichever direction nobody is watching.
            "human_verified": report.verified_total,
            "unverified": report.unverified_total,
            "rejected_by_a_human": report.rejected_total,
        },
        "disclaimer": REGISTRY_DISCLAIMER,
        "sources": [
            source_payload(source)
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
    registry: Registry,
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

    **And the channel says how many of the sources behind it a human has actually confirmed.**
    An RSS reader shows the channel description once and the items forever; if the registry's
    status only lived on the website, a subscriber would never see it at all.
    """
    scoped_sources = tuple(
        source
        for source in registry.sources
        if jurisdiction is None or source.jurisdiction == jurisdiction
    )
    items = "\n".join(
        _item_xml(record, registry.verification_of(record.source_id)) for record in _sorted(records)
    )
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
    description = f"{description} {_registry_sentence(scoped_sources, jurisdiction)}"
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


def _registry_sentence(sources: Sequence[Source], jurisdiction: str | None) -> str:
    """One sentence, in the channel description, stating the registry's verification state for
    whatever this feed is scoped to. Counted, not asserted — it will read differently the day
    someone finishes the burn-down, and it will read differently *by itself*."""
    scope = jurisdiction or "the registry"
    verified = sum(1 for s in sources if s.verified)
    where = f"in {scope}" if jurisdiction else "in the registry"
    if verified == len(sources) and sources:
        return f"All {len(sources)} sources {where} are HUMAN-VERIFIED. {REGISTRY_DISCLAIMER}"
    return (
        f"SOURCE REGISTRY: {verified} of {len(sources)} sources {where} are human-verified; "
        f"the rest are UNVERIFIED — machine-checked candidate URLs that no human has confirmed "
        f"are the official page for their document class. {REGISTRY_DISCLAIMER}"
    )


def _item_xml(record: ChangeRecord, verification: Verification) -> str:
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
        # Two different humans, two different jobs, and conflating them is the failure this
        # line exists to prevent. `Reviewed by` is the person who read the DIFF. `Source
        # verification` is whether anyone has ever confirmed that this URL is the page it
        # claims to be — and today, for every source in the registry, nobody has. An item that
        # named a reviewer but stayed silent about the source would read as though both had
        # been checked.
        f"Source verification: {verification.label}\n"
        f"  {verification.statement}\n"
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
        # Machine-readable in RSS too, so a pipeline can filter on it without parsing prose.
        # An RSS consumer who wants only human-verified sources can have that today; what they
        # cannot have is a feed that forgot to tell them the difference exists.
        f"      <category>source-verification:{_esc(verification.status)}</category>\n"
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
