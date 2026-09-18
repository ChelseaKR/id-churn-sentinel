"""Google Analytics 4 on the two HTML pages (ADR 0004), and the guards around it.

On 2026-09-18 the owner decided to count page visits on this site with GA4, as on her other
public sites. `docs/RESPONSIBLE-TECH-AUDITS.md` §C calls a list of people reading about trans
ID law a targeting risk, and ADR 0004 records that the decision accepts some of that risk in
exchange for limits. This module holds the loader to those limits by running it.

**The build.** No measurement ID means no analytics on either page: no script, no reference to
Google, no opt-out control, and copy that says so. A malformed ID fails. With the committed ID,
both committed pages carry the loader once, in `<head>`, with the footer control and a link to
the privacy page, and no feed or data file carries any of it.

**The loader, executed.** The script is lifted out of each committed page and run in Node
against a stubbed `window`, `navigator`, `document` and `localStorage`. Off the production host
or outside this project's path, under Global Privacy Control, under any Do Not Track spelling,
or after the footer opt-out, it creates no `dataLayer` and requests nothing. Otherwise it sets
both Consent Mode defaults before `config`, turns Google signals and ad personalization off,
and appends gtag.js once.

**Nothing a reader typed, and nothing that identifies them.** A page address carrying a name,
an email address, a date of birth and a search term in its query string and fragment, arriving
from a search page, is sent as the bare origin and path, with a referrer cut to its origin; the
only commands are the two consent defaults, `js` and one `config` with four known keys, so no
event, no `set`, and no `user_id` carries anything else.

**Negative controls.** Each guard is removed from the script in turn, and each scrub is undone
or bypassed. Every control first asserts that the edit landed (the original text occurred
exactly once, its one copy was replaced, and the new text is there), then asserts that the
harness now sees the load the guard exists to stop or the leak the scrub exists to stop. A
control whose sabotage silently no-ops would otherwise read as a pass.

The whole module is in the merge-blocking `feed_integrity` gate (`make no-unreviewed-in-feed`)
beside the third-party sweep in `tests/test_site.py`: that one allows exactly this loader, and
this one proves the loader does what the decision says. Node is on every GitHub-hosted runner.
Locally the executed tests skip when it is missing; under CI they fail instead, so a runner
without Node cannot turn them into a green skip.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from id_churn_sentinel.core import analytics
from id_churn_sentinel.core.coverage import coverage, repo_root
from id_churn_sentinel.core.registry import Registry, load_registry
from id_churn_sentinel.core.site import render_privacy, render_site

pytestmark = pytest.mark.feed_integrity

DOCS: Final = repo_root() / "docs"
INDEX: Final = DOCS / "index.html"
PRIVACY: Final = DOCS / "privacy.html"
PAGES: Final = (INDEX, PRIVACY)

ID: Final = "G-98S46JC943"
KEY: Final = "id-churn-sentinel:analytics-opt-out"
GTAG_SRC: Final = f"https://www.googletagmanager.com/gtag/js?id={ID}"
DENIED_REGIONS: Final = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO", "GB", "CH",
]  # fmt: skip

NOW: Final = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
ORIGIN: Final = "https://chelseakr.github.io"
PRODUCTION: Final = {"hostname": "chelseakr.github.io", "pathname": "/id-churn-sentinel/"}

GUARDS: Final = {
    "host": 'if (w.location.hostname !== "chelseakr.github.io") return;',
    "path": 'if (w.location.pathname.indexOf("/id-churn-sentinel/") !== 0) return;',
    "gpc": "if (n.globalPrivacyControl === true) return;",
    "dnt": 'if (dnt === "1" || dnt === "yes") return;',
    "opt-out": "if (optedOut()) return;",
}
"""The guards, exactly as the committed script spells them. The negative controls delete
each one, and the occurrence count is how they prove the deletion landed."""

CONFIG_KEYS: Final = {
    "allow_google_signals",
    "allow_ad_personalization_signals",
    "page_location",
    "page_referrer",
}
"""Every parameter the loader may send. Anything else in `config` is a change to what Google
receives, which ADR 0004 says needs a new decision."""

HARNESS: Final = r"""
const vm = require("vm");
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};
for (const sc of input.scenarios) {
  const appended = [];
  const listeners = {};
  const store = Object.assign({}, sc.storage || {});
  const status = { textContent: "" };
  const button = {
    hidden: false, textContent: "Opt out of analytics", handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
  };
  const box = {
    hidden: true,
    querySelector(sel) {
      return sel === "button" ? button : sel === "[role=status]" ? status : null;
    },
  };
  const document = {
    head: { appendChild(el) { appended.push(el); } },
    referrer: sc.referrer || "",
    createElement(tag) { return { tag: tag }; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    querySelector(sel) { return sel === "[data-analytics-choice]" ? box : null; },
  };
  const protocol = sc.protocol || "https:";
  const origin = protocol === "file:" ? "null" : protocol + "//" + sc.hostname;
  const search = sc.search || "";
  const hash = sc.hash || "";
  const window = {
    location: {
      hostname: sc.hostname, pathname: sc.pathname, protocol: protocol, origin: origin,
      search: search, hash: hash, href: origin + sc.pathname + search + hash,
    },
    doNotTrack: sc.windowDnt,
  };
  if (sc.storageThrows) {
    Object.defineProperty(window, "localStorage", {
      get() { throw new Error("SecurityError"); },
    });
  } else {
    window.localStorage = {
      getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
      setItem(k, v) { store[k] = String(v); },
      removeItem(k) { delete store[k]; },
    };
  }
  const navigator = {
    globalPrivacyControl: sc.gpc, doNotTrack: sc.dnt, msDoNotTrack: sc.msDnt,
  };
  const ctx = vm.createContext({
    window: window, navigator: navigator, document: document, Date: Date,
  });
  vm.runInContext(input.script, ctx);
  (listeners.DOMContentLoaded || []).forEach((fn) => fn());
  const control = [{
    label: button.textContent, hidden: button.hidden, boxHidden: box.hidden,
    status: status.textContent,
  }];
  for (let i = 0; i < (sc.clicks || 0); i++) {
    button.handlers.click();
    control.push({
      label: button.textContent, hidden: button.hidden, status: status.textContent,
      flag: Object.prototype.hasOwnProperty.call(store, input.key) ? store[input.key] : null,
      disabled: window["ga-disable-" + input.id],
    });
  }
  out[sc.name] = {
    dataLayer: window.dataLayer === undefined ? null
      : window.dataLayer.map(
        (args) => Array.from(args).map((a) => (a instanceof Date ? "<date>" : a)),
      ),
    scripts: appended.map((el) => ({ tag: el.tag, src: el.src, async: el.async })),
    control: control,
  };
}
process.stdout.write(JSON.stringify(out));
"""

Scenario = dict[str, Any]
Result = dict[str, Any]


def loader(page: Path) -> str:
    """The inline script in a committed page's `<head>`, exactly as published."""
    # Plain string search, not a regex: the page is our own generated HTML, and the gate in
    # tests/test_site.py already fails on any `<script` beyond the one permitted loader.
    head = page.read_text(encoding="utf-8").split("</head>", 1)[0]
    count = head.lower().count("<script")
    assert count == 1, f"{page.name}: expected one inline script, found {count}"
    start = head.index("<script>") + len("<script>")
    return head[start : head.index("</script>", start)]


def run(script: str, scenarios: list[Scenario], tmp_path: Path) -> dict[str, Result]:
    """Execute the loader once per scenario and report what it did."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node is required in CI to execute the GA4 loader")
        pytest.skip("node is not installed; CI runs these")
    harness = tmp_path / "ga4-harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    payload = json.dumps({"script": script, "scenarios": scenarios, "key": KEY, "id": ID})
    done = subprocess.run(  # noqa: S603 -- fixed argv, no shell, input is this test's own JSON
        [node, str(harness)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, f"node harness failed: {done.stderr}"
    results: dict[str, Result] = json.loads(done.stdout)
    assert set(results) == {s["name"] for s in scenarios}
    return results


def scenario(name: str, **overrides: object) -> Scenario:
    return {"name": name, **PRODUCTION, **overrides}


def loads_ga(result: Result) -> bool:
    return result["dataLayer"] is not None or bool(result["scripts"])


# A reader's own words, the way they could reach an address: a name, an email address, a date
# of birth and a search typed into another site, in the query string and the fragment of this
# page's address and in the referrer.
TYPED: Final = ("Jane", "Doe", "jane.doe", "example.net", "1990-02-03", "texas", "marker")
NOISY: Final = scenario(
    "noisy",
    search="?name=Jane%20Doe&email=jane.doe%40example.net&dob=1990-02-03&q=texas+marker",
    hash="#sources-TX-Jane",
    referrer="https://www.search.example/results?q=texas+gender+marker+jane.doe",
)


def user_text_leaks(result: Result) -> list[str]:
    """Everything wrong with what the NOISY visit handed to Google. Empty means nothing
    typed and nothing identifying left the page."""
    problems: list[str] = []
    layer = result["dataLayer"] or []
    sent = json.dumps(layer)
    problems.extend(f"sent {word!r}" for word in TYPED if word.lower() in sent.lower())
    problems.extend(f"sent a {mark!r}" for mark in ("?", "#") if mark in sent)
    commands = [args[0] for args in layer]
    if commands != ["consent", "consent", "js", "config"]:
        problems.append(f"sent commands {commands}")
    configs = [args for args in layer if args[0] == "config"]
    if configs:
        params = configs[0][2]
        if set(params) != CONFIG_KEYS:
            problems.append(f"config carries {sorted(params)}")
        if params.get("page_location") != f"{ORIGIN}/id-churn-sentinel/":
            problems.append(f"page_location is {params.get('page_location')!r}")
        if params.get("page_referrer") != "https://www.search.example/":
            problems.append(f"page_referrer is {params.get('page_referrer')!r}")
    return problems


# --- the build ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_no_id_means_no_analytics_on_either_page(empty: str | None) -> None:
    registry: Registry = load_registry()
    pages = {
        "index": render_site(
            registry,
            coverage(registry),
            (),
            generated_at=NOW,
            ga4_id=empty,
        ),
        "privacy": render_privacy(ga4_id=empty),
    }
    for name, page in pages.items():
        assert "<script" not in page, name
        assert "googletagmanager" not in page, name
        assert "Google Analytics" not in page, name
        assert "Opt out of analytics" not in page, name
        assert "data-analytics-choice" not in page, name
        assert "run no analytics" in page, name


def test_a_malformed_id_fails() -> None:
    for bad in ("UA-12345-1", "G-abc123", 'G-ABC"};alert(1);//', "G-", "98S46JC943"):
        with pytest.raises(ValueError, match="not a GA4 measurement ID"):
            analytics.head_snippet(bad)
    assert analytics.measurement_id(f"  {ID} ") == ID


def test_each_committed_page_carries_one_loader_with_the_committed_id() -> None:
    assert analytics.GA4_MEASUREMENT_ID == ID
    for page in PAGES:
        source = page.read_text(encoding="utf-8")
        body = source.split("</head>", 1)[1]
        assert source.count("<script") == 1, page.name
        assert "<script" not in body, page.name
        script = loader(page)
        assert json.dumps(ID) in script
        assert json.dumps(GTAG_SRC) in script
        assert json.dumps(KEY) in script
        for guard in GUARDS.values():
            assert script.count(guard) == 1, (page.name, guard)


def test_each_committed_page_links_the_privacy_page_and_the_opt_out() -> None:
    for page in PAGES:
        footer = page.read_text(encoding="utf-8").split("<footer>", 1)[1]
        assert '<a href="privacy.html">Privacy</a>' in footer, page.name
        assert "Google Analytics 4" in footer, page.name
        assert "The feeds and data files carry no tracking." in footer, page.name
        assert (
            '<span data-analytics-choice hidden><button type="button" class="link-button">'
            'Opt out of analytics</button> <span role="status"></span></span>'
        ) in footer, page.name


def test_no_feed_or_data_file_carries_any_of_it() -> None:
    """The feeds are what a clinic subscribes to. They load nothing and name nothing."""
    files = [p for p in DOCS.iterdir() if p.is_file() and p.suffix in {".xml", ".json"}]
    assert len(files) > 100
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert ID not in text, path.name
        assert "googletagmanager" not in text, path.name
        assert "Google Analytics" not in text, path.name


def test_the_privacy_page_makes_no_root_relative_reference() -> None:
    """Same reason as the front page: /x resolves against the shared origin, not this site."""
    source = PRIVACY.read_text(encoding="utf-8")
    assert re.findall(r'(?:href|src|content)="(/(?!/)[^"]*)"', source) == []
    assert (
        '<link rel="canonical" href="https://chelseakr.github.io/id-churn-sentinel/privacy.html">'
    ) in source


@pytest.mark.parametrize(
    "claim",
    [
        "Google Analytics 4",
        "cut down to the site and the path",
        "cut down to its origin",
        "Anything you type.",
        "no custom\nevents",
        "<code>_ga</code>",
        f"<code>_ga_{ID.removeprefix('G-')}</code>",
        "European Economic Area, the United Kingdom and Switzerland",
        "cookieless pings",
        "Google signals and ad personalization are both turned off",
        "14 months",
        "Global Privacy Control",
        "Do Not Track",
        "&ldquo;Opt out of analytics&rdquo;",
        "&ldquo;Opt back in&rdquo;",
        f"<code>{KEY}</code>",
        "https://tools.google.com/dlpage/gaoptout",
        "The feeds and data files are not tracked.",
        "GitHub's access logs record the IP address of every request",
    ],
)
def test_the_privacy_page_describes_what_ships(claim: str) -> None:
    text = PRIVACY.read_text(encoding="utf-8").replace("\n", " ")
    assert claim.replace("\n", " ") in text
    assert "run no analytics" not in text
    assert analytics.GA4_DATA_RETENTION == "14 months"


def test_the_privacy_page_links_the_decision_record_that_exists() -> None:
    record = repo_root() / analytics.DECISION_RECORD
    assert record.is_file(), f"{analytics.DECISION_RECORD} is missing"
    link = f"https://github.com/ChelseaKR/id-churn-sentinel/blob/main/{analytics.DECISION_RECORD}"
    assert f'<a href="{link}">' in PRIVACY.read_text(encoding="utf-8")


def test_the_opt_out_key_names_this_project() -> None:
    """Every chelseakr.github.io project shares one localStorage, so the key names this one."""
    assert analytics.GA4_OPT_OUT_KEY == KEY
    assert KEY.startswith("id-churn-sentinel:")


# --- the loader, executed ------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_on_the_production_page_it_loads_with_the_right_config(page: Path, tmp_path: Path) -> None:
    path = "/id-churn-sentinel/" + ("" if page == INDEX else page.name)
    result = run(loader(page), [scenario("live", pathname=path)], tmp_path)["live"]
    assert result["scripts"] == [{"tag": "script", "src": GTAG_SRC, "async": True}]
    ads_denied = {
        "ad_storage": "denied",
        "ad_user_data": "denied",
        "ad_personalization": "denied",
    }
    assert result["dataLayer"] == [
        [
            "consent",
            "default",
            {**ads_denied, "analytics_storage": "denied", "region": DENIED_REGIONS},
        ],
        ["consent", "default", {**ads_denied, "analytics_storage": "granted"}],
        ["js", "<date>"],
        [
            "config",
            ID,
            {
                "allow_google_signals": False,
                "allow_ad_personalization_signals": False,
                "page_location": f"{ORIGIN}{path}",
                "page_referrer": "",
            },
        ],
    ]
    assert len(DENIED_REGIONS) == 32


def test_it_sends_no_typed_text_and_no_identifier(tmp_path: Path) -> None:
    """THE extra rule for this audience (ADR 0004): the address without its query string or
    fragment, the referrer without its path or query, and nothing else."""
    for page in PAGES:
        result = run(loader(page), [NOISY], tmp_path)["noisy"]
        assert loads_ga(result), page.name
        assert user_text_leaks(result) == [], page.name


def test_the_referrer_is_cut_to_an_origin_or_dropped(tmp_path: Path) -> None:
    results = run(
        loader(INDEX),
        [
            scenario("sibling", referrer="https://chelseakr.github.io/another-project/x?y=1#z"),
            scenario("same-site", referrer=f"{ORIGIN}/id-churn-sentinel/privacy.html"),
            scenario("app", referrer="android-app://com.example.reader/"),
            scenario("none"),
        ],
        tmp_path,
    )
    sent = {name: result["dataLayer"][3][2]["page_referrer"] for name, result in results.items()}
    assert sent == {
        "sibling": f"{ORIGIN}/",
        "same-site": f"{ORIGIN}/",
        "app": "",
        "none": "",
    }


def test_off_the_production_host_or_path_it_loads_nothing(tmp_path: Path) -> None:
    cases = [
        scenario("localhost", hostname="localhost", protocol="http:"),
        scenario("loopback", hostname="127.0.0.1", protocol="http:"),
        scenario("file", hostname="", pathname="/Users/x/docs/index.html", protocol="file:"),
        scenario("other-host", hostname="example.com"),
        scenario("raw-mirror", hostname="raw.githubusercontent.com"),
        scenario("sibling-project", pathname="/chalkline/"),
        scenario("origin-root", pathname="/"),
        scenario("prefix-lookalike", pathname="/id-churn-sentinel-fork/"),
    ]
    for name, result in run(loader(INDEX), cases, tmp_path).items():
        assert not loads_ga(result), (name, result)


def test_a_privacy_signal_or_the_opt_out_loads_nothing(tmp_path: Path) -> None:
    cases = [
        scenario("gpc", gpc=True),
        scenario("dnt-navigator", dnt="1"),
        scenario("dnt-yes", dnt="yes"),
        scenario("dnt-window", windowDnt="1"),
        scenario("dnt-ms", msDnt="1"),
        scenario("opted-out", storage={KEY: "1"}),
    ]
    for page in PAGES:
        for name, result in run(loader(page), cases, tmp_path).items():
            assert not loads_ga(result), (page.name, name, result)


def test_an_unrelated_or_false_signal_still_loads(tmp_path: Path) -> None:
    """GPC false, DNT "0", or a sibling site's opt-out key do not switch this one off."""
    cases = [
        scenario("gpc-false", gpc=False),
        scenario("dnt-zero", dnt="0"),
        scenario("sibling-opt-out", storage={"chalkline:analytics-opt-out": "1"}),
        scenario("opt-out-not-1", storage={KEY: "0"}),
        scenario("storage-blocked", storageThrows=True),
        scenario("privacy-page", pathname="/id-churn-sentinel/privacy.html"),
    ]
    for name, result in run(loader(INDEX), cases, tmp_path).items():
        assert loads_ga(result), (name, result)


def test_the_footer_control_opts_out_and_back_in(tmp_path: Path) -> None:
    control = run(loader(INDEX), [scenario("toggle", clicks=2)], tmp_path)["toggle"]["control"]
    assert control[0]["label"] == "Opt out of analytics"
    assert control[0]["hidden"] is False
    assert control[0]["boxHidden"] is False
    assert control[1]["label"] == "Opt back in"
    assert control[1]["flag"] == "1"
    assert control[1]["disabled"] is True
    assert "Opted out" in control[1]["status"]
    assert control[2]["label"] == "Opt out of analytics"
    assert control[2]["flag"] is None
    assert control[2]["disabled"] is False
    assert "Opted back in" in control[2]["status"]


def test_the_footer_control_reports_a_signal_or_an_earlier_opt_out(tmp_path: Path) -> None:
    results = run(
        loader(INDEX),
        [
            scenario("gpc", gpc=True),
            scenario("was-out", storage={KEY: "1"}),
            scenario("no-storage", storageThrows=True),
        ],
        tmp_path,
    )
    assert results["gpc"]["control"][0]["hidden"] is True
    assert "Global Privacy Control" in results["gpc"]["control"][0]["status"]
    assert results["was-out"]["control"][0]["label"] == "Opt back in"
    assert "You have opted out" in results["was-out"]["control"][0]["status"]
    assert results["no-storage"]["control"][0]["hidden"] is True
    assert "blocking site storage" in results["no-storage"]["control"][0]["status"]


# --- negative controls ---------------------------------------------------------------------

TRIGGERS: Final = {
    "host": scenario("host", hostname="localhost", protocol="http:"),
    "path": scenario("path", pathname="/chalkline/"),
    "gpc": scenario("gpc", gpc=True),
    "dnt": scenario("dnt", dnt="1"),
    "opt-out": scenario("opt-out", storage={KEY: "1"}),
}

SCRUBS: Final = {
    "the full address": ("w.location.origin + w.location.pathname,", "w.location.href,"),
    "the full referrer": ('page_referrer: ref ? ref[1] + "/" : ""', "page_referrer: d.referrer"),
    "a typed-text event": (
        "  var s = d.createElement",
        '  gtag("event", "search", { search_term: w.location.search });\n  var s = d.createElement',
    ),
    "a user_id": (
        "allow_google_signals: false,",
        "allow_google_signals: false, user_id: w.location.hash,",
    ),
}
"""Edits that would each make the loader send something ADR 0004 says it never sends."""


def _sabotage(original: str, old: str, new: str) -> str:
    assert original.count(old) == 1, f"{old!r} is not in the script exactly once"
    sabotaged = original.replace(old, new)
    # The sabotage landed: the one original occurrence is gone (a replacement that keeps the
    # text as a prefix keeps only that copy), the new text is there, and nothing else moved.
    assert sabotaged != original
    assert sabotaged.count(old) == new.count(old)
    assert not new or new in sabotaged
    assert len(sabotaged) - len(original) == len(new) - len(old)
    return sabotaged


def test_every_guard_has_a_control() -> None:
    assert set(TRIGGERS) == set(GUARDS)


def test_the_intact_script_holds_every_trigger(tmp_path: Path) -> None:
    for name, result in run(loader(INDEX), list(TRIGGERS.values()), tmp_path).items():
        assert not loads_ga(result), f"the intact script loaded GA for {name}"


@pytest.mark.parametrize("guard", sorted(GUARDS))
def test_each_guard_is_what_stops_ga(guard: str, tmp_path: Path) -> None:
    sabotaged = _sabotage(loader(INDEX), GUARDS[guard], "")
    result = run(sabotaged, [TRIGGERS[guard]], tmp_path)[guard]
    assert loads_ga(result), f"removing the {guard} guard changed nothing"


@pytest.mark.parametrize("scrub", sorted(SCRUBS))
def test_the_leak_check_sees_each_way_typed_text_could_leave(scrub: str, tmp_path: Path) -> None:
    old, new = SCRUBS[scrub]
    sabotaged = _sabotage(loader(INDEX), old, new)
    result = run(sabotaged, [NOISY], tmp_path)["noisy"]
    assert loads_ga(result)
    assert user_text_leaks(result), f"with {scrub} the leak check saw nothing"
