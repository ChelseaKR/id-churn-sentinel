"""The published site — accessibility, and the promise that it does not surveil its readers.

The tests that carry the `feed_integrity` marker run in the merge-blocking
`make no-unreviewed-in-feed` gate, because they are not cosmetic checks:

* **No third-party request except the one the owner decided on.** Anyone reading this page
  is, with high probability, a trans person or someone working directly with trans people.
  Every external request — a CDN script, a web font, a tracking pixel, an analytics beacon —
  is a request that tells a third party who is reading about trans ID law, in a country where
  that list is a targeting artifact. `docs/RESPONSIBLE-TECH-AUDITS.md` §C says the mitigation
  is not to secure the list but to **never create it**, and until 2026-09-18 this gate held
  the site to zero third-party requests.

  On 2026-09-18 the owner decided to count page visits with Google Analytics 4, as on her
  other public sites (`docs/adr/0004-count-page-visits-with-ga4.md`). The gate
  was changed, not disabled, and it now enforces the new rule: **exactly one** script is
  allowed, the GA4 loader from `core/analytics.py`, matched by its whole text and by a digest
  pinned here, only in the `<head>` of an HTML page, and only with its privacy guards intact.
  Anything else — a second script, a different measurement ID, a loader with a guard removed,
  a font, an image, a pixel, a frame, a tracker in a feed or data file — still fails, and
  the negative controls below prove each half: the intact site passes, every sabotage fails.
  `tests/test_analytics.py` executes the loader to prove what the guards do.

* **No unreviewed record reaches the page.** The site renders the same records as the feed,
  so it inherits the same gate. A safety property that holds for `feed.xml` and not for
  `index.html` is not a safety property; it is a coincidence.

The rest is WCAG 2.2 AA structure. It is tested rather than asserted because a legal-aid
caseworker using a screen reader is precisely who this page exists for, and because "status
is signalled by colour alone" is the single easiest accessibility failure to ship by accident
and the one that would make the coverage table useless to them.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from id_churn_sentinel.core import analytics
from id_churn_sentinel.core.changes import ChangeRecord
from id_churn_sentinel.core.coverage import coverage, repo_root
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
from id_churn_sentinel.core.site import (
    PAGES_URL,
    PRIVACY_URL,
    SOCIAL_CARD_URL,
    feed_slug,
    render_privacy,
    render_site,
)
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
#
# THE RULE, as ADR 0004 left it. Until 2026-09-18: nothing on any published byte is fetched
# from a third party. From 2026-09-18, by the owner's decision: the same, except for exactly
# one script — the Google Analytics 4 loader — allowed once, in the <head> of an HTML page,
# and only as the exact text `core/analytics.py` renders and the digest below pins. Every
# check that follows runs AFTER that one permitted text is removed, so the loader cannot
# shelter anything else, and a loader that differs by one character is not removed at all and
# fails as the stray <script> it then is.

#: The only measurement ID ADR 0004 permits.
PERMITTED_MEASUREMENT_ID = "G-98S46JC943"

#: SHA-256 of the permitted loader, exactly as `analytics.head_snippet` renders it. Pinned here,
#: outside the module that renders it, so that a change to the loader — a guard dropped, a
#: parameter added, another host, another ID — cannot pass the gate by changing the page and the
#: permission in one edit. If you change the loader on purpose, re-read ADR 0004 first: a change
#: to when it loads or to what Google receives needs a new decision record, not a new digest.
PERMITTED_LOADER_SHA256 = "f59a7d207c05b86c48e3311e3380602535f9d5bab9fe9b712f75555fd038e0d7"

#: The loader's guards and settings, as the permitted text spells them. The digest already
#: pins them; this names them, so a failure says which property went rather than only that
#: bytes changed.
REQUIRED_IN_LOADER = (
    'if (w.location.hostname !== "chelseakr.github.io") return;',
    'if (w.location.pathname.indexOf("/id-churn-sentinel/") !== 0) return;',
    "if (n.globalPrivacyControl === true) return;",
    'if (dnt === "1" || dnt === "yes") return;',
    "if (optedOut()) return;",
    "allow_google_signals: false",
    "allow_ad_personalization_signals: false",
    # The two Consent Mode defaults: ad signals denied in both, analytics storage denied in
    # the listed regions and granted elsewhere.
    'ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",\n'
    '    analytics_storage: "denied", region: ["AT",',
    'ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",\n'
    '    analytics_storage: "granted"\n',
    "page_location: w.location.origin + w.location.pathname,",
    'page_referrer: ref ? ref[1] + "/" : ""',
    'KEY = "id-churn-sentinel:analytics-opt-out"',
)

#: What the loader must never touch. `location.search`, `.hash` and `.href` are where a URL
#: carries text a reader chose; the rest are how a script reads what a reader typed or sends
#: something of its own. None of them appears in the permitted text.
FORBIDDEN_IN_LOADER = (
    "location.search",
    "location.hash",
    "location.href",
    "document.cookie",
    ".value",
    "innerhtml",
    "sendbeacon",
    "xmlhttprequest",
    "fetch(",
    "new image",
    "document.write",
    "eval(",
    'gtag("event"',
    'gtag("set"',
    "user_id",
    "user_properties",
    "send_page_view",
)

#: Forbidden on the front page once the permitted loader is removed. The strictest list, and
#: unchanged by ADR 0004.
FRONT_PAGE_FORBIDDEN = (
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
    "src=",
    "srcset=",
    "poster=",
    "@font-face",
)

#: Forbidden in EVERY published artifact once the permitted loader is removed from an HTML
#: page. Unchanged by ADR 0004. A feed or data file has nothing removed from it, so the loader
#: itself fails there (as `<script` and `googletagmanager`).
ARTIFACT_FORBIDDEN = (
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
    # Nothing this project publishes is fetched from a host that is not ours, bar the one
    # permitted loader. The only http(s) URLs in the bytes are OFFICIAL SOURCES we cite and our
    # own repo — links a reader chooses to follow, never subresources a browser fetches.
    "cdn.",
    "analytics.",
)


def permitted_loader() -> str:
    """The one script ADR 0004 permits, as the committed configuration renders it."""
    return analytics.head_snippet(analytics.GA4_MEASUREMENT_ID)


def third_party_violations(name: str, content: str, forbidden: tuple[str, ...]) -> list[str]:
    """Every way one published artifact breaks the ADR 0004 rule. Empty means it holds.

    An HTML page may carry the permitted loader at most once, and only inside `<head>`; that
    exact text, and nothing else, is removed before `forbidden` is checked. Any other artifact
    has nothing removed.
    """
    problems: list[str] = []
    loader = permitted_loader()
    if name.endswith(".html") and loader:
        count = content.count(loader)
        if count > 1:
            problems.append(f"{name}: carries the permitted loader {count} times")
        elif count == 1:
            head_end = content.find("</head>")
            if head_end == -1 or content.index(loader) > head_end:
                problems.append(f"{name}: the permitted loader is outside <head>")
            content = content.replace(loader, "", 1)
    lowered = content.lower()
    problems.extend(f"{name}: carries {item!r}" for item in forbidden if item in lowered)
    return problems


@pytest.mark.feed_integrity
def test_the_permitted_loader_is_the_one_adr_0004_describes() -> None:
    """The permission itself, checked before anything is allowed through it."""
    loader = permitted_loader()
    digest = hashlib.sha256(loader.encode("utf-8")).hexdigest()
    assert digest == PERMITTED_LOADER_SHA256, (
        f"the GA4 loader changed (sha256 {digest}). The safety gate allows exactly one loader, "
        f"byte for byte. Re-read docs/adr/0004-count-page-visits-with-ga4.md: a "
        f"change to when it loads or what Google receives needs a new decision record."
    )
    assert analytics.GA4_MEASUREMENT_ID == PERMITTED_MEASUREMENT_ID
    assert set(re.findall(r"G-[A-Z0-9]{4,20}", loader)) == {PERMITTED_MEASUREMENT_ID}

    # One external address, and it is Google's tag script for this property.
    assert re.findall(r"https?://[^\s\"']+", loader) == [
        f"https://www.googletagmanager.com/gtag/js?id={PERMITTED_MEASUREMENT_ID}"
    ]
    assert loader.startswith("<script>\n") and loader.endswith("\n</script>")
    assert loader.count("<script") == 1

    for required in REQUIRED_IN_LOADER:
        assert loader.count(required) == 1, f"the loader lost {required!r}"
    lowered = loader.lower()
    for forbidden in FORBIDDEN_IN_LOADER:
        assert forbidden not in lowered, f"the loader must not use {forbidden!r}"


@pytest.mark.feed_integrity
def test_the_published_site_makes_no_third_party_requests_but_the_permitted_loader(
    tmp_path: Path, site_registry: Registry, confirmed_change: ChangeRecord
) -> None:
    """THE GATE. A page that surveils trans people while claiming to protect them would be a
    disgrace. The owner has accepted one measured exception (ADR 0004); nothing else gets in.

    So: no other script, no external stylesheet, no font, no image, no iframe, no beacon — and
    no form or input, because there is no user model in this codebase and nothing a reader
    types can reach an analytics parameter if the page has nowhere to type it.
    """
    publish([confirmed_change], tmp_path, registry=site_registry)
    page = (tmp_path / "index.html").read_text()

    assert page.count(permitted_loader()) == 1, "the front page lost its one permitted loader"
    assert third_party_violations("index.html", page, FRONT_PAGE_FORBIDDEN) == []


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
    artifact nobody remembers to add to a list is covered the day it is written. ADR 0004 did
    not change it: only an HTML page may carry the permitted loader, and every feed and data
    file must carry no tracker at all.
    """
    publish([], tmp_path, registry=load_registry())

    artifacts = sorted(tmp_path.iterdir())
    assert len(artifacts) > 100, "expected the full published surface, per-jurisdiction included"

    problems: list[str] = []
    carriers: list[str] = []
    for path in artifacts:
        content = path.read_text()
        if permitted_loader() in content:
            carriers.append(path.name)
        problems.extend(third_party_violations(path.name, content, ARTIFACT_FORBIDDEN))
    assert problems == []
    # The loader is on the two pages the decision names, and on nothing else.
    assert carriers == ["index.html", "privacy.html"]


@pytest.mark.feed_integrity
def test_the_committed_site_holds_the_rule_on_the_bytes_pages_serves() -> None:
    """The same sweep over `docs/` as committed. Pages serves the branch with no build step, so
    the committed bytes are the product, and a hand-edit would never pass through `publish()`.
    """
    published = repo_root() / "docs"
    artifacts = [
        path
        for path in sorted(published.iterdir())
        if path.is_file() and path.suffix in {".html", ".xml", ".json"}
    ]
    assert len(artifacts) > 100

    problems: list[str] = []
    for path in artifacts:
        problems.extend(
            third_party_violations(path.name, path.read_text(encoding="utf-8"), ARTIFACT_FORBIDDEN)
        )
    assert problems == []
    front = (published / "index.html").read_text(encoding="utf-8")
    assert third_party_violations("index.html", front, FRONT_PAGE_FORBIDDEN) == []


# ---- negative controls: the gate still fails on everything the rule does not permit --------


def _before_head_close(snippet: str) -> Callable[[str], str]:
    return lambda page: page.replace("</head>", f"{snippet}\n</head>", 1)


def _before_body_close(snippet: str) -> Callable[[str], str]:
    return lambda page: page.replace("</body>", f"{snippet}\n</body>", 1)


def _in_loader(old: str, new: str) -> Callable[[str], str]:
    """Edit the loader where it sits in the page, as a hand-edit or a careless change would."""

    def edit(page: str) -> str:
        loader = permitted_loader()
        assert page.count(loader) == 1 and loader.count(old) == 1, old
        return page.replace(loader, loader.replace(old, new))

    return edit


def _move_loader_to_body(page: str) -> str:
    loader = permitted_loader()
    return page.replace(loader, "", 1).replace("</body>", f"{loader}\n</body>", 1)


SABOTAGES: dict[str, Callable[[str], str]] = {
    "a web font": _before_head_close(
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">'
    ),
    "a second, external script": _before_body_close(
        '<script async src="https://cdn.example.net/widget.js"></script>'
    ),
    "a second, inline beacon": _before_body_close(
        '<script>navigator.sendBeacon("https://collect.example.net/b");</script>'
    ),
    "a tracking pixel": _before_body_close(
        '<img src="https://www.facebook.com/tr?id=1&amp;ev=PageView" alt="">'
    ),
    "an iframe": _before_body_close('<iframe title="x" src="https://example.net/"></iframe>'),
    "the loader twice": _before_body_close(analytics.head_snippet(PERMITTED_MEASUREMENT_ID)),
    "the loader in the body": _move_loader_to_body,
    "another measurement ID": _in_loader(
        f'gtag("config", "{PERMITTED_MEASUREMENT_ID}"', 'gtag("config", "G-OTHER12345"'
    ),
    "the host guard removed": _in_loader(REQUIRED_IN_LOADER[0], ""),
    "the path guard removed": _in_loader(REQUIRED_IN_LOADER[1], ""),
    "the GPC guard removed": _in_loader(REQUIRED_IN_LOADER[2], ""),
    "the DNT guard removed": _in_loader(REQUIRED_IN_LOADER[3], ""),
    "the opt-out guard removed": _in_loader(REQUIRED_IN_LOADER[4], ""),
    "Google signals on": _in_loader("allow_google_signals: false", "allow_google_signals: true"),
    "the full address sent": _in_loader(
        "w.location.origin + w.location.pathname,", "w.location.href,"
    ),
    "a custom event": _in_loader(
        "  var s = d.createElement", '  gtag("event", "view", {});\n  var s = d.createElement'
    ),
}


@pytest.mark.feed_integrity
@pytest.mark.parametrize("sabotage", sorted(SABOTAGES))
def test_the_gate_fails_on_the_front_page_when(
    sabotage: str, tmp_path: Path, site_registry: Registry
) -> None:
    """Each sabotage is applied to a freshly published front page. The control first asserts
    that the sabotage landed, then that the gate reports it; one that silently no-ops would
    otherwise read as a pass. The intact page is the positive control, and it passes."""
    publish([], tmp_path, registry=site_registry)
    page = (tmp_path / "index.html").read_text()
    assert third_party_violations("index.html", page, FRONT_PAGE_FORBIDDEN) == []
    assert third_party_violations("index.html", page, ARTIFACT_FORBIDDEN) == []

    sabotaged = SABOTAGES[sabotage](page)
    assert sabotaged != page, f"the sabotage {sabotage!r} changed nothing"

    assert third_party_violations("index.html", sabotaged, FRONT_PAGE_FORBIDDEN), sabotage
    assert third_party_violations("index.html", sabotaged, ARTIFACT_FORBIDDEN), sabotage


@pytest.mark.feed_integrity
@pytest.mark.parametrize("artifact", ["feed.xml", "changes.json", "feed-us-tx.xml", "sources.json"])
def test_the_gate_fails_when_the_permitted_loader_reaches_a_feed_or_data_file(
    artifact: str, tmp_path: Path
) -> None:
    """The permission is for HTML pages. The same bytes in a feed are a tracker in a feed."""
    publish([], tmp_path, registry=load_registry())
    content = (tmp_path / artifact).read_text()
    assert third_party_violations(artifact, content, ARTIFACT_FORBIDDEN) == []

    sabotaged = permitted_loader() + "\n" + content
    assert sabotaged.count(permitted_loader()) == 1
    problems = third_party_violations(artifact, sabotaged, ARTIFACT_FORBIDDEN)
    assert f"{artifact}: carries '<script'" in problems
    assert f"{artifact}: carries 'googletagmanager'" in problems


@pytest.mark.feed_integrity
def test_the_privacy_page_is_held_to_the_same_rule(tmp_path: Path, site_registry: Registry) -> None:
    """The second page that carries the loader gets the same sweep and the same controls."""
    publish([], tmp_path, registry=site_registry)
    page = (tmp_path / "privacy.html").read_text()
    assert page.count(permitted_loader()) == 1
    assert third_party_violations("privacy.html", page, ARTIFACT_FORBIDDEN) == []

    for name in ("a second, external script", "the GPC guard removed", "a tracking pixel"):
        sabotaged = SABOTAGES[name](page)
        assert sabotaged != page
        assert third_party_violations("privacy.html", sabotaged, ARTIFACT_FORBIDDEN), name


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
    or an **absolute https** URL (an official source we cite, or our repo). Nothing else. The
    privacy page (ADR 0004) is held to the same rule.
    """
    publish([confirmed_change], tmp_path, registry=site_registry)
    for name in ("index.html", "privacy.html"):
        page = (tmp_path / name).read_text()

        links = re.findall(r'href="([^"]+)"', page)
        assert links
        for link in links:
            if link.startswith(("https://", "#")):
                continue
            assert not link.startswith("/"), (
                f"{name}: {link!r} is root-absolute. Served from a Pages subpath it resolves "
                f"off-site and 404s — and it will look fine in every local test that serves "
                f"docs/ at a root."
            )
            assert not link.startswith("docs/"), (
                f"{name}: {link!r} repeats the publish directory. `docs/` IS the site root "
                f"once Pages serves it; this would resolve to /id-churn-sentinel/docs/…"
            )

        # And no markup on the page fetches anything — a subresource with a broken relative
        # path fails silently, which is the other half of the same deployment bug. The one
        # permitted loader (ADR 0004) is removed first: it requests Google's tag script from
        # an absolute https URL, in script, and only on the production host.
        assert "src=" not in page.replace(permitted_loader(), "")
    assert f'<link rel="canonical" href="{PRIVACY_URL}">' in (tmp_path / "privacy.html").read_text()


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

    # A card type that promises an image must actually carry one.
    card = meta("name", "twitter:card")
    assert card in {"summary", "summary_large_image"}, f"unknown twitter:card {card!r}"
    if card == "summary_large_image":
        assert meta("property", "og:image"), "summary_large_image promises an og:image"
    assert meta("property", "og:type") == "website"


def test_the_preview_card_names_an_image_that_is_actually_published(
    tmp_path: Path, site_registry: Registry
) -> None:
    """`og:image` is the one URL on this page that cannot be relative, and the one that is
    never resolved by a browser sitting on the page.

    A social crawler reads the tag out of context: there is no document base to resolve
    `social-card.png` against, so a relative value yields no card at all, silently, and the
    only symptom is a bare link in somebody else's chat window. It must be absolute AND carry
    the `/id-churn-sentinel/` subpath, for the same reason the canonical must — six project
    sites share `chelseakr.github.io`, and an origin-rooted image URL names a different one.

    And the file has to exist. This site has no build step: `docs/` is served straight off the
    branch, so "published" means "committed", and `tools/make_social_card.py` writes the card
    into the same directory as every generated artifact. A card type promising an image that
    404s renders worse than the `summary` card this page used to declare, so the assertion is
    on the committed bytes, not on the tag alone.
    """
    publish([], tmp_path, registry=site_registry)
    page = (tmp_path / "index.html").read_text()

    def meta(attribute: str, name: str) -> str | None:
        found = re.search(rf'<meta {attribute}="{name}" content="([^"]*)">', page)
        return found.group(1) if found else None

    for tag in ("og:title", "og:description", "og:image"):
        value = meta("property", tag)
        assert value, f"the page has no {tag}"

    image = meta("property", "og:image")
    assert image == SOCIAL_CARD_URL
    assert image is not None
    assert image.startswith("https://"), (
        f"{image!r} is not absolute. A crawler reads og:image with no document base and "
        f"cannot resolve a relative path, so the card silently does not render."
    )
    assert "/id-churn-sentinel/" in image, f"{image!r} omits this project's path segment"
    assert meta("name", "twitter:image") == image, "twitter:image must not drift from og:image"
    assert meta("name", "twitter:card") == "summary_large_image"
    assert meta("property", "og:image:alt"), "the card carries no alt text"

    # The bytes, on the branch that serves them. `repo_root()` rather than `tmp_path`: the card
    # is committed, not generated by `publish()`, which is exactly why nothing else checks it.
    card = repo_root() / "docs" / "social-card.png"
    assert card.exists(), (
        f"{card} is missing, but og:image points at it. Regenerate it with "
        f"`uv run --with pillow python tools/make_social_card.py` and commit the result."
    )
    assert card.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{card} is not a PNG"


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


@pytest.mark.parametrize("ga4_id", [analytics.GA4_MEASUREMENT_ID, ""])
def test_the_privacy_page_has_the_same_structure_as_the_front_page(ga4_id: str) -> None:
    """ADR 0004's privacy page is read by the same people, with the same tools."""
    page = render_privacy(ga4_id=ga4_id)

    assert '<html lang="en">' in page
    assert page.count("<h1>") == 1
    levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", page)]
    assert levels[0] == 1
    for previous, current in pairwise(levels):
        assert current <= previous + 1, f"heading level jumped from h{previous} to h{current}"
    assert '<a class="skip" href="#main">Skip to main content</a>' in page
    assert '<main id="main">' in page
    assert "<header>" in page and "<footer>" in page
    assert ":focus-visible" in page
    assert '<a href="privacy.html">Privacy</a>' in page
    assert '<a href="./">' in page


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
