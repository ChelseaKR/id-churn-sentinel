# ID Churn Sentinel

**Cited, machine-checkable change detection for US transgender identity-document law and process.** It watches registry-claimed government-source candidates — state vital records, DMVs, courts, State Department, SSA, the Federal Register — on a polite weekly cadence. For HTML and plain text, it hashes normalized text and produces the **passage that changed**; for PDFs and other binary sources, the current alpha reports only that the bytes changed. A named human reviews every detected change before it is published. The tool reports an observation about a URL; it never asserts what the law is or that an unverified URL is authoritative.

**Status:** `In build` (M0 shipped; first real baseline 2026-07-13: **152 sources, 52 of 52 jurisdictions**, zero false drift on a second consecutive pass) · **Track:** Civic / trans infrastructure · **License:** [MIT](./LICENSE) · **Runtime deps:** zero (stdlib only)

## V1.0 delivery plan

The repository now has an execution-ready V1.0 plan that treats source verification, community governance, reviewer operations, consumer adoption, reliability, accessibility, and release safety as product work—not post-launch cleanup. Start with [`docs/00-V1-PLAN.md`](./docs/00-V1-PLAN.md); the complete planning set is indexed there.

**V1.0 means:** every active source is human-verified; high-impact publication has independent review; two design partners complete a six-week shadow pilot; the feed contract and correction path are stable; eight weekly watch→review→publish cycles satisfy the pre-release evidence rule; and every must-pass gate in [`docs/15-V1-RELEASE-CHECKLIST.md`](./docs/15-V1-RELEASE-CHECKLIST.md) has an owner and dated receipt. It does **not** mean the service interprets law, gives advice, or guarantees it observed every policy change.

## Read this before you rely on anything here

**The source registry is not human-verified. `0 of 152 sources are human-verified`, and the published site says so next to every single entry.**

**Current alpha limits:** PDF and other binary changes do not yet have extracted-text or passage diffs; the SQLite store keeps only the newest **five** snapshots per source and prunes older ones; and neither durable long-term reproducibility nor full WCAG 2.2 AA conformance is a current claim. Both are V1 release gates. The current publisher carries source-verification labels but does not yet withhold a reviewed change solely because its source is unverified, due for recheck, or fetch-policy-ineligible; V1 must block all three before publication.

Every URL in the registry was fetched by this tool's own crawler and had its title read. **That is a machine fact about a socket, not a person confirming the page is the right page**, and the difference is the entire reason this section is at the top rather than in an appendix. A reader who sees a table row saying *"OH · Birth certificate · Ohio Department of Health · <url>"* will reasonably read it as **"this is Ohio's official birth-certificate page"** — and nobody has checked that it is. If it is wrong, that implicit claim sends a trans person to the wrong office on a day they took off work.

Machine-checking cannot close that gap, and this repo has the receipts: `courts.oregon.gov` serves a **soft 404 — HTTP 200 with a body titled "404 Page Not Found"**, and `ecfr.gov` answers our crawler with a **bot-wall titled "Request Access", also at HTTP 200**. A status code blesses both. A title check blesses the second. Only a person opening the page catches either.

So, plainly:

| | |
|---|---|
| **What this tool claims** | *The fetched content at this URL changed* · *for text/HTML, this is the passage that changed* · *here are the before/after hashes; the supporting bytes remain reproducible only while their snapshots are among the newest five in the current alpha* |
| **What it never claims** | *What the law is* · *what a change means legally* · *that an unverified URL is the right page* |
| **What a consumer may rely on** | The machine observation, named change-review receipt, explicit source-verification status, and honesty of the gaps list—not source authority unless `source_verification.status` is `verified` and in date |
| **What a consumer may NOT rely on** | The registry as a directory of official pages. It is a list of **candidates**. Every artifact carries a machine-readable `verification_status` per source; today every one of them reads `unverified` |

The fix is not a disclaimer, it is the work: **`sentinel verify`** is the review aid that makes it cheap — it fetches each source, shows a human the page's own title and an excerpt of its text, asks one question, and records the answer **with the verifier's name and the date**. It refuses to record a verification without a name, and the registry will not even *load* a `verified: true` entry that has no human behind it. See [`docs/VERIFYING.md`](./docs/VERIFYING.md) — it is about three and a half hours of work for all 152, and it is the most valuable three and a half hours anyone could spend on this repo.

## The result that matters

A sentinel that cries wolf gets muted, and a muted sentinel is worse than none — so the first thing worth reporting is not the coverage number, it is what happened when the tool was pointed at the real internet twice in a row.

**Run 1** baselined 125 reachable government-hosted candidates. **Run 2, minutes later, reported 123 unchanged and 2 changed.** Both "changes" were false:

| Source | What "changed" |
|---|---|
| `dpbh.nv.gov` (NV birth certificates) | a rotating *"Nevada state symbol"* trivia block in the footer: **state fish → state reptile**. It re-rolls on **every single request** — three back-to-back fetches gave three different hashes. |
| `azdot.gov/mvd` (AZ driver's license) | a randomly-sampled *"frequently viewed links"* widget: `rest area rules` → `penalties`. |

Neither is markup churn, so **the normalizer could not have caught them** — that is real, visible page text. And "normalize harder" is the wrong instinct: a normalizer that guesses which visible text doesn't count is one that can *hide a real change*, which is the one failure this repo will not trade for tidiness.

So: Nevada's page was **removed** from the registry (the widget is site-wide across the nv.gov CMS — there is no stable Nevada vital-records page to substitute) and recorded as a named GAP. Arizona was **swapped** for a deeper page carrying the same content and no widget. Then the tool got a new command — **`sentinel sources check --twice`** — which fetches every source twice and names anything that hashes differently. Run across the whole registry it caught **a third**: `nebraskajudicial.gov` renders its "recently adopted rules" list in *non-deterministic order*, so the same rules come back shuffled. Swapped for the stable self-help page.

**Re-baselined and re-run: 125 unchanged, 0 changed, 0 false positives.** The three sources that would have alerted every week forever are gone, and they are the finding — a monitor that had shipped without this pass would have been ignored inside a month.

**And `--twice` is not enough, which is the second finding.** It catches a widget that re-rolls on every *request*. It cannot catch a page that rotates on a *slower* cycle — and three candidate sources fetched cleanly, hashed identically twice in a row, and would have drifted forever anyway:

| Candidate | What it renders into the page |
|---|---|
| `leg.state.fl.us` (FL name-change statute) | **today's date**. A change record every day, whose diff is the date. |
| `legislature.mi.gov` (MI statutes, HTML view) | a **live session ticker** — *"Senate adjourned until Wednesday, July 15, 2026 10:00 AM"*. |
| `ecfr.gov` (the federal SSA regulations) | a bot-wall page titled **"Request Access" — served with HTTP 200**. |

The last one is the nastiest thing in this repo. A status-code check *blesses* it, it hashes perfectly stably, and we would have watched a captcha for years and called it Social Security policy. None of the three is in the registry: they were caught by **reading the normalized text of every candidate before adding it**, which is now part of adding one.

## Closing the map without lying to get there

Michigan and New Hampshire used to be **absent entirely**: `michigan.gov` and every `nh.gov` host serve a browser and return **403 to our descriptive User-Agent**, and `courts.michigan.gov` normalizes to *zero passages*. There is a two-line change that "fixes" that — send a Chrome User-Agent — and a tool that lies about who it is, to a government server, on behalf of a population under surveillance, has not earned the trust it is asking for. So the gap stood.

The honest fix is that **a state often publishes related policy content on a second government-hosted surface**, and that surface may answer us. These substitutes remain registry claims—not authoritative citations—until a named human verifies them:

| Was | Now watched instead |
|---|---|
| `michigan.gov` (403) · `courts.michigan.gov` (SPA, 0 passages) | the **Michigan Compiled Laws** section governing a new birth certificate *"to show a sex designation other than that designated at birth"* (MCL 333.2831), the licence-application statute, and the SCAO's **PC 51 name-change petition** — the form a Michigan name change actually runs on |
| every `nh.gov` host (403) | **RSA 5-C:87** (amending a birth record), **Saf-C 1000** (the DMV's own licensing rules), **RSA 547:3-i** (probate-court name change) |
| `odh.ohio.gov` (404s its own site root — a WAF wearing a 404's clothes) | **OAC 3701-5**, the vital-statistics rules ODH itself administers |
| `sccourts.org` (`robots.txt` disallows us, site-wide) | the **S.C. Code** name-change chapter, whose robots.txt permits us |
| `dmv.nv.gov` (JavaScript shell) · `dpbh.nv.gov` (rotating state-fish widget — *removed* in the pass above) | **NAC 483** and **NRS 440** on the Legislature's site, which carries no widget |
| `dps.ms.gov` (unverifiable TLS chain) | the **Driver Service Bureau host** the registry claims as the same authority, with a TLS chain our fetcher verifies |

**Sixteen jurisdictions were closed this way, and the two absent ones are gone**: MI, NH, DE, HI, ID, KS, LA, MN, MS, MT, NC, NV, OH, RI, SC, TX. Coverage is now **52 of 52 jurisdictions**.

Note precisely what these entries claim, because it is less than it looks: **a statute page is the law an agency administers, not the agency's own process page.** Every one of them says so in its notes. Watching Texas's Family Code Chapter 45 does not mean we watch a Texas county's filing process — Texas publishes no statewide name-change instructions at all, and we still say so.

And what did *not* get fixed is the more interesting half. **12 named gaps remain**, and they are named rather than closed because the only ways to close them are the ways we will not use — 5 hosts that 403 our UA, 3 whose TLS chain does not verify, 2 whose robots.txt forbids us, 2 that are JavaScript shells with no text to hash. Colorado's statutes *are* published, but only as **year-stamped PDFs at a frozen URL**: a source that can never drift is worse than no source, because it is a wrong "no change" with a green light on it.

## Why it matters

Three organizations already document how to change your name and gender marker on an ID in the United States. All three cover the ground. **All three say, in their own words, that they cannot keep up with how fast it moves.**

- **Advocates for Trans Equality (A4TE)** — the [ID Documents Center](https://transequality.org/documents) covers all 50 states, DC, 5 territories, and 5 federal document classes. It also says, verbatim: *"Due to the ever-changing nature of state laws and policies, we are working to keep the ID Documents Center as up to date as possible. If you see something that needs updating, please contact us."* Their freshness mechanism is **a contact form**.
- **Trans Lifeline** — the [ID Change Library](https://translifeline.org/resources/id-change-library/) has been maintained by volunteers since 2016. It is self-acknowledged incomplete (entries are literally flagged *"Help Us Find It"*), publishes no API or export, and carries **no last-updated dates at all**.
- **Namesake** ([namesake.fyi](https://namesake.fyi/)) — genuinely well-engineered and open-source. Its repository already documents a **daily upstream-PDF monitor**: PDFs with a `canonicalUrl` are fetched, their extracted text is compared with the local copy, changed lines are diffed, and a scheduled workflow opens or updates an issue. That is a material substitute for the PDF-monitoring slice of this product, not an organization waiting for someone else to invent monitoring.

**Coverage is not the gap. Freshness is.** Namesake proves that upstream PDF freshness monitoring already exists in this space. The narrower whitespace hypothesis is the **combined contract**: a multi-jurisdiction registry with dated human source verification, shared fetch/publication eligibility, text evidence across heterogeneous government surfaces, named gaps and run health, independent review/correction, and a public no-reader-tracking feed. The dated scan found no purpose-built offering publicly combining all of those elements; that is a hypothesis to keep testing, not proof that no private or emerging competitor exists. Incumbent guides and tools already have the writers, legal review, community trust, context, and sometimes their own monitors; this product should integrate with or extend those workflows where its combined contract adds value.

This repo is an attempt to supply the remaining combined layer. **Its intended consumers are institutions—not individual guidance-seekers.** A4TE, Trans Lifeline, Namesake, and legal-aid orgs may consume, partner on, or provide the better substitute for parts of the feed. The correct outcome may be integration or upstream contribution rather than a parallel monitor. See [`docs/CONSUMERS.md`](./docs/CONSUMERS.md).

Why this is worth building carefully: **a wrong "no change" is a safety failure.** Someone reads guidance that a monitor silently failed to flag as stale, drives to a DMV with the wrong documents, and loses a day of work, a filing fee, or — in the wrong state on the wrong day — considerably more. That asymmetry drives every design decision below.

## What it does

- **Watches government-source candidates with explicit verification state.** A committed registry (`sources/registry.json`) of https government URLs, keyed by jurisdiction (50 states + DC + a `US` federal bucket) and document class (birth certificate, driver's license, court-order name change, passport, Social Security, Selective Service). Each entry names the authority the registry claims and ships **`verified: false`** — the registry is *seeded*, and only a human who has actually opened the URL may flip that flag. Nothing in the codebase decides it, and **the registry will not load an entry claiming `verified: true` without a named verifier and a date attached** — so the flag cannot be flipped by a hurried maintainer, a `sed`, or an AI agent asked to make the file look finished. `sentinel verify` is the one writer, and it refuses to record a verification without a name.
- **Says "unverified" out loud, on every source, in every artifact.** The published site marks each source **UNVERIFIED — machine-checked, not human-confirmed** as a word rather than relying on colour or an icon; `sources.json`, `changes.json` and every per-jurisdiction feed carry a machine-readable `verification_status` on every source and on every change record's source; the RSS channel states the count and each item carries the status as a `<category>`. A merge-blocking gate asserts on the published bytes that no source appears anywhere without it. **This status labelling is tested; full WCAG 2.2 AA audit and remediation remain a V1 gate.**
- **Refuses to watch a page it cannot watch honestly.** Some government-hosted pages re-roll a rotating widget on every single request — `dpbh.nv.gov` renders a *"Nevada state symbol"* trivia block (state fish → state reptile) into its footer, and hashes differently every time it is asked. A page like that would report a change **every week, forever**, and its diff would be a fact about the desert tortoise. The normalizer cannot save us: that rotating text is real, visible page text, and a normalizer that guesses which visible text "doesn't count" is a normalizer that can *hide a real change*. So the answer is not to normalize harder — it is to not watch the page, to say so in the registry's GAP list, and to ship the diagnostic that finds them: `sentinel sources check --twice`.
- **Tells you what changed for text/HTML, and labels the binary limitation.** On text or HTML drift it computes a unified diff of the **normalized text** and hands the reviewer the changed passages. For a PDF or other binary source, the alpha reports only that its bytes changed and directs the reviewer to compare the retained snapshot; extracted-text passage diffs are a V1 gate.
- **Ignores markup churn.** Government pages churn a rotated CSRF token, a re-minified stylesheet, and an `&nbsp;` far more often than they churn text. Normalization strips script/style/comments/tags and resolves entities before hashing, so a cosmetic re-deploy does not wake anyone up. (A watcher that cries wolf gets muted, and a muted watcher is worse than none.)
- **Treats an outage as an outage — but does not treat a *disappearance* as an outage.** **A fetch failure is never drift.** A 503, a WAF block, a timeout: the previous hash is held, no snapshot is written, no content change is recorded. A state's website falling over is not a state changing its policy. But a page that has been *taken down* used to look exactly like a brief outage — forever — and the tool answered that silence with silence. It now counts *consecutive* failures per source, and after a threshold escalates to a distinct `possibly_removed` record that a human must review. A government page about trans identity documents vanishing is itself a policy signal; it is never auto-classified as one.
- **Keeps a bounded recent evidence window.** The current SQLite snapshot store retains the newest five fetches per source — raw bytes, normalized text, sha256, timestamp, HTTP status — and prunes older snapshots. That supports immediate review and recent-diff reproduction, not a months-long archive. Pinning published evidence and proving long-term reproduction are V1 release gates.
- **Commits the baseline, so a clean clone has a memory.** The snapshot store is not committed (it is megabytes of government HTML and grows weekly), which used to mean a fresh checkout knew nothing: every source is a first sighting, a first sighting is a baseline rather than drift, and the tool could say nothing at all until it had watched for a week. `sources/baseline-hashes.json` — 125 hashes, mirroring `trans-docs-navigator/corpus/source-hashes.json` — closes that: `sentinel baseline check` answers *"which of these pages is not what it was?"* from a fresh clone, with no store. It is honest about its limit, too: it holds the hash, not the text, so it can tell you a page moved but not what moved. `sentinel watch` does that.
- **Requires a human before it says anything.** Every detected change is born `unclassified` / `unreviewed`. A person classifies it (`editorial` | `substantive`) and confirms or dismisses it. Only confirmed, classified, human-signed records reach the feed.
- **Publishes something an incumbent can actually consume, with no account.** A static site (`docs/index.html`), RSS (`feed.xml`), a versioned JSON feed against a [published schema](./docs/schema/changes-v1.schema.json) (`changes.json`), an inventory of every watched source *and every named gap* (`sources.json`), and **one feed per jurisdiction** (`feed-us-tx.xml`, `changes-us-tx.json`) so a clinic that serves one state is not made to consume all 52. It is **fetchable right now, with nothing switched on** — see [Consuming it](#consuming-it) — because the published bytes are committed rather than built by a CI job that this account's billing limit would never run. No auth, no email capture, no tracking, no third-party request anywhere in the published bytes — asserted on the published output by a merge-blocking test. A subscriber list for a trans-ID-law feed is a list of trans people; the safest way to protect that list is to never create one.
- **Cannot lie about its own coverage.** Every number in this README — sources, jurisdictions, gaps, unreachable — is *derived from the registry* by `sentinel coverage`, and `sentinel coverage --check-docs` fails the build if any doc disagrees, or if a jurisdiction/document-class pair is neither watched nor a **named gap**. A project whose pitch is *"we tell you what went stale"* cannot have a stale front page. It found two silent holes on the day it was written (see below).

## Prior art this builds on

`trans-docs-navigator/scripts/source-watch.ts` and `policy-watch.ts` already do content-hash drift detection, and this repo's normalization approach and its "a fetch failure is never drift" discipline are lifted directly from them. What they don't do — and what this repo exists to do — is cover more than the five jurisdictions that repo carries, say *what* changed rather than *that* something did, or publish anything at all.

## Quickstart

```sh
make install                       # uv sync (Python 3.12+; zero runtime deps)
make verify                        # the full 7-gate merge pipeline
uv run sentinel sources validate   # the registry gate
uv run sentinel coverage           # the coverage numbers + the verification burn-down, DERIVED
uv run sentinel coverage --check-docs  # …and the gate that fails if a doc disagrees
uv run sentinel sources check      # live-fetch every URL (network) — liveness only
uv run sentinel sources check --twice  # find false-drift sources BEFORE they reach a reviewer
uv run sentinel verify --verifier "Your Name" --federal-first   # THE VERIFICATION QUEUE (network)
uv run sentinel verify --list      # what is still unverified. No network, no writes.
uv run sentinel baseline check     # what moved since the committed baseline (needs no store)
uv run sentinel watch              # fetch, normalize, hash, diff, record drift
uv run sentinel diff <change-id>   # the changed passages
uv run sentinel review <change-id> --reviewer "Your Name" --significance substantive --status confirmed
uv run sentinel publish --out docs/    # site + RSS + JSON + per-jurisdiction feeds + inventory
make serve                            # serve the site the way Pages does — under a SUBPATH
```

**Two different humans, two different commands, and they are not interchangeable.** `verify` is a judgment about a **source** (*"this URL is the official page for this document class in this jurisdiction"*). `review` is a judgment about a **change** (*"this diff matters"*). Both refuse to run without a name. Neither can be done by a machine, and nothing in this codebase tries.

## Consuming it

**No account, no key, no email, nothing to switch on.** The published bytes are **committed**, so `raw.githubusercontent.com` serves every artifact straight off `main` — that is a legitimate consumption path, not a workaround, and it works today:

```sh
BASE=https://raw.githubusercontent.com/ChelseaKR/id-churn-sentinel/main/docs

curl -s "$BASE/changes.json"       | jq '.changes[] | select(.significance=="substantive")'
curl -s "$BASE/changes-us-tx.json" | jq '.changes[]'   # just Texas — not all 52
curl -s "$BASE/feed-us-tx.xml"                         # …or the same, as RSS, in Slack
curl -s "$BASE/sources.json"       | jq '.gaps[]'      # what we do NOT watch, and why
```

Once GitHub Pages is enabled (*Settings → Pages → Deploy from a branch → `main` / `/docs`*), the identical paths are served at **`https://chelseakr.github.io/id-churn-sentinel/`**. The base URL is the only thing that changes.

**Why it is served from a branch and not from CI.** This account has an **account-wide GitHub Actions spending limit**, so an Actions-driven Pages deploy would never run — the workflow that used to do it has been deleted rather than left in the repo pretending. Branch-based Pages serves exactly two source paths, `/` or `/docs`, which is why the published site lives in **`docs/`** alongside the prose docs. A feed that only exists once somebody else's billing system agrees to run a job is a feed that does not exist.

The full integrator guide — the field meanings, the review states, the versioning promise, and what this tool will never tell you — is [`docs/CONSUMERS.md`](./docs/CONSUMERS.md). The layout of the published directory is [`docs/README.md`](./docs/README.md).

**The weekly run.** `make watch-weekly` is the operational job, and `.github/workflows/watch.yml` is the same thing on a cron. The workflow opens or updates a single human-review issue when a source moves and **cannot publish** — publication requires `sentinel review --reviewer`, a named human, and there is no path from CI to that command. Note that this repo's owner has an account-wide GitHub Actions spending limit, so **do not assume the hosted workflow ever runs**: the Makefile target is the primary path and the workflow is the convenience. A monitor whose only trigger is someone else's billing system is not a monitor.

## For Claude Code

- **Build entrypoint:** [`docs/ROADMAP.md`](./docs/ROADMAP.md) → *Implementation Plan* (M0–M5).
- **Hard guardrails:**
  1. **Never auto-classify legal significance.** The tool observes that bytes changed. A human decides what it means. This is enforced in four independent places (the detector has no vocabulary to classify; `reviewed_by` refuses an unnamed reviewer; a SQL `CHECK` constraint rejects a classified row with no reviewer; the CLI requires `--reviewer`) and gated by `make no-auto-classification`. A machine announcing *"Texas substantively changed its gender-marker policy"* on the strength of a sha256 comparison will be believed, and will sometimes be wrong.
  2. **Unreviewed drift never reaches the feed.** Gated by `make no-unreviewed-in-feed`. An item in the published feed propagates outward into advice real people act on.
  3. **A fetch failure is never drift.** Carry the old hash forward. An outage is not a content change. (But a *long* silence is escalated for human review rather than held forever — see `possibly_removed` in `core/detect.py`. Silence is not a safe default when a page may have been scrubbed.)
  4. **Never fabricate a source.** The registry holds government-domain candidates with explicit verification status; it does not call an unverified candidate authoritative. It is better to cover 21 jurisdictions honestly than to seed 300 URLs nobody has opened. If a source's robots.txt forbids us, or its TLS cannot be verified, the answer is to remove it and say so — not to route around it.
  5. **The feed requires no account, ever.** No auth, no email, no tracking, no analytics.
  6. **Do not assert what the law says.** Not in the feed, not in the docs, not in a helpful summary field. That job belongs to A4TE, Trans Lifeline, Namesake, and lawyers — and doing it badly is the harm.
  7. **Never add a source without running `sentinel sources check --twice` on it — and then reading its normalized text.** A page that re-rolls a widget on every request is a permanent false alarm, and one of those costs more trust than ten missing jurisdictions. Three were caught by `--twice`. Three *more* passed `--twice` cleanly and were caught only by reading the text (a live date, a session ticker, and a bot-wall served with HTTP 200). Both steps, every time.
  8. **Never hand-write a coverage number.** `sentinel coverage` derives them; `--check-docs` gates them. If a number in a doc is wrong, the fix is to run the command — never to edit the registry until the prose comes true.
  9. **Never flip `verified: true`. Not for any reason.** It is a named human's judgment that they opened a government page and confirmed it is the right one. An agent cannot have that judgment, and an entry marked verified by a machine is strictly worse than one marked unverified, because it will be believed. The registry enforces it (no `verified: true` loads without a named verifier and a date), and the honest way to help is to make the human's job cheaper — which is what `sentinel verify` and [`docs/VERIFYING.md`](./docs/VERIFYING.md) are.
- **Commands:** `make verify` · `make coverage` · `make watch-weekly` · `make publish` · `make serve` · `make sources-check` · `make sources-stability` · `make baseline-check` · `make verify-sources` (the human queue).
- **The published site is `docs/`, and it is served from the branch, not from CI.** `make publish` writes it; `make serve` renders it under the `/id-churn-sentinel/` subpath Pages actually uses. Never hand-edit a generated file in `docs/`, and never use a root-absolute link (`/feed.xml`) in the site — under a Pages subpath it silently points off-site. Both are gated.
- **Definition of done (M1):** every registry entry human-verified; a real weekly watch pass running; at least one reviewed change published to a feed an incumbent has actually subscribed to; all 7 gates green.

## Gates

`make verify` runs seven merge-blocking stages:

| # | Gate | What it holds |
|---|------|---------------|
| 1 | `lint` | ruff (correctness, bandit security, import hygiene, no bare TODOs) |
| 2 | `type` | `mypy --strict` |
| 3 | `cov` | pytest, coverage floor **90%** |
| 4 | `security` | `pip-audit` |
| 5 | `sources-validate` | every registry entry: well-formed https government-domain candidate URL + known jurisdiction + known document class + claimed authority + unique id + no duplicate watch target + **no `verified: true` without a named human and a date** — **and** `coverage --check-docs`: every coverage number in every doc is re-derived from the registry (including *how many sources a human has verified*), and every unwatched (jurisdiction, document class) pair is a **named gap** |
| 6 | `no-unreviewed-in-feed` | **safety** — unreviewed or dismissed drift cannot be published, **and no source can appear in any published artifact without its verification status rendered alongside it** (`-m "feed_integrity or source_labelling"`) |
| 7 | `no-auto-classification` | **safety** — nothing is classified `substantive` without a named human |

Gates 6 and 7 are not code-quality checks. They are the safety properties this tool exists to hold. If either goes red, the correct response is to stop, not to weaken the test.

**Gate 6 holds two properties, and the second is new.** *Unreviewed drift never reaches a consumer* — and *a source never reaches a consumer stripped of the fact that no human has confirmed it*. They are the same discipline pointed at two different implicit claims: "a machine noticed this, so it must matter" and "this URL is in your list, so it must be the right page." Both are claims the tool would be making by omission, and neither is one it has earned. The second is enforced structurally as well as by test: `publish()` **requires** the registry, so there is no code path that can write an artifact without the thing that knows each source's status.

The whole suite runs **with no network** — the fetcher is injected, and the tests hand it fixtures.

## Honest limits

- **The registry is machine-checked, not human-verified — and this is the biggest thing wrong with this repo.** 152 sources across **52 of 52 jurisdictions**, every one `verified: false`. Every URL in it has been live-fetched *by the tool's own fetcher*, its title read, **and its normalized text read**; **none of them has been confirmed by a human as the right page**, and that is a different, unfinished job. It is now a *cheap* job: `sentinel verify` is a review aid built for exactly this ([`docs/VERIFYING.md`](./docs/VERIFYING.md)), `sentinel coverage` prints the burn-down, and **every published artifact carries the status as a machine-readable field on every source** — so an integrator cannot consume one without being told, and a merge-blocking gate asserts that on the published bytes. The `checked` block on each entry records *machine* facts (status, redirect target, reachability) and is deliberately a separate field from `verified` — a socket returning 200 is not a person confirming a page.
- **What a reader could still be misled by, stated rather than implied.** The site says "unverified" beside every source, and a reader in a hurry may still take a table of one official-looking URL per state as a directory. Better copy cannot eliminate that risk; it is materially reduced only when all sources are verified and kept in date. Until then: *treat every URL here as a lead, not a citation.*
- **12 named gaps remain**, each one a (jurisdiction, document class) pair we do not watch, with the host that refused us and the reason: `blocked-403` (5), `tls-unverifiable` (3), `robots-disallowed` (2), `spa-no-text` (2). They are **data, not prose** (`gaps` in `sources/registry.json`), they are on the published site, and the completeness gate proves that *every* unwatched pair is one of them. **We do not spoof a browser User-Agent, we do not disable certificate verification, and we do not route around a robots.txt.** A blocked source degrades the tool without corrupting it.
- **The gaps used to be prose, and prose does not get checked.** DC and RI were each missing an entire document class that **no gap paragraph mentioned** — they were not decisions, they were omissions wearing the costume of decisions. The derived gate makes that unrepresentable: an unwatched pair that is not a named gap now fails the build. (RI is now watched; DC's courts 403 us and it is a named gap.)
- **The TLS stack is part of the claim.** `jud.ct.gov` answers a legacy OpenSSL 1.1.1 client with 200 and refuses the tool's OpenSSL 3.5 handshake outright. "It loads in my browser" is not evidence that this tool can watch it, and a registry recording the browser's opinion would be quietly wrong. Several of these hosts ship an incomplete chain that a browser silently repairs by chasing the AIA extension; **the chain can be completed correctly, and it is the server's job to send it** — the fetcher does not relax verification to compensate, and it never will.
- **6 of the 152 registered sources cannot currently be fetched** by our own crawler — `ssa.gov` (×2), `nycourts.gov`, `health.ny.gov`, `cdph.ca.gov`, `ilsos.gov` — and are therefore *watched in name only*, carrying **no baseline hash at all**, because a hash we did not observe is not a hash. They are not deleted: deleting them would erase the fact that we cannot watch them.
- **This detects change, it does not detect *importance*.** A state can gut a policy by an internal directive that never touches a web page, and this tool will see nothing. It is one signal, not a guarantee.
- **Coverage is uneven by document class**, and a landing page is often the deepest honest target: several states publish no statewide page for a document class at all (Texas's name-change process is county-level). Where the office page is all there is, the office page is what is watched, and the entry says so. The feed's silence about a jurisdiction means nothing at all.
- **The feed is currently, legitimately, empty.** `docs/feed.xml` is valid RSS 2.0 with zero `<item>`s and an XML comment saying it is empty rather than broken; the site says the same thing in prose. Nothing has been reviewed and confirmed by a human yet, so nothing is published, **and no change was manufactured to make the feed look alive.** An empty feed is **not** a claim that nothing changed anywhere.
- **The site says when it was *generated*, which is not the same as when the watcher last ran.** A consumer could read a fresh `generated_at` as evidence that a watch pass ran and found nothing — a wrong "no change" with a friendly face on it. Publishing the last watch pass's own timestamp and outcome is the next thing that should ship (`docs/ROADMAP.md` §10).
- **The published output is committed, deliberately, and it lives in `docs/`.** It is not a build artifact; it is the product. Committing it means the site is servable from a clean clone with no build step and no CI run — which matters, because this account has an Actions spending limit and a feed that only exists once someone else's billing system agrees to run a workflow is a feed that does not exist. It sits in `docs/` rather than `dist/` because that is the **only non-root path branch-based GitHub Pages will serve**, and the Actions-based deploy that could have served `dist/` is exactly the thing the billing limit stops. The prose docs live alongside it; [`docs/README.md`](./docs/README.md) says which files are generated and which are written by a human. **Do not hand-edit a published file** — `make publish` overwrites them, and a merge-blocking test asserts the *committed* `changes.json` contains only human-confirmed records, because with no CI in the loop the committed bytes are the served bytes.

## Responsible technology

The real risks are named and addressed in [`docs/RESPONSIBLE-TECH-AUDITS.md`](./docs/RESPONSIBLE-TECH-AUDITS.md): a wrong "no change" as a safety failure; auto-classification as an out-of-scope, forbidden capability; the subscriber list as a list of trans people; and polite crawling of public infrastructure.

## Standards

Inherits the portfolio's private engineering standards (`/STANDARDS`), fetched read-only at CI time rather than vendored. Per-repo values live in [`docs/ROADMAP.md`](./docs/ROADMAP.md) and [`docs/RESPONSIBLE-TECH-AUDITS.md`](./docs/RESPONSIBLE-TECH-AUDITS.md).

## Provenance

Built AI-assisted, within a portfolio that shares a common quality standard: every project ships merge-blocking gates for its core safety properties, and audit artifacts are committed rather than claimed.
