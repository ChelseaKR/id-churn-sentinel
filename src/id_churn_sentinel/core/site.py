"""The published site — an accessible, static, tracker-free index of what is watched.

**Why a site at all, when the product is a feed.** `docs/CONSUMERS.md` argues that the
customers are the incumbents — A4TE, Trans Lifeline, Namesake, legal-aid clinics — and that
they cannot keep up with churn. Until now they could not actually consume anything: the
artifacts existed in `dist/` in a git repo, the source inventory existed only as a JSON file
a Python program parses, and the gaps existed as a paragraph in a README. An integrator's
first question is not "what changed?" — it is **"what do you watch, and what don't you?"**,
because the answer decides whether our silence about Vermont means anything. This page
answers that question first and the feed second, on purpose.

**Four properties, and they are not decoration:**

*Accessible.* WCAG 2.2 AA. Semantic landmarks, one `<h1>`, real heading order, tables with
`<caption>` and `<th scope>`, a skip link, visible focus. **Status is never signalled by
colour alone** — "not watched" is the *word* "not watched", not a red dot. A legal-aid
worker using a screen reader is exactly who this page is for, and a diff or a coverage table
that only renders as colour is one they cannot read.

*Self-contained.* No JavaScript, no external stylesheet, no web font, no image, no CDN. Not
minimalism for its own sake: **every third-party request is a request that tells a third
party who is reading about trans ID law.** A page that surveils the people it claims to
protect would be a disgrace, and the only way to be sure it does not is to have nothing on
it to surveil with. `test_the_published_site_makes_no_third_party_requests` asserts this on
the published bytes, in the merge-blocking gate.

*Honest.* The gaps are on the page, in the same table as the coverage, with the host that
refused us and the reason. Coverage transparency that hides the holes is marketing.

*And honest about itself first.* The page's opening section — before the coverage numbers,
before the feed — says that **no human has confirmed a single one of these URLs**, and every
row of every source table carries that status as a word. This page's most dangerous property
is that it looks authoritative: a table headed *"OH · Birth certificate · Ohio Department of
Health"* reads as *"this is Ohio's official birth-certificate page"*, and nobody has checked
that it is. The disclosure is therefore structural — a `verification_status` field on the
source everywhere it is published, not a footnote — and a merge-blocking gate
(`tests/test_source_labelling.py`) asserts on the published bytes that no source appears in
any artifact without it.

*Derived.* Every number comes from `sources/registry.json` via `core/coverage.py`. Nothing
on this page is typed by a human, so nothing on it can go stale while the registry moves.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime

from id_churn_sentinel.core.changes import ChangeRecord
from id_churn_sentinel.core.coverage import CoverageReport
from id_churn_sentinel.core.registry import REJECTED, Registry, Source

__all__ = ["SITE_TITLE", "feed_slug", "render_site"]

# Every published surface says this, and the site says it first — above the coverage numbers,
# above the feed, above everything except the title. The numbers are the part a reader wants;
# the status is the part they need in order to know what the numbers are worth.
UNVERIFIED_HEADLINE = "UNVERIFIED — machine-checked, not human-confirmed"

SITE_TITLE = "ID Churn Sentinel"

_CLASS_LABELS = {
    "birth_certificate": "Birth certificate",
    "court_order_name_change": "Court-order name change",
    "drivers_license": "Driver's licence / state ID",
    "passport": "Passport",
    "selective_service": "Selective Service",
    "social_security": "Social Security",
}

_REASON_LABELS = {
    "wrong-page": "A human verifier found the URL we had was not the official page, and no "
    "replacement exists",
    "robots-disallowed": "Their robots.txt forbids us (honoured without appeal)",
    "blocked-403": "Serves a browser, 403s our User-Agent (we do not spoof one)",
    "blocked-404": "Returns 404 to every non-browser client (a WAF wearing a 404's clothes)",
    "blocked-200": "Returns a bot-wall page with HTTP 200 (a WAF wearing a 200's clothes)",
    "tls-unverifiable": "TLS chain our trust store cannot verify (we do not disable checks)",
    "js-challenge": "JavaScript interstitial to every non-browser client",
    "spa-no-text": "Client-rendered page with no policy text to hash",
    "false-drift": "Rotating content: watching it would cry wolf every week, forever",
    "unreachable": "Completes no HTTP exchange at all",
    "no-such-authority": "No such authority exists (the gap is in the world, not the crawler)",
}


def feed_slug(jurisdiction: str) -> str:
    """`TX` → `us-tx`; the federal bucket `US` → `us`.

    So a Texas legal-aid clinic subscribes to `feed-us-tx.xml` and is never asked to read
    fifty-one other states to find the one it serves.
    """
    key = jurisdiction.upper()
    return "us" if key == "US" else f"us-{key.lower()}"


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def render_site(
    registry: Registry,
    report: CoverageReport,
    records: Sequence[ChangeRecord],
    *,
    generated_at: datetime,
) -> str:
    """The whole page. One string, no template engine, no runtime dependency."""
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_esc(SITE_TITLE)} — watched sources, gaps, and the reviewed-change feed</title>",
            '<meta name="description" content="Human-reviewed changes at official US state and '
            "federal pages governing name and gender-marker changes on identity documents. "
            'No account, no tracking.">',
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            '<a class="skip" href="#main">Skip to main content</a>',
            _header(generated_at),
            '<main id="main">',
            _verification_notice(report),
            _what_this_is(),
            _coverage_section(report),
            _changes_section(records, registry),
            _endpoints_section(registry),
            _sources_section(registry),
            _gaps_section(report),
            "</main>",
            _footer(),
            "</body>",
            "</html>",
            "",
        ]
    )


def _header(generated_at: datetime) -> str:
    stamp = generated_at.strftime("%d %B %Y, %H:%M UTC")
    return "\n".join(
        [
            "<header>",
            f"<h1>{_esc(SITE_TITLE)}</h1>",
            '<p class="lede">Change detection over the <strong>official</strong> US pages '
            "that govern name and gender-marker changes on identity documents. It reports "
            "that a source page changed, and shows the passage that changed. "
            "<strong>It never asserts what the law is.</strong></p>",
            f'<p class="meta">Generated {_esc(stamp)} · No account · No tracking · '
            "Every published item reviewed by a named human</p>",
            "</header>",
        ]
    )


def _verification_notice(report: CoverageReport) -> str:
    """The first thing on the page, and the reason this page can be published at all.

    A visitor lands here, scrolls to a table headed *"OH — Birth certificate — Ohio Department
    of Health — <url>"*, and reads it as **"this is Ohio's official birth-certificate page."**
    That reading is entirely reasonable and it is not something the registry has earned:
    nobody has opened that URL and confirmed it. If it is wrong, the person acting on it loses
    a day, a fee, or a document — and they are the person least able to absorb any of the
    three.

    A footnote at the bottom of a long page does not fix that. The disclosure has to be where
    the reader is *before* they read the table, in the words they would use themselves, with
    the numbers derived so the day it stops being true it stops saying so by itself.
    """
    verified = report.verified_total
    total = report.sources_total
    if verified == total and total > 0:
        headline = f"All {total} sources are human-verified"
    elif verified == 0:
        # The sentence a reader must not be able to skim past, and it is the true one today.
        headline = f"no human has confirmed any of these {total} sources"
    else:
        headline = f"only {verified} of {total} sources are human-verified"
    return "\n".join(
        [
            '<section class="notice" aria-labelledby="verification">',
            f'<h2 id="verification">Read this first: {_esc(headline)}</h2>',
            f"<p><strong>Every source listed on this page is a <em>candidate</em> official "
            f"URL.</strong> Our crawler fetched it and read its title. That is all that has "
            f"happened to {report.unverified_total} of the {total} entries below: "
            f"<strong>{_esc(UNVERIFIED_HEADLINE)}</strong>. "
            "<strong>No human has confirmed that each one is the official page it claims to "
            "be</strong> for that document class in that jurisdiction.</p>",
            "<p><strong>Do not rely on this list as authoritative guidance, and do not hand it "
            "to someone who will.</strong> A machine cannot tell a state's real "
            "birth-certificate page from a convincing-looking one. It cannot even reliably tell "
            "a live page from a bot-wall: one federal site answers our crawler with a page "
            "titled <q>Request Access</q> and serves it with <strong>HTTP 200</strong>. A "
            "status code is not a verification, and a title is not a verification. A person "
            "is.</p>",
            "<h3>What this tool does claim</h3>",
            "<ul>",
            "<li><strong>This URL changed</strong> — the text at this address is not the text "
            "that was here last week.</li>",
            "<li><strong>This is what changed in it</strong> — the passage, with a reproducible "
            "hash before and after.</li>",
            "</ul>",
            "<h3>What this tool never claims</h3>",
            "<ul>",
            "<li><strong>What the law is</strong>, or what any change means legally. Not in the "
            "feed, not in a summary field, not ever.</li>",
            "<li><strong>That an unverified URL is the right page.</strong> Until a named human "
            "has confirmed it, the entry is a lead, not a citation.</li>",
            "</ul>",
            '<p>Every source below, in <a href="sources.json">sources.json</a>, and in every '
            "per-jurisdiction feed carries a machine-readable "
            "<code>verification_status</code> field for exactly this reason — so that an "
            "integrator cannot consume a source without being told what is behind it. The "
            "verification queue and how it is worked: "
            '<a href="https://github.com/ChelseaKR/id-churn-sentinel/blob/main/docs/VERIFYING.md">'
            "docs/VERIFYING.md</a>.</p>",
            "</section>",
        ]
    )


def _what_this_is() -> str:
    return "\n".join(
        [
            '<section aria-labelledby="what">',
            '<h2 id="what">What this tool will and will not tell you</h2>',
            "<h3>It will tell you</h3>",
            "<ul>",
            "<li>That an official page changed, with the <strong>passage that changed</strong>, "
            "the official URL, and a reproducible hash of the page text before and after.</li>",
            "<li>That a page we were watching <strong>stopped answering</strong> for long "
            "enough that an outage is no longer the likeliest explanation.</li>",
            "<li>Exactly what we do <strong>not</strong> watch, and which host refused us.</li>",
            "</ul>",
            "<h3>It will never tell you</h3>",
            "<ul>",
            "<li><strong>What the law is.</strong> A change to a web page is not a change to "
            "the law, and a hash comparison cannot read law. Advocates for Trans Equality, "
            "Trans Lifeline, Namesake and lawyers do that work; this is the monitor "
            "underneath it, not a replacement for it.</li>",
            "<li><strong>What a change means.</strong> Whether a change is editorial or "
            "substantive is a judgment, and a named human makes it. Nothing here is "
            "classified by a machine.</li>",
            "<li><strong>What you should do.</strong> This is not legal advice.</li>",
            "</ul>",
            "<p><strong>Silence from this feed is not evidence that nothing changed.</strong> "
            "Policy can move by an internal directive that never touches a web page, and this "
            "tool would see nothing. Read the gaps below before you read the silence.</p>",
            "</section>",
        ]
    )


def _coverage_section(report: CoverageReport) -> str:
    rows = "\n".join(
        f'<tr><th scope="row">{_esc(_CLASS_LABELS.get(cls, cls))}</th><td>{count}</td></tr>'
        for cls, count in report.by_document_class
    )
    return "\n".join(
        [
            '<section aria-labelledby="coverage">',
            '<h2 id="coverage">Coverage</h2>',
            '<dl class="stats">',
            f"<div><dt>Sources watched</dt><dd>{report.sources_total}</dd></div>",
            f"<div><dt>Jurisdictions</dt><dd>{report.jurisdictions_covered} of "
            f"{report.jurisdictions_total}</dd></div>",
            f"<div><dt>Named gaps</dt><dd>{report.gaps_total}</dd></div>",
            f"<div><dt>Watched in name only</dt><dd>{report.unreachable_total}</dd></div>",
            f"<div><dt>Human-verified</dt>"
            f"<dd>{report.verified_total} of {report.sources_total}</dd></div>",
            f"<div><dt>Rejected by a human</dt><dd>{report.rejected_total}</dd></div>",
            "</dl>",
            f"<p><strong>{report.unverified_total} of the {report.sources_total} sources in "
            "this registry are machine-checked and <em>not</em> human-verified.</strong> A "
            "live fetch confirmed each URL answers, and its title was read. That is a fact "
            "about a socket, not a person confirming it is the right page — those are "
            "different claims, and this project keeps them in different fields, on every "
            "source, in every artifact.</p>",
            f"<p><strong>{report.unreachable_total} of the {report.sources_total} registered "
            "sources cannot currently be fetched</strong> by our own crawler and are "
            "therefore <em>watched in name only</em>. They are listed as such in the "
            "inventory below rather than deleted, because deleting them would erase the fact "
            "that we cannot watch them.</p>",
            "<table>",
            "<caption>Sources by document class</caption>",
            '<thead><tr><th scope="col">Document class</th>'
            '<th scope="col">Sources</th></tr></thead>',
            f"<tbody>\n{rows}\n</tbody>",
            "</table>",
            "</section>",
        ]
    )


def _changes_section(records: Sequence[ChangeRecord], registry: Registry) -> str:
    if not records:
        body = [
            '<p class="empty"><strong>No reviewed changes yet. This log is empty, not '
            "broken.</strong></p>",
            "<p>Every change the watcher detects is held until a named human reviews it, and "
            "none has been confirmed so far. <strong>An empty log does not mean nothing "
            "changed at any watched source</strong> — those are different sentences, and "
            "conflating them is the exact failure this project treats as its primary safety "
            "risk. No change has been manufactured to make this page look alive.</p>",
        ]
    else:
        body = [_change_article(record, registry) for record in records]
    return "\n".join(
        [
            '<section aria-labelledby="changes">',
            '<h2 id="changes">Reviewed changes</h2>',
            *body,
            "</section>",
        ]
    )


def _change_article(record: ChangeRecord, registry: Registry) -> str:
    """One reviewed change. The diff is text in a `<pre>`, and the +/- markers carry the
    meaning — a diff rendered only in red and green is a diff a blind reviewer cannot read
    (docs/ROADMAP.md §5).

    It carries **two** human statuses, and they are not the same status. *Reviewed by* is the
    person who read this diff and decided it mattered. *Source verification* is whether anyone
    has ever confirmed that the URL underneath it is the page it claims to be. A change record
    that named a reviewer and said nothing about the source would let a reader conclude both
    had been checked — which, today, would be wrong 152 times out of 152.
    """
    kind = (
        "Source unreachable (possibly removed)"
        if str(record.kind) == "possibly_removed"
        else "Content change"
    )
    verification = registry.verification_of(record.source_id)
    return "\n".join(
        [
            '<article class="change">',
            f"<h3>{_esc(record.jurisdiction)} — "
            f"{_esc(_CLASS_LABELS.get(record.document_class, record.document_class))}</h3>",
            "<dl>",
            f"<div><dt>Observed</dt><dd>{_esc(record.observed_at.isoformat())}</dd></div>",
            f"<div><dt>What the machine saw</dt><dd>{_esc(kind)}</dd></div>",
            f"<div><dt>What the human judged</dt><dd>{_esc(str(record.significance))}</dd></div>",
            f"<div><dt>Reviewed by</dt><dd>{_esc(record.reviewer or '')}</dd></div>",
            f"<div><dt>Source verification</dt><dd><strong>"
            f"{_esc(verification.label)}</strong><br>{_esc(verification.statement)}</dd></div>",
            f'<div><dt>Official source</dt><dd><a href="{_esc(record.url)}">'
            f"{_esc(record.url)}</a></dd></div>",
            f"<div><dt>Change id</dt><dd><code>{_esc(record.id)}</code></dd></div>",
            "</dl>",
            f"<p>{_esc(record.review_note)}</p>" if record.review_note else "",
            "<h4>The passage that changed</h4>",
            f"<pre><code>{_esc(record.diff_excerpt)}</code></pre>",
            "</article>",
        ]
    )


def _endpoints_section(registry: Registry) -> str:
    per_jurisdiction = "\n".join(
        f'<li><a href="feed-{feed_slug(j)}.xml">feed-{feed_slug(j)}.xml</a> · '
        f'<a href="changes-{feed_slug(j)}.json">changes-{feed_slug(j)}.json</a> '
        f"<span>({_esc(j)})</span></li>"
        for j in sorted(registry.jurisdictions)
    )
    return "\n".join(
        [
            '<section aria-labelledby="endpoints">',
            '<h2 id="endpoints">Endpoints</h2>',
            "<p><strong>No account. No API key. No email address. No tracking.</strong> "
            "Fetch these with <code>curl</code>, a feed reader, or a cron job. We do not want "
            "to know who reads this: a subscriber list for a trans-ID-law feed is a list of "
            "trans people, and the only way to keep that list safe is to never create it. "
            "Consequently we cannot report readership and never will.</p>",
            "<ul>",
            '<li><a href="feed.xml">feed.xml</a> — RSS 2.0, every jurisdiction.</li>',
            '<li><a href="changes.json">changes.json</a> — the versioned JSON feed. This is '
            "the one you integrate against.</li>",
            '<li><a href="sources.json">sources.json</a> — the full inventory of what is '
            "watched and what is not, so you can map your own pages to our "
            "<code>source_id</code>s.</li>",
            "</ul>",
            "<h3>One jurisdiction at a time</h3>",
            "<p>An organisation that serves one state should not have to consume all 52. "
            "Every jurisdiction has its own feed, and it exists whether or not it has items "
            "yet.</p>",
            f'<ul class="feeds">\n{per_jurisdiction}\n</ul>',
            "</section>",
        ]
    )


def _sources_section(registry: Registry) -> str:
    blocks = []
    for jurisdiction in sorted(registry.jurisdictions):
        sources = sorted(
            (s for s in registry.sources if s.jurisdiction == jurisdiction),
            key=lambda s: (s.document_class, s.id),
        )
        rows = "\n".join(_source_row(source) for source in sources)
        blocks.append(
            "\n".join(
                [
                    "<table>",
                    f"<caption>{_esc(jurisdiction)} — {len(sources)} watched "
                    f"source{'s' if len(sources) != 1 else ''}</caption>",
                    '<thead><tr><th scope="col">Document class</th>'
                    '<th scope="col">Issuing authority</th>'
                    '<th scope="col">Watched page</th>'
                    '<th scope="col">Human verification · watch status</th></tr></thead>',
                    f"<tbody>\n{rows}\n</tbody>",
                    "</table>",
                ]
            )
        )
    return "\n".join(
        [
            '<section aria-labelledby="sources">',
            '<h2 id="sources">What is watched</h2>',
            "<p>Every URL below was fetched by this tool's own crawler, with its own TLS "
            "stack and its own descriptive User-Agent, and its title read, before it was "
            "added. <strong>The verification column says whether a named human has since "
            "opened it and confirmed it is the official page for that document class in that "
            "jurisdiction</strong> — and for anything marked "
            f"<strong>{_esc(UNVERIFIED_HEADLINE)}</strong>, nobody has.</p>",
            *blocks,
            "</section>",
        ]
    )


def _source_row(source: Source) -> str:
    """One source, and its status **as a word**.

    Two independent statuses, deliberately never merged into one green tick:

    * *can we fetch it* — a fact about our sockets;
    * *has a human confirmed it is the right page* — a fact about a person, and the one that
      decides whether a reader may act on the URL.

    A source can be perfectly reachable and completely wrong, which is precisely the case this
    column exists for. Neither status is signalled by colour: a red dot is invisible to the
    screen-reader user this table is most likely to be read by, and "the red ones are the bad
    ones" is not information a screen reader can convey (WCAG 2.2 AA, 1.4.1).
    """
    reach = "Watched" if source.reachable else "Watched in name only — our crawler cannot fetch it"
    # A REJECTED row is the most dangerous row on this page: a human has established that the
    # URL is wrong, and it is still listed (deleting it would take the finding with it, and
    # would say nothing to whoever picked it up last week). So it carries the whole sentence —
    # the reason and the instruction — rather than a status word a skimming reader can miss.
    detail = (
        f"<br><span>{_esc(source.verification.statement)}</span>"
        if source.verification_status == REJECTED
        else ""
    )
    return (
        f'<tr><th scope="row">'
        f"{_esc(_CLASS_LABELS.get(source.document_class, source.document_class))}</th>"
        f"<td>{_esc(source.authority)}</td>"
        f'<td><a href="{_esc(source.url)}">{_esc(source.url)}</a><br>'
        f"<code>{_esc(source.id)}</code></td>"
        f"<td><strong>{_esc(source.verification.label)}</strong>{detail}<br>"
        f"<span>{_esc(reach)}</span></td></tr>"
    )


def _gaps_section(report: CoverageReport) -> str:
    rows = "\n".join(
        "\n".join(
            [
                "<tr>",
                f'<th scope="row">{_esc(gap.jurisdiction)}</th>',
                f"<td>{_esc(_CLASS_LABELS.get(gap.document_class, gap.document_class))}</td>",
                f"<td>{_esc(_REASON_LABELS.get(gap.reason, gap.reason))}</td>",
                f"<td>{_esc(', '.join(gap.hosts))}</td>",
                f"<td>{_esc(gap.detail)}</td>",
                "</tr>",
            ]
        )
        for gap in sorted(report.gaps, key=lambda g: (g.jurisdiction, g.document_class))
    )
    return "\n".join(
        [
            '<section aria-labelledby="gaps">',
            '<h2 id="gaps">What is NOT watched, and why</h2>',
            f"<p>There are <strong>{report.gaps_total} named gaps</strong>. "
            "<strong>The feed's silence about any of them means nothing at all.</strong> "
            "Each one is a refusal we chose: in every case there is a two-line change — spoof "
            "a browser User-Agent, disable certificate verification, ignore a robots.txt — "
            "that would close the gap today and make this a tool that lies to a government "
            "server, about who it is, on behalf of a population under surveillance. We do not "
            "make it. The gap is recorded instead.</p>",
            "<table>",
            "<caption>Named gaps</caption>",
            '<thead><tr><th scope="col">Jurisdiction</th>'
            '<th scope="col">Document class</th>'
            '<th scope="col">Why we do not watch it</th>'
            '<th scope="col">Host(s) that refused us</th>'
            '<th scope="col">Detail</th></tr></thead>',
            f"<tbody>\n{rows}\n</tbody>",
            "</table>",
            "</section>",
        ]
    )


def _footer() -> str:
    return "\n".join(
        [
            "<footer>",
            "<p>ID Churn Sentinel is free software (MIT). It reports that an official source "
            "page changed; it does not assert what the law is, and it is not legal advice.</p>",
            '<p><a href="https://github.com/ChelseaKR/id-churn-sentinel">Source code, the '
            "registry, and the audit that explains every refusal above</a>.</p>",
            "</footer>",
        ]
    )


# Inline, because a stylesheet on another host is a request that tells that host who is
# reading about trans ID law. Colour is never load-bearing: it decorates a distinction the
# text already makes. Both schemes are specified, and both are checked for AA contrast.
_CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --muted:#4a4a4a; --line:#c9c9c9; --accent:#0b5d8a;
        --panel:#f4f4f5; --focus:#b32d00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#121316; --fg:#eceef1; --muted:#b8bcc4; --line:#3a3d44; --accent:#7fc2ea;
          --panel:#1c1e23; --focus:#ffb38a; }
}
* { box-sizing: border-box; }
body { margin:0; padding:0 1rem 4rem; background:var(--bg); color:var(--fg);
       font:1rem/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, main, footer { max-width: 62rem; margin: 0 auto; }
header { padding: 2.5rem 0 1rem; border-bottom: 2px solid var(--line); }
h1 { font-size: 2rem; margin: 0 0 .5rem; }
h2 { font-size: 1.5rem; margin: 2.5rem 0 .75rem; padding-top: 1rem;
     border-top: 1px solid var(--line); }
h3 { font-size: 1.15rem; margin: 1.5rem 0 .5rem; }
h4 { font-size: 1rem; margin: 1rem 0 .35rem; }
p, li { max-width: 68ch; }
.lede { font-size: 1.1rem; }
.meta { color: var(--muted); }
a { color: var(--accent); }
a:focus-visible, .skip:focus { outline: 3px solid var(--focus); outline-offset: 2px; }
.skip { position:absolute; left:-9999px; }
.skip:focus { position:static; display:inline-block; padding:.5rem; background:var(--panel); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }
pre { background: var(--panel); border: 1px solid var(--line); padding: 1rem;
      overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; display: block;
        overflow-x: auto; }
caption { text-align: left; font-weight: 700; padding: .5rem 0; }
th, td { border: 1px solid var(--line); padding: .5rem .6rem; text-align: left;
         vertical-align: top; font-size: .95rem; }
thead th { background: var(--panel); }
.stats { display: flex; flex-wrap: wrap; gap: 1rem; padding: 0; }
.stats div { border: 1px solid var(--line); padding: .75rem 1rem; min-width: 10rem; }
.stats dt { color: var(--muted); font-size: .85rem; }
.stats dd { margin: .25rem 0 0; font-size: 1.5rem; font-weight: 700; }
/* The unverified-registry notice. It is bordered and panelled so it reads as a caution, but
   the caution is carried entirely by the WORDS inside it — strip every style from this page
   and the reader still learns that no human has confirmed these URLs. Colour decorates a
   distinction the text has already made; it never makes one. */
.notice { background: var(--panel); border: 2px solid var(--line);
          border-left: .5rem solid var(--focus); padding: 1rem 1.25rem; margin: 1.5rem 0 2rem; }
.notice h2 { margin-top: 0; padding-top: 0; border-top: 0; }
.change { border: 1px solid var(--line); padding: 1rem; margin: 1rem 0; }
.change dl { display: grid; grid-template-columns: max-content 1fr; gap: .25rem .75rem; }
.change dl div { display: contents; }
.change dt { color: var(--muted); }
.change dd { margin: 0; }
.empty { background: var(--panel); border: 1px solid var(--line); padding: 1rem; }
.feeds { columns: 14rem; }
.feeds li { break-inside: avoid; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 2px solid var(--line);
         color: var(--muted); }
"""
