"""Google Analytics 4 on the two HTML pages, and nowhere else (docs/adr/0004).

**This module exists because the owner changed a rule, and it is the whole of the change.**
Until 2026-09-18 the published site loaded nothing from any third party, on the reasoning in
`docs/RESPONSIBLE-TECH-AUDITS.md` §C: anyone reading about trans ID law is, with high
probability, a trans person or someone working with trans people, and a list of them is a
targeting artifact. On 2026-09-18 the owner decided to count page visits here with GA4, as on
her other public sites. ADR 0004 records that decision, the risk it accepts, and the limits
below, which are the price of it:

* **Two pages only.** `index.html` and `privacy.html` carry the loader. The feeds, the JSON
  files and every per-jurisdiction artifact never do: those are what a clinic subscribes to,
  and fetching one runs no script. The merge-blocking sweep in `tests/test_site.py` holds that
  on every published byte.
* **Production only.** The loader returns before doing anything unless the page is served
  from :data:`GA4_HOST` under :data:`GA4_PATH`. `docs/` is committed and served as it stands,
  so this is a run-time guard: `make serve`, a `file://` preview, the test suite and CI never
  contact Google.
* **Refusable.** It returns, loading nothing, under Global Privacy Control, under Do Not Track
  (`navigator.doNotTrack`, `window.doNotTrack` or `navigator.msDoNotTrack` set to "1" or
  "yes"), or after the footer's "Opt out of analytics". No `dataLayer`, no request, no cookie.
* **No advertising.** Consent Mode v2 defaults deny `ad_storage`, `ad_user_data` and
  `ad_personalization` everywhere, and deny `analytics_storage` in the EEA, the UK and
  Switzerland (via `region`); there is no banner, so nothing ever grants them. Google signals
  and ad personalisation are off in the `config` call.
* **The address and nothing else about it.** `page_location` is the origin plus the path:
  the query string and the fragment are never sent. `page_referrer` is the referring site's
  origin only. This site has no form field, sets no `user_id`, and sends no custom event, so
  nothing a reader types or anything that identifies them is ever a parameter.

The measurement ID is public (every page that loads GA hands it to the browser), so it is
configuration committed here, not a secret. Empty means no analytics at all: no script, no
reference to Google, no opt-out control, and footer and privacy copy that say so. A malformed
ID raises instead of shipping a broken tag. Any change to the loader text changes the digest
the safety gate pins, which is deliberate: a new loader needs a new decision record.
"""

from __future__ import annotations

import json
import re
from typing import Final

__all__ = [
    "ANALYTICS_DENIED_REGIONS",
    "DECISION_RECORD",
    "GA4_DATA_RETENTION",
    "GA4_HOST",
    "GA4_MEASUREMENT_ID",
    "GA4_OPT_OUT_KEY",
    "GA4_PATH",
    "GTAG_JS_URL",
    "footer_note",
    "head_snippet",
    "measurement_id",
]

DECISION_RECORD: Final = "docs/adr/0004-count-page-visits-with-ga4.md"
"""The decision record the privacy page links to, as a repository path."""

GA4_MEASUREMENT_ID: Final = "G-98S46JC943"
"""The chelseakr.github.io/id-churn-sentinel web stream of GA4 property 554857497.

Empty ("") means no analytics on any page."""

GA4_DATA_RETENTION: Final = "14 months"
"""What the privacy page says about retention. It must match the property's Admin > Data
retention setting, which was provisioned at 14 months."""

GA4_HOST: Final = "chelseakr.github.io"
GA4_PATH: Final = "/id-churn-sentinel/"
"""Where the loader may run. GitHub Pages serves this project under a path of an origin its
sibling projects share, so the host alone is not enough."""

GA4_OPT_OUT_KEY: Final = "id-churn-sentinel:analytics-opt-out"
"""The footer's opt-out, remembered per browser in localStorage.

Every project under chelseakr.github.io shares one origin and so one localStorage. A generic
key would opt a reader out of every sibling site at once, or opt them out of this one because
of a choice made on another, so this key names the project. Renaming it would silently opt
every opted-out reader back in: never rename it."""

MEASUREMENT_ID_RE: Final = re.compile(r"G-[A-Z0-9]{4,20}")
"""GA4 web-stream measurement IDs. Checked strictly: the value goes into an inline script."""

EU_MEMBER_STATES: Final = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
)  # fmt: skip
ANALYTICS_DENIED_REGIONS: Final = (*EU_MEMBER_STATES, "IS", "LI", "NO", "GB", "CH")
"""Where `analytics_storage` defaults to denied (ISO 3166-1 alpha-2): the 27 EU member
states, the other three EEA states (Iceland, Liechtenstein, Norway), the UK and Switzerland."""

GTAG_JS_URL: Final = "https://www.googletagmanager.com/gtag/js"

OPT_OUT_MESSAGES: Final = {
    "__MSG_OPTED_OUT__": (
        "Opted out. From the next page you open, this site will not load Google Analytics "
        "in this browser."
    ),
    "__MSG_IS_OUT__": (
        "You have opted out: this site does not load Google Analytics in this browser."
    ),
    "__MSG_BACK_IN__": "Opted back in. Page counts resume from the next page you open.",
    "__MSG_SIGNAL__": (
        "Google Analytics is off: your browser sends Global Privacy Control or Do Not Track."
    ),
    "__MSG_NO_STORAGE__": (
        "This browser is blocking site storage, so an opt-out cannot be remembered here. "
        "Global Privacy Control or Do Not Track keeps Google Analytics off."
    ),
}
"""The footer control's status line after each change, announced by its role="status"."""

# The loader. `page_location` is built from `origin` and `pathname` and from nothing else on
# `location`: the query string and the fragment are the two places a URL can carry text a
# reader chose, and neither is read. `page_referrer` keeps the scheme and host of
# `document.referrer` and drops its path and query.
_TEMPLATE: Final = r"""<script>
(function () {
  var w = window, n = navigator, d = document, KEY = __OPT_OUT_KEY__, OFF = __GA_DISABLE__;
  var store = null;
  try { store = w.localStorage; store.getItem(KEY); } catch (e) { store = null; }
  function optedOut() {
    try { return !!store && store.getItem(KEY) === "1"; } catch (e) { return false; }
  }
  var dnt = n.doNotTrack || w.doNotTrack || n.msDoNotTrack;
  var signal = n.globalPrivacyControl === true || dnt === "1" || dnt === "yes";
  d.addEventListener("DOMContentLoaded", function () {
    var box = d.querySelector("[data-analytics-choice]");
    if (!box) return;
    var button = box.querySelector("button"), status = box.querySelector("[role=status]");
    function render(message) {
      button.textContent = optedOut() ? "Opt back in" : "Opt out of analytics";
      button.hidden = signal || !store;
      status.textContent = message;
      box.hidden = false;
    }
    button.addEventListener("click", function () {
      try {
        if (optedOut()) {
          store.removeItem(KEY);
          w[OFF] = false;
          render(__MSG_BACK_IN__);
        } else {
          store.setItem(KEY, "1");
          w[OFF] = true;
          render(__MSG_OPTED_OUT__);
        }
      } catch (e) {
        store = null;
        render(__MSG_NO_STORAGE__);
      }
    });
    render(signal ? __MSG_SIGNAL__ : !store ? __MSG_NO_STORAGE__
      : optedOut() ? __MSG_IS_OUT__ : "");
  });
  if (w.location.hostname !== __HOST__) return;
  if (w.location.pathname.indexOf(__PATH__) !== 0) return;
  if (n.globalPrivacyControl === true) return;
  if (dnt === "1" || dnt === "yes") return;
  if (optedOut()) return;
  var ref = /^(https?:\/\/[^\/?#]+)/.exec(d.referrer || "");
  w.dataLayer = w.dataLayer || [];
  function gtag() { w.dataLayer.push(arguments); }
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "denied", region: __DENIED_REGIONS__
  });
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "granted"
  });
  gtag("js", new Date());
  gtag("config", __ID__, {
    allow_google_signals: false, allow_ad_personalization_signals: false,
    page_location: w.location.origin + w.location.pathname,
    page_referrer: ref ? ref[1] + "/" : ""
  });
  var s = d.createElement("script");
  s.async = true;
  s.src = __GTAG_SRC__;
  d.head.appendChild(s);
})();
</script>"""


def measurement_id(value: str | None) -> str | None:
    """None for an unset or blank ID, the ID itself when well formed.

    Anything else raises rather than being written into a script: a typo should stop the
    publish, not ship a broken tag.
    """
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not MEASUREMENT_ID_RE.fullmatch(value):
        raise ValueError(f"not a GA4 measurement ID (expected G-XXXXXXXXXX): {value!r}")
    return value


def head_snippet(ga4_id: str | None) -> str:
    """The loader for one page's `<head>`, or "" when no ID is set."""
    mid = measurement_id(ga4_id)
    if mid is None:
        return ""
    replacements = {
        "__OPT_OUT_KEY__": json.dumps(GA4_OPT_OUT_KEY),
        "__GA_DISABLE__": json.dumps(f"ga-disable-{mid}"),
        "__HOST__": json.dumps(GA4_HOST),
        "__PATH__": json.dumps(GA4_PATH),
        "__DENIED_REGIONS__": json.dumps(list(ANALYTICS_DENIED_REGIONS)),
        "__ID__": json.dumps(mid),
        "__GTAG_SRC__": json.dumps(f"{GTAG_JS_URL}?id={mid}"),
        **{token: json.dumps(message) for token, message in OPT_OUT_MESSAGES.items()},
    }
    snippet = _TEMPLATE
    for token, replacement in replacements.items():
        snippet = snippet.replace(token, replacement)
    return snippet


def footer_note(ga4_id: str | None) -> str:
    """The footer's privacy line, true for the build it is in.

    It deliberately avoids the word the strict front-page gate forbids ("cookie"): the privacy
    page is where cookies are described, and the footer links to it.
    """
    if measurement_id(ga4_id) is None:
        return (
            '<p class="privacy-note">These pages run no analytics and load nothing from any '
            'third party. <a href="privacy.html">Privacy</a>.</p>'
        )
    return (
        '<p class="privacy-note">These two web pages count visits with Google Analytics 4. '
        "Advertising features are off, and the address it receives never includes anything "
        "after a ? or #. Google Analytics does not load when your browser sends Global "
        "Privacy Control or Do Not Track. The feeds and data files carry no tracking. "
        '<a href="privacy.html">Privacy</a>.\n'
        '<span data-analytics-choice hidden><button type="button" class="link-button">'
        'Opt out of analytics</button> <span role="status"></span></span></p>'
    )
