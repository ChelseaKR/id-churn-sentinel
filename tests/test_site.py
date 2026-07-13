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
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from id_churn_sentinel.core.changes import ChangeRecord
from id_churn_sentinel.core.coverage import coverage
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import Gap, Registry, Source, load_registry
from id_churn_sentinel.core.site import feed_slug, render_site

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
    return Registry(version="1.0", sources=(source, unreachable), gaps=(gap,))


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
    tmp_path: Path, confirmed_change: ChangeRecord
) -> None:
    """THE GATE, on **every byte we publish** — not just `index.html` and `changes.json`.

    Publishing the real registry writes 108 more files than the two the original tests
    covered: `feed-us-tx.xml`, `changes-us-tx.json`, and a pair for each of 52 jurisdictions.
    Those are the artifacts a legal-aid clinic actually subscribes to, and "the promise holds
    for the two files we happened to test" is not a promise — it is a coincidence with good
    intentions. A tracking pixel in `feed-us-tx.xml` would tell a third party which state's
    trans-ID feed someone reads, which is *more* identifying than the unscoped one, not less.

    So the sweep is over the whole `dist/` directory, and it is by construction: a future
    artifact nobody remembers to add to a list is covered the day it is written.
    """
    publish([confirmed_change], tmp_path, registry=load_registry())

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

    assert "Watched in name only — our crawler cannot fetch it" in page
    # And the fact is stated in prose too, not only in a table cell.
    assert "registered sources cannot currently be fetched" in page

    # No CSS class in this page encodes a status by colour name, which is how the red dot
    # gets in: someone adds `.status-red` and the information stops existing for a screen
    # reader while still "looking right" to the person who added it.
    for colour_class in (".status-red", ".status-green", ".ok {", ".bad {", ".error {"):
        assert colour_class not in page


def test_the_page_says_what_is_not_watched_and_who_refused_us(site_registry: Registry) -> None:
    """Coverage transparency that hides the holes is marketing. The gap, the reason, and the
    host that refused us are all on the page — and the page says plainly that our silence
    about a gap means nothing."""
    page = render(site_registry)

    assert "What is NOT watched, and why" in page
    assert "1 named gap" in page
    assert "VT" in page
    assert "dmv.vermont.gov" in page
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
