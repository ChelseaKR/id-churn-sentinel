# ID Churn Sentinel — Implementation Roadmap

> **Commercial activity hold — July 14, 2026.** This roadmap preserves technical
> history and former external-validation milestones. Recruiting, interviews,
> pilots, consumer adoption, partnerships, paid support, funding, contracts, and
> commercial work are paused. Only noncommercial public-interest research,
> documentation, safety analysis, and open-source technical work may continue
> under [`COMMERCIAL-STATUS.md`](./COMMERCIAL-STATUS.md).

> **V1.0 execution note:** this file preserves the implementation history and prior milestone logic. The dated, cross-domain V1.0 delivery roadmap is [`12-ROADMAP.md`](./12-ROADMAP.md), and the release definition is [`00-V1-PLAN.md`](./00-V1-PLAN.md). Where schedule or V1 release scope differs, those two documents govern; the safety commitments in this history remain in force.

> Generic enforcement lives in `/STANDARDS`. This document carries the decisions and the project-specific values.
> **Last verified: 2026-07-13 · Recheck cadence: per registry expansion + per incumbent-consumer conversation.**

## 1. Snapshot

A change-detection service over registry-claimed US government-source candidates relevant to name and gender-marker changes on identity documents. V1 requires in-date named human source verification and shared fetch/publication eligibility before a source can support new publication. It hashes normalized page text on a polite weekly cadence, diffs changed text passages when a hash moves, routes every detected change through a **named human** before publication, and emits an RSS + JSON feed that other organizations consume. It never asserts what the law is.

The hard parts are not technical. They are: (a) resisting the enormous pull toward auto-classifying legal significance, (b) keeping the registry honest when the temptation is to claim 51-state coverage, and (c) getting an incumbent to actually subscribe.

## 2. Problem & users

- **Problem.** Existing organizations cover trans ID-document guidance and already own writing, legal review, community trust, and context. Namesake also publicly documents a daily canonical-PDF extracted-text/line-diff monitor and issue workflow, so monitoring is not an untouched category. The remaining hypothesis is the combined multi-jurisdiction verification/eligibility, heterogeneous-source evidence, run-health/gap, independent-review/correction, and public no-tracking feed contract described in [`16-RESEARCH-SOURCES.md`](./16-RESEARCH-SOURCES.md).
- **Primary users — and this is the unusual part — are institutions, not individual guidance-seekers.** A4TE, Trans Lifeline, Namesake, and legal-aid organizations may be consumers, partners, or better substitutes for parts of the system. Build/partner/buy evidence—not an assumption that they lack monitoring—decides the relationship. See [`CONSUMERS.md`](./CONSUMERS.md).
- **Secondary users.** Journalists and researchers tracking policy churn; individuals who want a raw RSS feed with no intermediary.
- **Jobs to be done.** *“Tell me which mapped source pages had reviewed observed changes this week so my editors can investigate.”* · *“For text/HTML, show me the passage that changed, not just that something did.”* · *“Never make me trust a machine's opinion about what a legal change means.”*
- **Non-goals (permanent).** Telling a person what the law is. Telling a person what to do. Auto-classifying legal significance. Replacing any incumbent. Collecting a single subscriber's identity.

## 3. Product definition

- **Vision.** The freshness layer under everyone else's guidance. Boring, cited, and correct.
- **Scope (MoSCoW).**
  - *Must:* registry-claimed government-source candidates with explicit verification and closed vocabularies; snapshot store with retained bytes; normalized-text hash detection; **unified diff of changed text passages**; human review gate; RSS + JSON feed of reviewed records only; CLI.
  - *Should:* human verification of every seeded registry entry; per-host crawl spacing; a scheduled weekly run; a review queue that is pleasant enough that a human actually works it.
  - *Could:* PDF text extraction (so form changes are diffable, not just detectable); the Federal Register JSON API instead of its search page; a static review UI; per-jurisdiction feeds so a consumer can subscribe to one state.
  - *Won't (ever):* significance classification without a human; an LLM that "summarizes what changed legally"; a subscriber list; anything that requires an account.
- **The hardest "won't".** An LLM could plausibly read a diff and label it substantive. It would be *right most of the time*, and the times it is wrong would be invisible, confident, and downstream of a legal-aid org's guidance page. `make no-auto-classification` exists to make that road closed rather than merely discouraged.

## 4. Research & evidence

- **The corrected finding.** The initial “zero machine-checkable change detection” premise did not survive first-party competitor review: Namesake already operates a material daily canonical-PDF monitoring slice. Current work therefore tests the narrower combined-contract hypothesis and must reuse, partner, buy, or contribute upstream when that is safer and cheaper than parallel implementation. See [`11-GTM-BUSINESS-MODEL.md`](./11-GTM-BUSINESS-MODEL.md) and [`16-RESEARCH-SOURCES.md`](./16-RESEARCH-SOURCES.md).
- **The prior art.** `trans-docs-navigator/scripts/source-watch.ts` and `policy-watch.ts` do content-hash drift detection over 5 jurisdictions, report only "something changed", and publish nothing. This repo takes their normalization approach and their **"a fetch failure is never drift"** discipline verbatim, and extends the idea to *what* changed and to a published artifact.
- **Registry provenance.** State entries marked `corpus-vetted` were carried from trans-docs-navigator's hand-corrected corpus (CA, IL, NY, TX, WA + federal). Entries marked `seed-unchecked` are plausible official landing pages **that no one has opened yet**, and they say so.

## 5. Experience & design

- **The reviewer is the user.** The product surface that matters is not the feed; it is the moment a human reads a diff and decides. If that takes more than a minute per change, the queue backs up, the reviewer starts rubber-stamping, and the human-in-the-loop gate becomes theatre. Hence: passage-level diffs, not page-level pings; noise suppression via normalization; and dismissal being as cheap as confirmation.
- **The feed is deliberately dull.** RSS 2.0 and a versioned JSON document. No account, no auth, no tracking, no JavaScript. A consumer wires it into their CMS in an afternoon.
- **Accessibility.** No UI surface exists today (CLI + static XML/JSON). If a review UI ships (M4), WCAG 2.2 AA is a release gate, and the diff view must be usable with a screen reader — a diff rendered only via red/green colour is an inaccessible diff.

## 6. Architecture

- **Shape.** A CLI + a SQLite snapshot store + a static feed. No server, no queue, no cloud. It runs from a cron job on one cheap box, or from a scheduled GitHub Action.
- **Zero runtime dependencies.** urllib, sqlite3, hashlib, difflib, and hand-written XML. An unattended watcher that must run for years on a solo-dev budget should not carry a dependency tree that rots faster than the law it watches.
- **The network is a seam, not a fact.** `watch()` takes a `Fetcher`. Production passes `HttpFetcher`; the tests pass a dict. The entire suite runs with **no network**.
- **Key decisions (ADRs, inline until `docs/adr/` exists).**
  1. **Human-in-the-loop classification, enforced four ways** (type signature, review method, SQL `CHECK`, CLI flag). *Rejected:* heuristic or LLM classification. The failure mode is a confident, wrong, widely-believed assertion about trans people's legal status.
  2. **Normalize before hashing; segment into passages.** *Rejected:* raw-byte hashing (reports every stylesheet re-minify) and the TS original's single-line normalization (produces a hash, but an undiffable one).
  3. **A fetch failure is never drift.** *Rejected:* treating a non-200 body as content. An outage would manufacture a policy change.
  4. **Retain the bytes, not just the hash.** *Rejected:* a hash-only baseline. A diff you cannot reproduce six months later is a claim, not evidence.
  5. **`ON CONFLICT (change_id) DO NOTHING`, never `INSERT OR IGNORE`.** SQLite's `OR IGNORE` swallows **CHECK** violations too — it would have silently discarded the very rows the human-in-the-loop constraint exists to reject, leaving the gate passing while enforcing nothing. (Caught by a test during M0. This is why the schema constraint and the Python type are *both* tested, independently.)
  6. **Fetch robots.txt ourselves.** `RobotFileParser.read()` calls `urlopen` with **no timeout**; an unattended weekly job against a government server that accepts-and-never-answers would hang forever. Every socket this tool opens is bounded.
  7. **JSON registry, not YAML.** Keeps the dependency count at zero and matches trans-docs-navigator's corpus convention.

## 7. Quality attributes & metrics

| Metric | Target | Measured by | Gate |
|---|---|---|---|
| Changes classified `substantive` with no named human | **0 (unrepresentable)** | `make no-auto-classification` (4 independent layers) | merge-blocking |
| Unreviewed/dismissed records in the published feed | **0** | `make no-unreviewed-in-feed` | merge-blocking |
| Registry entries with a malformed/non-official/duplicate URL | 0 | `make sources-validate` | merge-blocking |
| Line coverage | ≥90% (currently ~99%) | `make cov` | merge-blocking |
| Tests requiring network | **0** | injected `Fetcher`; suite runs air-gapped | structural |
| Cosmetic-churn false positives | 0 on the fixture corpus | `test_cosmetic_markup_churn_is_not_a_content_change` | merge-blocking |
| Registry entries human-verified | **`0 of 152 sources are human-verified`** — printed on every `sources validate` run, derived by `sentinel coverage`, gated in every doc | `sentinel verify` (a human) | the **number** is gated; the **work** is not automatable |
| **Sources published without their verification status alongside them** | **0 (unrepresentable)** — `publish()` cannot be called without the registry | `-m source_labelling` | merge-blocking (stage 6) |
| **Registry entries claiming `verified: true` with no named human and no date** | **0 (unloadable)** | `load_registry` raises | merge-blocking (stage 5) |
| Registry entries machine-checked (live-fetched, status + title + normalized text read) | **all 152** (2026-07-13) | `sources check` | tracked, not gated |
| Registered sources our own fetcher cannot reach | **6** — each named with its reason in the registry | `sources check` | tracked, not gated |
| **Coverage numbers in the docs that disagree with the registry** | **0 (unrepresentable)** | `sentinel coverage --check-docs` | merge-blocking (stage 5) |
| **Unwatched (state, core document class) pairs that are not a NAMED GAP** | **0 (unrepresentable)** | `sentinel coverage --check-docs` | merge-blocking (stage 5) |
| Third-party requests in the published site | **0** | `test_the_published_site_makes_no_third_party_requests` | merge-blocking (`feed_integrity`) |
| **False-drift sources in the registry** | **0** — 3 were found and removed/swapped | `sources check --twice` + the real two-run pass | tracked, not gated |
| **False positives on a real second consecutive run** | **0 / 125** (2026-07-13) | two live `watch` passes | the headline result |
| Crawl cadence | ≤1 fetch per source per week | `make watch-weekly` / `.github/workflows/watch.yml` | operational |

**The metric this repo refuses to optimize:** number of changes published. A feed that publishes more is not a better feed; it is a noisier one. The reviewer's dismissal rate is a health signal, not a failure.

## 8. Implementation plan

### M0 — Core (**shipped**)
Registry with closed vocabularies + validation; SQLite snapshot store with retention; normalization + hashing; drift detection with unified passage diffs; `ChangeRecord` + the human review gate; RSS + JSON publication; the `sentinel` CLI; 7 merge-blocking gates; 143 tests, ~99% coverage, all offline.

### M1 — Verify the registry (**the next real work, and it is not code**)
`0 of 152 sources are human-verified`. A human opens each URL, confirms it is the official page for that jurisdiction and document class, corrects it or deletes it, and flips the flag. **Nothing else in this repo is worth more than this**, and no amount of engineering substitutes for it.

**Machine-checking is done and is not the same thing.** As of 2026-07-13 every entry has been live-fetched, its status and redirect target recorded, and its `<title>`/`<h1>` read to confirm it is the office and document class it claims to be; the results are in each entry's `checked` block. That is a *machine* fact. `verified` remains a *human* fact, and nothing in this codebase may set it — which is the entire discipline, and the reason the two fields exist separately.

What machine-checking already caught, and what it says about the honest expectation: **zero of the seeded entries were 404s**, but seven of twenty-four could not be fetched at all, one was structurally unfetchable (the Federal Register entry pointed at a path that site's own robots.txt Disallows — it would have failed silently forever), and one host serves a page fine to a browser and 403s our crawler. A status code is not a verification, and a 200 is not a promise: `courts.oregon.gov` serves **soft 404s** — HTTP 200 with a body titled "404 Page Not Found" — so a status-only check would happily bless a dead URL.

**Shipped 2026-07-13 — the two things that were actually in the way.** Neither of them was "someone should get round to it":

1. **The product was making an implicit claim it had not earned, and now it does not.** A published table listing one official URL per (jurisdiction, document class) *reads* as a directory of official pages. It is a list of **candidates**. So the status now travels with the source everywhere it goes: a word on every row of the site (**UNVERIFIED — machine-checked, not human-confirmed** — never a colour, WCAG 2.2 AA); a `verification_status` field on every source in `sources.json`, in `changes.json`, and in **every per-jurisdiction feed**; a `source_verification` block on every change record; a count and a sentence in every RSS channel description; a `<category>` on every RSS item. `publish()` now **requires** the registry, so no code path can write an artifact without the thing that knows each source's status — and a merge-blocking gate (`-m source_labelling`, inside stage 6) asserts it on the published bytes.
2. **The human's job was expensive, and now it is cheap.** `sentinel verify` fetches each source and prints the jurisdiction, document class, authority, URL, the page's own `<title>` and an excerpt of its normalized text, then asks one question. It records the answer **with the verifier's name and the date**, refuses to record one without a name, writes to the registry immediately (so it is resumable — a `q` at source 90 costs nothing), and supports `--jurisdiction`, `--document-class`, `--federal-first` and `--limit` so the highest-value entries go first. `sentinel coverage` prints the burn-down. A rejection is recorded with a reason and either flagged for repair or moved to the named-gap list (reason `wrong-page`). `docs/VERIFYING.md` states the question, what *not* to judge, and the honest cost: **≈3.5 hours for all 152**, in sittings.

The registry also refuses, structurally, to be *told* it is verified: an entry with `verified: true` and no named verifier and no date **does not load**. There is no hand-edit, no bulk `sed`, and no AI agent that can quietly finish this milestone on paper — which is the correct property for the one field in this repo that is a human's word.

**Exit:** 152/152 verified, `verified: true` in the committed registry with a name and a date on each, and the README's "read this first" section deleted.

### M2 — Run it for real (**started 2026-07-13; the first real baseline exists**)

**Done:**
- **The first real baseline.** 152 sources, 6 unreachable-and-named. Committed as `sources/baseline-hashes.json` (mirroring `trans-docs-navigator/corpus/source-hashes.json`) so drift is detectable from a clean checkout with no snapshot store — previously a fresh clone had no memory and could say nothing for a week.
- **A second consecutive run produced ZERO false drift over 125 live government pages** — after three false-drift sources were found and dealt with (see M3 below). This was the point of the exercise, and it was not free.
- **The first published artifacts.** `docs/feed.xml` + `docs/changes.json`, legitimately **empty** — no change has been reviewed and confirmed by a human, so nothing is published. The empty feed parses as valid RSS 2.0 and carries an XML comment saying it is empty rather than broken, because a consumer who decides the feed is broken stops reading it, and a consumer who reads the silence as "nothing changed" has been told something we never said. **No change was manufactured to make the feed look populated.**
- **The scheduled path.** `.github/workflows/watch.yml` runs weekly, opens/updates one human-review issue on drift, and **cannot publish** (no path from CI to `sentinel review --reviewer`). Because this account has an Actions spending limit and scheduled workflows are the first thing it stops, the *primary* path is `make watch-weekly` on a cron box, and the workflow is the convenience. A monitor whose only trigger is someone else's billing system is not a monitor.

**Still open:** a reviewer working a real queue over four consecutive weeks; the measured per-week review cost and dismissal rate; re-deriving `REMOVAL_THRESHOLD` from observed outage lengths.
**Closed since:** *a real feed URL*. The published bytes are committed, so `https://raw.githubusercontent.com/ChelseaKR/id-churn-sentinel/main/docs/changes.json` serves today with nothing switched on, and the Pages URL is one repository setting away (see M4). It was blocked on an Actions-based Pages deploy that this account's billing limit would never have run.
**Exit:** four consecutive weeks of watch → review → publish, with a real feed URL, and a documented per-week review cost.

### M3 — Reduce noise, widen coverage
Driven by what M2 measures, not by guesswork.

**Shipped early, because it was a safety gap rather than a nice-to-have:**
- **Outage vs. removal.** Consecutive-failure tracking per source, persisted in `source_health`, reset on any success, escalating past `REMOVAL_THRESHOLD` (default 3) to a distinct `possibly_removed` change record that requires human review. This closes §10's open question — *"what is the right response when a source is unreachable for weeks?"* — whose old answer was "hold the baseline silently", i.e. treat a scrubbed page and a flaky server identically, forever. The escalation is never auto-classified: it hands over the literal error string and names the three readings (removed / blocked / down) without choosing.
- **The Federal Register JSON API**, replacing its HTML search page — which federalregister.gov's robots.txt Disallows, making the old entry unfetchable by construction rather than merely noisy.
- **A 45s fetch timeout**, up from 20s. `travel.state.gov` — the passport sex-marker page, the highest-value source in the registry — was measured answering in 3s, 17s and 44s on three consecutive requests, so it was intermittently reporting as unreachable on latency alone.
- **Coverage 9 → 21 jurisdictions** (24 → 59 watch targets), every URL live-fetched before it was added.

**Shipped 2026-07-13, and this one was a finding rather than a plan:**

- **Coverage 21 → 50 jurisdictions** (59 → 131 watch targets). Every URL fetched by the tool's own fetcher, with its own TLS stack, and its `<title>`/`<h1>` read before it was added. Most of the plausible-looking deep links a generator would have invented (`dmv.nv.gov/dlnamechange.htm`, `mva.maryland.gov/Pages/change-name.aspx`, and a dozen more) return 404 — which is exactly why they are not in the registry. MI and NH were absent entirely and said so; 21 further gaps were named with a reason each.
- **False-drift detection — `sentinel sources check --twice`.** The first real two-run pass produced two "changes" that were not changes: `dpbh.nv.gov` re-rolls a *"Nevada state symbol"* trivia block (state fish → state reptile) on **every single request**, and `azdot.gov/mvd` randomly samples a "frequently viewed links" widget. Both would have alerted **every week, forever**, with diffs about the desert tortoise and rest-area rules. **The normalizer cannot fix this** — the rotating text is real, visible page text, and a normalizer that guesses which visible text "doesn't count" is one that can hide a real change (RESPONSIBLE-TECH §A). So the fix is not a better normalizer, it is *not watching a page that cannot be watched honestly*, plus a command that finds them: fetch twice, compare hashes, name anything unstable. Run across the registry it caught a third — `nebraskajudicial.gov` renders its "recently adopted rules" list in non-deterministic order. NV removed (the widget is site-wide on the nv.gov CMS); AZ and NE swapped for stable pages. **The stated limit is in the code: passing `--twice` does not prove week-over-week stability** — `azdot.gov/mvd` looked perfectly stable across two back-to-back fetches and was caught by the weekly run, not by the check.
- **A corrected registry URL no longer manufactures drift.** Swapping a landing page for a deep link under the same source id used to diff page A against page B and mint a change record claiming *the source changed* — when what changed is *which page we watch*. It is now re-baselined and reported as `rebaselined`. Found while fixing the above, which is the only reason it was found at all.

**Shipped later the same day — the map is closed, and the way it was closed is the point:**

- **Coverage 50 → 52 of 52 jurisdictions** (131 → 152 sources). **MI and NH are no longer absent.** Neither was closed by defeating a block: `michigan.gov` and every `nh.gov` host still 403 our User-Agent and we still do not spoof one. They were closed by the observation that **a state usually publishes the same policy content on a second official surface** — its statutes, its administrative code, or a court's PDF form — and that surface usually answers us honestly. Michigan is watched via MCL 333.2831 (the section that expressly provides for a new birth certificate *"to show a sex designation other than that designated at birth"*), MCL 257.307, and the SCAO's **PC 51 name-change petition**; New Hampshire via RSA 5-C:87, Saf-C 1000 and RSA 547:3-i. Sixteen jurisdictions were closed this way in total (MI, NH, DE, HI, ID, KS, LA, MN, MS, MT, NC, NV, OH, RI, SC, TX), including **Nevada**, which had been *removed entirely* over the rotating state-fish widget and is now watched via NRS 440 and NAC 483 on the Legislature's site — a different system, no widget.
  **What these entries claim is narrower than it looks, and the notes say so:** a statute page is the law an agency administers, **not** the agency's own process page. Texas still publishes no statewide name-change instructions; we watch the Family Code chapter every county court applies, and we still say the process is county-level.
- **The gap list shrank from 21 holes to 12.** What remains is what cannot be closed honestly: 5 hosts that 403 our UA, 3 whose TLS chain does not verify, 2 whose robots.txt forbids us, 2 that are JavaScript shells with no text. Colorado's statutes *are* published — as **year-stamped PDFs at a frozen URL**, i.e. a source that could never drift, which is a wrong "no change" with a green light on it. Not added.
- **`--twice` is necessary and not sufficient, and this cost us three candidates.** Three pages fetched cleanly, hashed identically twice in a row, and would have drifted forever: `leg.state.fl.us` renders **today's date** into the statute page; `legislature.mi.gov`'s HTML renders a **live legislative-session ticker**; and `ecfr.gov` answers our UA with a **bot-wall page titled "Request Access" — at HTTP 200**. The last is the most dangerous artifact this project has met: a status-code check *blesses* it, it hashes perfectly stably, and we would have watched a captcha for years and called it Social Security policy. All three were caught by **reading the normalized text of a candidate before adding it**, which is now part of adding one (README guardrail 7).
- **The gaps are data, and the docs cannot lie about them.** `gaps` is a structured array with a closed vocabulary of reasons; `sentinel coverage` derives every published number from the registry; `sentinel coverage --check-docs` (merge-blocking, stage 5) fails the build when a doc disagrees, **and** when any (state, core document class) pair is neither watched nor a named gap. It mirrors `gate-count` in trans-docs-navigator. **It found two silent holes the day it was written** — DC and RI were each missing an entire document class, and *neither appeared in the hand-written gap paragraph*. They were not decisions; they were omissions wearing the costume of decisions. It also caught the registry's own header claiming a source count of 132 when the file held 131.

  **A maintainer's note, because this gate bites its author first — twice.** The gated grammar (`N sources`, `N of 52 jurisdictions`, `N named gaps`) is reserved for **live claims about what we watch now**. Narrating history must avoid it: *"the gap list shrank from 21 holes to 12"* passes, and the same sentence written in the gated grammar does not. That is the correct behaviour, not an irritation. A reader skimming a sentence written in the gated grammar cannot tell whether it is a current claim or a memoir; neither can the gate; so the two get different grammar, and the one that matters is the one that is checked. (The gate first red-lighted this very paragraph, which had quoted the old number as an example. It was right to.)
- **The feed became consumable** (M5's actual prerequisite, and the reason it moved up): a published accessible site, a documented versioned JSON schema, per-jurisdiction feeds, and a source inventory. See M4 below.

**Still open:** PDF text extraction so `us-ssa-ss5-form` and the Michigan sources produce a diff rather than "the bytes changed"; re-deriving `REMOVAL_THRESHOLD` from real M2 outage data rather than the current educated guess; an automated slow-rotation detector (reading a candidate's text catches it *at registration*, but a page that starts rotating later is still only caught by a reviewer's repeated `editorial` dismissals). (Per-host crawl spacing landed in the fetcher: consecutive page requests to one host are held a minimum interval apart, structurally, so no call path can burst a government server.)
**Exit:** a dismissal rate a reviewer can live with, and ≥25 jurisdictions verified.

### M4 — Make it consumable, then make review pleasant

**Shipped (2026-07-13) — the consumable surface.** The thesis of `docs/CONSUMERS.md` is that the customers are the incumbents; the embarrassing fact was that **they could not actually consume anything.** The artifacts lived in a git repo with no URL that served them, the source inventory existed only as a file a Python program parses, and the gaps were a paragraph. So:

- **A published static site** (`docs/index.html`) — what is watched, **what is not and why**, the reviewed-change log, and the endpoints. WCAG 2.2 AA: semantic landmarks, one `<h1>`, real heading order, `<caption>` + `<th scope>` on every table, a skip link, visible focus, and **status signalled by a word rather than a colour** — a coverage table that only renders as red and green is useless to the caseworker most likely to be reading it with a screen reader. **No JavaScript, no external stylesheet, no font, no image, no third-party request of any kind**, asserted on the published bytes by a merge-blocking test: every external request is a request that tells a third party who is reading about trans ID law.
- **A documented, versioned JSON schema** (now `docs/schema/changes-v2.schema.json`; major 1 remains as the pre-correction compatibility contract) so an integrator can build without reading our source — with a merge-blocking test that the schema and the code agree about every field and enum, and that real published output validates. A schema that has drifted from its implementation is worse than none, because someone built against it.
- **Per-jurisdiction feeds** (`feed-us-tx.xml`, `changes-us-tx.json`) for every jurisdiction, published **whether or not they have items yet**. A URL that only appears the day of the emergency is a URL nobody is subscribed to. This closes the §10 open question below — and the answer to *"or does a consumer just filter changes.json?"* turned out to be: telling a name-change clinic to filter JSON is telling them to write code before they can read their own state.
- **An inventory** (`sources.json`) carrying the sources **and the gaps in the same document**, because an inventory that lists only what we cover invites a reader to infer that the rest is fine.
- **No account, no email, no tracking — as a tested property**, not a policy statement.

**Shipped (2026-07-13, later the same day) — and it is the one that made the four bullets above *true* rather than merely built.** Everything M4 shipped was consumable in principle and unreachable in practice: **there was no URL that served any of it.** The artifacts sat in `dist/`, and `dist/` is the one directory GitHub Pages cannot serve — branch-based Pages takes `/` or `/docs`, and nothing else. The Actions-based deploy that *could* have served `dist/` was in the repo, looked plausible, and **could never run**: this account has an account-wide Actions spending limit. A publishing pipeline that depends on somebody else's billing system is not a publishing pipeline, and a `docs/CONSUMERS.md` whose every example read `https://<host>/changes.json` was a consumer guide with the consumption removed.

- **The published surface moved to `docs/`**, alongside the prose docs, which is the only layout branch-based Pages will serve. `.nojekyll` ships with it — without it Pages runs Jekyll over the output, and Jekyll **silently drops** anything whose name starts with an underscore. `docs/README.md` says which files are generated and which are written by a human.
- **The Pages workflow was deleted, not fixed.** It could not be fixed; it could only be made to look like it worked. The site is served **from the branch** (Settings → Pages → *Deploy from a branch* → `main` / `/docs`), which needs no CI and no billing.
- **`raw.githubusercontent.com` is a first-class consumption path, documented as such**, and it needs *nothing* switched on — the published bytes are committed, so the raw host serves every artifact off `main` today. Both bases now appear in `docs/CONSUMERS.md` as copy-pasteable `curl` lines, with the RSS polling story, the per-jurisdiction slugs, and the schema URL.
- **Two new gates, both learned from what nearly shipped.** `test_every_link_on_the_page_is_subpath_safe` — Pages serves the site under `/<repo-name>/`, so a root-absolute `href="/feed.xml"` points *off-site*, and it looks perfectly correct in any local server run at a root. `test_the_committed_published_feed_holds_the_safety_property` — with no CI in the loop, **the committed bytes are the served bytes**, so the "no unreviewed record reaches a consumer" gate now runs against the committed `changes.json` and not only against freshly generated output. `make serve` reproduces the subpath locally, because a test you can only run in production is not a test.

**Still open:** a static, accessible *review* UI so working the queue does not require a terminal. WCAG 2.2 AA as a release gate.
**Exit:** a non-engineer can review a change.

### M5 — Consumers
The point of the whole thing. Approach A4TE, Trans Lifeline, Namesake, and legal-aid orgs with a working feed and the offer in `docs/CONSUMERS.md`. Success is not "they praise it"; success is **one org wiring the feed into their content workflow**. The prerequisite that was quietly missing until M4 shipped: **there was nothing they could have wired.**
**Exit:** ≥1 incumbent consuming `changes.json` in production.

## 9. Risks

| Risk | Response |
|---|---|
| **A wrong "no change"** — we miss a real change and someone relies on stale guidance | The primary safety risk. Watch official pages *and* keep the noisy Federal Register source; never claim the feed is exhaustive; state the limit in the README and in the feed's own `disclaimer` field. Policy that moves by internal directive, with no web-page edit, is invisible to this tool and always will be. |
| Auto-classification creeps in ("just a heuristic to pre-sort the queue") | `make no-auto-classification`, 4 enforcement layers. A pre-sort heuristic that suggests `substantive` is a classification wearing a hat. |
| Reviewer burnout → rubber-stamping | Noise suppression is a *safety* feature, not a polish feature. Measure dismissal rate in M2. If review is a chore, the gate is theatre. |
| The registry silently rots (a URL 301s to a marketing page) | `sources check`; a change record on a redirect is itself the signal. Never auto-update a registry URL. |
| Nobody consumes it | The honest failure mode. Mitigated by M5 being the *point*, not an afterthought — and by keeping the feed trivially cheap to consume (no account, plain RSS/JSON). |
| Crawling irritates a state IT department into blocking us | Weekly cadence, descriptive UA with a contact URL, robots.txt honoured, bounded timeouts, bounded body size. A block is a fetch failure, which is never drift — so being blocked degrades the tool without corrupting it. |

## 10. Open questions

- Who reviews when the maintainer is unavailable? A single-reviewer system has a bus factor of one, and the human gate is load-bearing. (Multi-reviewer sign-off is a real M4 question.)
- Should `significance: substantive` require *two* humans? For a change that will propagate into legal-aid guidance, possibly yes.
- ~~Is a per-jurisdiction feed worth the complexity, or does a consumer just filter `changes.json`?~~ **Answered and shipped (M4).** "Just filter it" is a sentence written by someone who has an engineer. The lowest-effort, highest-value integration in `docs/CONSUMERS.md` is *a legal-aid clinic putting an RSS feed in a Slack channel*, and that clinic has no engineer — so `feed-us-tx.xml` exists, for every jurisdiction, whether or not it has items yet. The complexity turned out to be about thirty lines.
- Does the site need a "last successful watch run" timestamp? Today the site says when it was *generated*, which is not the same claim — a consumer could read a fresh `generated_at` as evidence that the watch ran and found nothing, and that is a wrong "no change" with a friendly face on it. The honest fix is to publish the last watch pass's own timestamp and outcome. **Not shipped, and it is the next thing that should be.**
- ~~What is the right response when a source is unreachable for *weeks*?~~ **Answered and shipped (M3).** It now counts consecutive failures, persists the streak, and escalates past a threshold to a `possibly_removed` record a human must review — rather than holding the baseline in silence and treating a scrubbed page as an indefinite outage. The residual question is not *whether* but *how long*: `REMOVAL_THRESHOLD = 3` is an educated guess, and it is the wrong kind of number to defend rather than measure. Too low and routine outages train the reviewer to close escalations unread (worse than nothing — it *looks* like someone is watching); too high and a deleted page stays quiet for months. M2 should re-derive it from observed outage lengths against real government hosts.
- Should the `possibly_removed` threshold be per-source? `ssa.gov` has 403'd us continuously since before this tool existed; `travel.state.gov` is merely slow. Treating them with one global number means the chronic blocks will escalate once, be dismissed once, and then sit dismissed — which is *tolerable*, but it is not the same as *right*.
