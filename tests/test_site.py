"""The published site — accessibility, and the promise that it does not surveil its readers.

Two of these tests carry the `feed_integrity` marker and therefore run in the merge-blocking
`make no-unreviewed-in-feed` gate, because they are not cosmetic checks:

* **No third-party requests.** Anyone reading this page is, with high probability, a trans
  person or someone working directly with trans people. Every external request — a CDN
  script, a web font, a tracking pixel, an analytics beacon — is a request that tells a third
  party who is reading about trans ID law, in a country where that list is a targeting
  artifact. `docs/RESPONSIBLE-TECH-AUDITS.md` §C says the mitigation is not to secure the
  list but to **never create it**, and a page that quietly loaded Google Fonts would break
  that promise while every other test stayed green.

* **No unreviewed record reaches the page.** The site renders the same records as the feed,
  so it inherits the same gate. A safety property that holds for `feed.xml` and not for
  `index.html` is not a safety property; it is a coincidence.

The rest is WCAG 2.2 AA structure. It is tested rather than asserted because a legal-aid
caseworker using a screen reader is precisely who this page exists for, and because "status
is signalled by colour alone" is the single easiest accessibility failure to ship by accident
and the one that would make the coverage table useless to them.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from id_churn_sentinel.core.changes import ChangeRecord
from id_churn_sentinel.core.coverage import coverage
from id_churn_sentinel.core.detect import watch_registry
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import (
    VERIFIED,
    FetchPolicyDecision,
    Gap,
    Registry,
    Source,
    Verification,
    load_registry,
)
from id_churn_sentinel.core.site import PAGES_URL, feed_slug, render_site
from id_churn_sentinel.core.status import build_public_status
from id_churn_sentinel.core.store import SnapshotStore

from .conftest import StubFetcher, eligible_source

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def site_registry(source: Source) -> Registry:
    unreachable = Source(
        id="us-ssa-name-change",
        jurisdiction="US",
        document_class="social_security",
        url="https://www.ssa.gov/personal-record/change-name",
        authority="Social Security Administration",
        verified=False,
        notes="403s every client we have.",
        checked={"at": "2026-07-13", "status": 403, "reachable": False},
    )
    gap = Gap(
        jurisdiction="VT",
        document_class="drivers_license",
        reason="blocked-403",
        hosts=("dmv.vermont.gov",),
        checked="2026-07-13",
        detail="403s our descriptive User-Agent. We do not spoof a browser UA.",
    )
    return Registry(version="1.0", sources=(eligible_source(source), unreachable), gaps=(gap,))


def render(registry: Registry, records: tuple[ChangeRecord, ...] = ()) -> str:
    return render_site(registry, coverage(registry), records, generated_at=NOW)


# ---- the merge-blocking properties -------------------------------------------------------


@pytest.mark.feed_integrity
def test_the_published_site_makes_no_third_party_requests(
    tmp_path: Path, site_registry: Registry, confirmed_change: ChangeRecord
) -> None:
    """THE GATE. A page that surveils trans people while claiming to protect them would be a
    disgrace, and the only way to be certain it does not is to have nothing on it that can.

    So: no script, no external stylesheet, no font, no image, no iframe, no beacon — and no
    form or account, because there is no user model in this codebase and there never will be.
    """
    publish([confirmed_change], tmp_path, registry=site_registry)
    page = (tmp_path / "index.html").read_text().lower()

    for forbidden in (
        "<script",
        "<iframe",
        "<img",
        "<form",
        "<input",
        '<link rel="stylesheet"',
        "@import",
        "googleapis",
        "google-analytics",
        "googletagmanager",
        "doubleclick",
        "facebook",
        "segment.io",
        "mixpanel",
        "plausible",
        "hotjar",
        "utm_source",
        "utm_medium",
        "cookie",
        "sign up",
        "subscribe to our",
        "enter your email",
        "api_key",
        "token=",
    ):
        assert forbidden not in page, f"the published site must not carry {forbidden!r}"

    # Every URL on the page is either a relative link to our own artifacts, or an official
    # source we are citing. Nothing is FETCHED from another host: a citation is an <a href>
    # the reader chooses to follow, which is categorically different from a subresource the
    # browser fetches on their behalf, without asking, the moment the page loads.
    for attribute in ("src=", "srcset=", "poster=", "@font-face"):
        assert attribute not in page


@pytest.mark.feed_integrity
def test_no_published_artifact_carries_a_tracker_including_the_per_jurisdiction_ones(
    tmp_path: Path,
) -> None:
    """THE GATE, on **every byte we publish** — not just `index.html` and `changes.json`.

    Publishing the real registry writes 108 more files than the two the original tests
    covered: `feed-us-tx.xml`, `changes-us-tx.json`, and a pair for each of 52 jurisdictions.
    Those are the artifacts a legal-aid clinic actually subscribes to, and "the promise holds
    for the two files we happened to test" is not a promise — it is a coincidence with good
    intentions. A tracking pixel in `feed-us-tx.xml` would tell a third party which state's
    trans-ID feed someone reads, which is *more* identifying than the unscoped one, not less.

    So the sweep is over the whole published directory, and it is by construction: a future
    artifact nobody remembers to add to a list is covered the day it is written.
    """
    publish([], tmp_path, registry=load_registry())

    artifacts = sorted(tmp_path.iterdir())
    assert len(artifacts) > 100, "expected the full published surface, per-jurisdiction included"

    for path in artifacts:
        content = path.read_text().lower()
        for forbidden in (
            "<script",
            "<iframe",
            "<img",
            "<form",
            "<input",
            "@import",
            "googleapis",
            "google-analytics",
            "googletagmanager",
            "doubleclick",
            "facebook.com",
            "segment.io",
            "mixpanel",
            "hotjar",
            "utm_source",
            "utm_medium",
            "set-cookie",
            "enter your email",
            "api_key",
            "apikey",
            "token=",
            "bearer ",
            "src=",
            "srcset=",
            "@font-face",
            # Nothing this project publishes is fetched from a host that is not ours. The
            # only http(s) URLs in the bytes are OFFICIAL SOURCES we cite and our own repo —
            # links a reader chooses to follow, never subresources a browser fetches for them.
            "cdn.",
            "analytics.",
        ):
            assert forbidden not in content, f"{path.name} must not carry {forbidden!r}"


# ---- servable from a subpath (the deployment property) -----------------------------------


def test_every_link_on_the_page_is_subpath_safe(
    tmp_path: Path, site_registry: Registry, confirmed_change: ChangeRecord
) -> None:
    """The site is served from `docs/` on GitHub Pages, which means it lives under
    `https://chelseakr.github.io/**id-churn-sentinel/**` — a SUBPATH, not the root of a domain.

    A root-absolute link (`href="/feed.xml"`) resolves to `https://chelseakr.github.io/feed.xml`
    — someone else's site — and 404s for every consumer. It is the classic way a static site
    breaks on deploy day, and the reason it survives review is that it looks completely correct
    when you serve the directory at a root with `python -m http.server` and click around.

    So: every link is either **relative** (our own artifacts), a **fragment** (the skip link),
    or an **absolute https** URL (an official source we cite, or our repo). Nothing else.
    """
    publish([confirmed_change], tmp_path, registry=site_registry)
    page = (tmp_path / "index.html").read_text()

    links = re.findall(r'href="([^"]+)"', page)
    assert links
    for link in links:
        if link.startswith(("https://", "#")):
            continue
        assert not link.startswith("/"), (
            f"{link!r} is root-absolute. Served from a Pages subpath it resolves off-site and "
            f"404s — and it will look fine in every local test that serves docs/ at a root."
        )
        assert not link.startswith("docs/"), (
            f"{link!r} repeats the publish directory. `docs/` IS the site root once Pages "
            f"serves it; this would resolve to /id-churn-sentinel/docs/…"
        )

    # And nothing on the page is fetched from anywhere at all — a subresource with a broken
    # relative path fails silently, which is the other half of the same deployment bug.
    assert "src=" not in page


def test_the_head_names_this_project_and_not_the_shared_origin(
    tmp_path: Path, site_registry: Registry
) -> None:
    """The canonical URL, and every social URL, must carry the `/id-churn-sentinel/` subpath.

    This site is one of six project sites served from the SAME origin,
    `chelseakr.github.io`, on paths rather than on domains of their own. That makes a
    plausible-looking canonical actively destructive in a way it would not be on a dedicated
    domain: `<link rel="canonical" href="/">` resolves to `https://chelseakr.github.io/`,
    which is not a shortened form of this site — it is a different address that today 404s,
    and every one of the six sites would claim the identical canonical. A crawler that
    believes them folds six unrelated projects into one document.

    So the check is not "is there a canonical" — an empty or origin-rooted one would pass
    that. It is that the canonical, `og:url`, `<title>` and `og:title` agree with each other
    and all name the subpath, and that the description a preview card shows is the same
    sentence the page's own `<meta name="description">` carries.
    """
    publish([], tmp_path, registry=site_registry)
    page = (tmp_path / "index.html").read_text()

    canonical = re.search(r'<link rel="canonical" href="([^"]+)">', page)
    assert canonical, "the page has no canonical URL"
    assert canonical.group(1) == PAGES_URL, (
        f"canonical is {canonical.group(1)!r}, not {PAGES_URL!r}"
    )

    def meta(attribute: str, name: str) -> str | None:
        found = re.search(rf'<meta {attribute}="{name}" content="([^"]*)">', page)
        return found.group(1) if found else None

    # Every social URL names this project, never the bare origin the six sites share.
    assert meta("property", "og:url") == PAGES_URL
    for url in (canonical.group(1), meta("property", "og:url")):
        assert url is not None
        assert url.rstrip("/") != "https://chelseakr.github.io", (
            f"{url!r} is the shared origin, which is a different site than this one"
        )
        assert "/id-churn-sentinel/" in url, f"{url!r} omits this project's path segment"

    # One sentence and one title, not two that can drift apart.
    title = re.search(r"<title>([^<]+)</title>", page)
    assert title is not None
    assert meta("property", "og:title") == title.group(1)
    assert meta("name", "description") == meta("property", "og:description")
    assert meta("name", "description"), "the page has no description"

    # A card type that promises an image must actually carry one. This repo publishes no
    # image, so it declares `summary` — the assertion holds either way round, so committing a
    # social image later fails here until og:image is added with it.
    card = meta("name", "twitter:card")
    assert card in {"summary", "summary_large_image"}, f"unknown twitter:card {card!r}"
    if card == "summary_large_image":
        assert meta("property", "og:image"), "summary_large_image promises an og:image"
    assert meta("property", "og:type") == "website"


def test_the_published_directory_turns_jekyll_off(tmp_path: Path, site_registry: Registry) -> None:
    """`.nojekyll` or GitHub Pages runs the output through Jekyll, which **silently drops**
    files and directories whose names begin with an underscore and tells nobody. The published
    surface is data an organisation acts on; a deploy step that quietly removes files from it is
    exactly the unwitnessed failure this project exists to refuse. The publisher writes the file
    so that no human has to remember it once.
    """
    publish([], tmp_path, registry=site_registry)

    nojekyll = tmp_path / ".nojekyll"
    assert nojekyll.exists()
    assert nojekyll.read_text() == ""


@pytest.mark.feed_integrity
def test_unreviewed_drift_never_reaches_the_site(
    tmp_path: Path, site_registry: Registry, observed_change: ChangeRecord
) -> None:
    """The site renders what the feed renders, so it inherits the feed's gate. A property
    that holds for feed.xml and not for index.html is a coincidence, not a property."""
    publish([observed_change], tmp_path, registry=site_registry)
    page = (tmp_path / "index.html").read_text()

    assert observed_change.id not in page
    assert observed_change.diff_excerpt not in page
    assert "No reviewed changes yet" in page
    assert "not broken" in page


# ---- accessibility (WCAG 2.2 AA structure) -----------------------------------------------


def test_the_page_has_a_language_one_h1_and_no_skipped_heading_levels(
    site_registry: Registry, confirmed_change: ChangeRecord
) -> None:
    page = render(site_registry, (confirmed_change,))

    assert '<html lang="en">' in page
    assert page.count("<h1>") == 1

    levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", page)]
    assert levels[0] == 1
    for previous, current in pairwise(levels):
        assert current <= previous + 1, f"heading level jumped from h{previous} to h{current}"


def test_landmarks_a_skip_link_and_a_focus_style_exist(site_registry: Registry) -> None:
    """A keyboard user must be able to get past the header, and must be able to SEE where
    they are — an invisible focus ring is a keyboard trap with good manners."""
    page = render(site_registry)

    assert '<a class="skip" href="#main">Skip to main content</a>' in page
    assert '<main id="main">' in page
    assert "<header>" in page
    assert "<footer>" in page
    assert ":focus-visible" in page
    assert "outline:" in page.replace("outline: ", "outline:")


def test_stale_health_label_is_not_repeated(site_registry: Registry) -> None:
    page = render(site_registry)

    assert "Run health: STALE" in page
    assert "STALE · STALE" not in page


def test_every_table_has_a_caption_and_scoped_headers(site_registry: Registry) -> None:
    """A table with no `<caption>` and no `<th scope>` is an unlabelled grid of strings to a
    screen reader — which is what the coverage table would become, for exactly the caseworker
    who needs to know whether we watch their state."""
    page = render(site_registry)

    tables = re.findall(r"<table>(.*?)</table>", page, re.DOTALL)
    assert tables
    for table in tables:
        assert "<caption>" in table
        assert 'scope="col"' in table
        assert 'scope="row"' in table


def test_status_is_never_signalled_by_colour_alone(site_registry: Registry) -> None:
    """The one accessibility failure this page could most easily ship: a red dot for "we
    cannot fetch this". The status has to be a WORD, and the word has to say what it means.
    """
    page = render(site_registry)

    assert "Crawler-unreachable when last machine-checked" in page
    assert "Not monitored — excluded by eligibility" in page
    # And the fact is stated in prose too, not only in a table cell.
    assert "registered candidates could not be fetched" in page

    # No CSS class in this page encodes a status by colour name, which is how the red dot
    # gets in: someone adds `.status-red` and the information stops existing for a screen
    # reader while still "looking right" to the person who added it.
    for colour_class in (".status-red", ".status-green", ".ok {", ".bad {", ".error {"):
        assert colour_class not in page


def test_long_inline_code_can_wrap_without_forcing_horizontal_page_scroll(
    site_registry: Registry,
) -> None:
    page = render(site_registry)

    assert "code {" in page
    assert "overflow-wrap: anywhere" in page


def test_page_section_navigation_reaches_the_primary_tasks(site_registry: Registry) -> None:
    page = render(site_registry)

    assert '<nav class="section-nav" aria-label="Page sections">' in page
    for target in ("verification", "run-health", "changes", "endpoints", "sources", "gaps"):
        assert f'href="#{target}"' in page
        assert f'id="{target}"' in page


def test_the_page_says_what_is_not_watched_and_who_refused_us(site_registry: Registry) -> None:
    """Coverage transparency that hides the holes is marketing. The gap, the reason, and the
    host that refused us are all on the page — and the page says plainly that our silence
    about a gap means nothing."""
    page = render(site_registry)

    # Read the host off the fixture rather than repeating the literal. This asserts the
    # stronger property anyway — the page shows *the gap's* host, not a string that happens
    # to match one — and it keeps CodeQL's py/incomplete-url-substring-sanitization quiet.
    # That rule is purely syntactic: it flags any `"<hostname-shaped literal>" in <anything>`
    # as a URL-sanitization bypass, with no notion of whether a URL or a security decision is
    # involved. Here the right operand is a rendered HTML document and the comparison is a
    # test assertion, so the alert is a false positive. Please do not inline the literal back.
    blocked_host = site_registry.gaps[0].hosts[0]

    assert "What is NOT watched, and why" in page
    assert "1 named gap" in page
    assert "VT" in page
    assert blocked_host in page
    assert "403s our User-Agent (we do not spoof one)" in page
    assert "silence about any of them means nothing at all" in page


def test_the_page_refuses_the_jobs_this_tool_does_not_do(site_registry: Registry) -> None:
    """The site is the most-read surface this project has, so the refusal has to be on it —
    in a heading, not in a footnote. A reader who takes a change record as a statement of law
    is the harm this whole repo is organised around."""
    page = render(site_registry)

    assert "It will never tell you" in page
    assert "What the law is." in page
    assert "not legal advice" in page
    assert "Silence from this feed is not evidence that nothing changed." in page


# ---- a zero denominator is not a clean sheet (issue #18) ------------------------------------


def test_a_run_with_no_eligible_sources_does_not_render_as_a_completed_run(
    tmp_path: Path, site_registry: Registry, source: Source
) -> None:
    """`attempted 0 of 0 eligible sources; 0 successful retrievals` is arithmetically true and
    reads as a run that had nothing to do and did it perfectly. What it means is that the
    watcher was allowed to look at nothing, and completeness over an empty denominator is not
    a number at all."""
    unverified = Registry(version="1.0", sources=(source,))
    with SnapshotStore(tmp_path / "s.db") as store:
        watch_registry(unverified, store, StubFetcher(), as_of=NOW.date(), started_at=NOW)
        status = build_public_status(store, now=NOW)

    page = render_site(unverified, coverage(unverified), (), generated_at=NOW, run_status=status)

    assert "attempted 0 of 0 eligible sources" not in page
    assert "0 successful retrievals" not in page
    assert "no source was attempt-eligible, so this run examined nothing" in page
    assert "there is no denominator here and the zero counts are not a measurement" in page


def test_a_fully_verified_registry_that_watches_nothing_does_not_headline_the_verification(
    source: Source,
) -> None:
    """The exact page the issue measured: every source human-verified, none attempt-eligible
    because no fetch-policy decision has been recorded, and the strongest verification claim
    the page can make sitting at the top of it."""
    verified_only = Registry(
        version="1.0",
        sources=(
            replace(
                eligible_source(source),
                fetch_policy=FetchPolicyDecision(),  # verified, but no policy decision
            ),
        ),
    )

    page = render_site(verified_only, coverage(verified_only), (), generated_at=NOW)

    assert "Read this first: All 1 sources are human-verified" not in page
    assert "human-verified, and 0 of them are monitored" in page
    assert "0 of 1 registered candidates are attempt-eligible" in page


def test_the_real_registry_renders(tmp_path: Path) -> None:
    """The committed registry — 152 sources, 52 jurisdictions, 12 gaps — actually renders,
    and every jurisdiction gets a subscribable feed link whether or not it has items yet."""
    registry = load_registry()

    publish([], tmp_path, registry=registry)
    page = (tmp_path / "index.html").read_text()

    for jurisdiction in registry.jurisdictions:
        slug = feed_slug(jurisdiction)
        assert f'href="feed-{slug}.xml"' in page
        assert (tmp_path / f"feed-{slug}.xml").exists()
    assert "MI" in page and "NH" in page


# -- "human-verified" is not "watched" (issue #18) ---------------------------------
#
# `verified` is a fact about a person opening a URL. Attempt-eligibility additionally needs an
# evidence reference, a recheck expiry and a dated fetch-policy decision. A registry can be
# fully verified and watch nothing — which is precisely the state a volunteer who works the
# whole queue ends in, because `sentinel verify` writes only the first of those. The page's
# loudest line must not read as "this registry is finished" at that moment.


def _verified_but_unwatchable(source: Source) -> Registry:
    """Every source human-verified by a named person, and none of them attempt-eligible —
    exactly what burning down the verification queue produces today."""
    return Registry(
        version="1.0",
        sources=(
            replace(
                source,
                verified=True,
                verification=Verification(
                    status=VERIFIED, verifier="A Named Human", at="2026-01-01"
                ),
            ),
        ),
    )


def test_a_fully_verified_but_unwatched_registry_does_not_headline_as_finished(
    source: Source,
) -> None:
    registry = _verified_but_unwatchable(source)

    page = render_site(
        registry, coverage(registry), [], generated_at=NOW, eligibility_as_of=NOW.date()
    )

    assert "All 1 sources are human-verified" not in page
    assert "are human-verified, and 0 of them are monitored" in page
    assert "This public deployment is not currently an operating monitor" in page


def test_the_completeness_headline_survives_when_it_is_actually_true(source: Source) -> None:
    """The claim is not removed, only conditioned. A registry that really is verified AND
    watched still says so — otherwise the page would understate on the day the work is done."""
    registry = Registry(version="1.0", sources=(eligible_source(source),))

    page = render_site(
        registry, coverage(registry), [], generated_at=NOW, eligibility_as_of=NOW.date()
    )

    assert "All 1 sources are human-verified" in page
