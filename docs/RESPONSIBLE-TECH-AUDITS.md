# Responsible Technology — ID Churn Sentinel

> Instantiates `/STANDARDS` RESPONSIBLE-TECH for this repo.
> **Last verified: 2026-07-13 · Recheck cadence: per registry expansion, per feed-schema change, and on any proposal to add classification.**

This repo serves a population that is currently the subject of active, adversarial policy change in the United States. The risks below are not hypothetical externalities; they are the failure modes of *this specific tool*, and each one has either a merge-blocking gate or an honest statement of the limit.

---

## A. A wrong "no change" is a safety failure

**The risk.** This is the one that matters most, and it is the one a passing test suite cannot fully close.

If the sentinel fails to detect that a state changed its birth-certificate policy, the downstream chain is: our feed stays quiet → an incumbent's guidance page is not flagged as stale → a person reads it → they arrive at a vital-records office with the wrong documents, the wrong form, or an expectation that no longer holds. The cost lands on them: a lost day of work, a filing fee, a wasted trip, a missed deadline — and, in a hostile jurisdiction on a bad day, an interaction with a state official that is not merely inconvenient.

**Note the asymmetry.** A false positive (we flag a change that turns out to be a nav-menu reshuffle) costs a reviewer sixty seconds. A false negative costs a person materially, and *nobody ever finds out it happened*. False negatives are silent. That asymmetry is the reason for almost every design decision in this repo.

**What we do about it:**

- **Detection is biased toward noise.** Normalization is conservative, unknown content types fail toward binary (lossless raw-byte hashing) rather than toward HTML (which would strip content out of the hash and could *hide* a change), and a genuinely noisy source (the Federal Register search page) is kept in the registry on purpose, with a note saying so. A noisy source a human triages beats a quiet source that misses a rule.
- **A fetch failure is never drift — and never silence, either.** An unreachable source is reported as unreachable on every run. It is not counted as "unchanged."
- **The retained snapshots make a miss auditable.** Because the raw bytes are kept, "did we miss this?" is a question with an answer, months later.

**The limits we do not paper over:**

- **A policy can change with no web-page edit.** An internal directive to counter staff, a new interpretation applied at the window, an unpublished form revision — this tool sees *none* of that, and never will. It watches pages, and a page is a proxy for policy, not policy itself.
- **The registry covers 52 of 52 jurisdictions**, and **12 named gaps** remain — each a (jurisdiction, document class) pair we do not watch, with the host that refused us and the reason. The feed's silence about any of them means nothing at all.
- **A gap list that is prose is a gap list that will be wrong.** The gaps used to be a paragraph of English in the registry header. **DC and RI were each missing an entire document class that the paragraph did not mention** — nobody decided that; nobody could have noticed it. The gaps are now structured data with a closed vocabulary of reasons, and `sentinel coverage --check-docs` (merge-blocking) fails the build if any unwatched (state, core document class) pair is not a named gap, *or* if any doc states a coverage number the registry does not support. The first thing it caught was the registry's own header claiming a source count of 132 when the file held 131. **A project whose entire pitch is "we tell you what went stale" cannot have a stale front page.**
- **6 of the 152 registered sources cannot currently be fetched**, and are therefore *watched in name only*: `ssa.gov` (both federal SSN sources) and `nycourts.gov` return 403 to every client we have; `health.ny.gov` returns 200 to a browser and 403 to our User-Agent; `cdph.ca.gov` ships a broken TLS chain; `ilsos.gov` will not complete an HTTP exchange at all. Each is named in `sources/registry.json` with the machine facts, and each carries **no baseline hash** — a hash we did not observe is not a hash. They are not deleted, because deleting them would erase the fact that we cannot watch them.
- **The feed is currently empty, and an empty feed is not a claim.** No change has been reviewed and confirmed, so nothing is published. `dist/feed.xml` says so in-band (an XML comment: *"EMPTY, not broken … not a claim that nothing changed at any watched source"*), because the two failure modes of a blank feed are a consumer who thinks it is broken and stops reading, and a consumer who reads the silence as reassurance. The second is a wrong "no change" with extra steps.
- **This feed is a signal, not a guarantee.** It is stated in the README, in `changes.json`'s own `disclaimer` field, and in the RSS channel description — in-band, because a disclaimer that lives only in a README nobody re-reads is decoration.

**A gap that was closed (2026-07-13): three sources would have cried wolf every week, forever.** The first real two-run pass over the live registry — the runs minutes apart — reported two "changes". Neither was one. `dpbh.nv.gov` renders a rotating *"Nevada state symbol"* trivia block into its footer (**state fish → state reptile**) and re-rolls it on **every single request**; `azdot.gov/mvd` randomly samples a "frequently viewed links" list. Running the new `sentinel sources check --twice` across the whole registry caught a third: `nebraskajudicial.gov` renders its "recently adopted rules" list in **non-deterministic order**, so the same rules come back shuffled.

This belongs in §A, not in a performance section, and the reason is the asymmetry at the top of this file. A source that alerts every week with a diff about the desert tortoise does not merely waste a reviewer's minute. It teaches the reviewer that this feed's alerts are noise — and a reviewer who has learned to close alerts unread will close *the real one* unread too. **A false-positive machine is a false-negative machine with extra steps**, and false negatives are the silent, uncounted harm this whole repo is organised around.

Note carefully what the fix was **not**. It was not "normalize harder". The rotating text is real, visible page text — structurally indistinguishable from policy text — so suppressing it would mean teaching the normalizer to guess which visible text does not count, and a normalizer that guesses wrong *hides a real change*. That is precisely the trade this section refuses. The fix was to **stop watching a page we cannot watch honestly**, name it in the registry's GAP list, and ship the diagnostic that finds the next one. Nevada was removed outright (the widget is site-wide across the nv.gov CMS, so there is no stable Nevada vital-records page to substitute); Arizona and Nebraska were swapped for stable pages carrying the same content.

**And the honest limit on the fix:** `--twice` catches per-*request* rotation. A widget that re-rolls hourly or daily passes it cleanly and still drifts week over week — `azdot.gov/mvd` did exactly that, and was caught by the weekly run rather than by the check. The other half of the signal is a reviewer dismissing the same source as `editorial` twice running, which is why dismissal rate is a health metric here and not a failure metric.

**The limit, demonstrated (2026-07-13).** Expanding coverage to all 52 jurisdictions produced three candidate sources that passed `--twice` cleanly and would have cried wolf forever:

- **`leg.state.fl.us`** renders **today's date** into the statute page — a change record every day, whose diff is the date.
- **`legislature.mi.gov`** (HTML view of the Michigan Compiled Laws) renders a **live legislative-session ticker**: *"Senate adjourned until Wednesday, July 15, 2026 10:00 AM."* Michigan is watched instead via the **PDF** rendering of the same statute, which carries no ticker.
- **`ecfr.gov`** — the alternative surface for the federal SSA regulations — answers our User-Agent with a bot-wall page titled **"Request Access"**, served with **HTTP 200**.

The last one belongs in this section rather than in §D, and it is the most dangerous artifact this project has met. A **200** is what a status-code check is looking for. The page **hashes stably**. Everything downstream would have worked perfectly, and we would have watched a captcha for years and called it Social Security policy — a wrong "no change" that is not merely silent but *actively reassuring*. It was caught by **reading the normalized text**, which is now a required step before adding any source, alongside `--twice`. Neither step alone is sufficient, and the reason to say so out loud is that the first one *feels* sufficient.

**A gap that was closed (M3): a removed page used to be indistinguishable from an outage, forever.** "A fetch failure is never drift" is correct, and on its own it was not enough. An unreachable source held its previous hash and was reported as unreachable in a line of console output — which meant that a page *taken down* looked exactly like a page briefly *down*, indefinitely, and the tool answered a permanent silence with silence. That is a wrong "no change" of the worst kind, because institutions removing trans-related content is a live phenomenon and the disappearance is itself the signal. The tool now tracks consecutive failures per source and escalates past a threshold to a `possibly_removed` record requiring human review. It carries the literal error string and explicitly refuses to choose between *removed*, *blocked* and *down* — telling those apart is a person's job, and the escalation is never auto-classified as a legal change (§B still holds; the escalation constructor is given no vocabulary to classify).

**Status:** partially mitigated by design; the residual risk is real, named, and inherent to the approach.

---

## A2. An unverified registry that *reads* as authoritative

**The risk, and it is a sibling of §A rather than a footnote to it.** The published site lists one official-looking URL per (jurisdiction, document class). A reader — a caseworker, a volunteer, a trans person — sees a row saying *"OH · Birth certificate · Ohio Department of Health · `<url>`"* and reads **"this is Ohio's official birth-certificate page."** That is a completely reasonable reading of a table like that, and **it is a claim nobody has made.** `0 of 152 sources are human-verified`. If the entry is wrong, the person who acted on it loses a day of work, a filing fee, or a document — and a wrong *citation* is worse than a wrong "no change", because it is actively directive: it does not merely fail to warn someone, it sends them somewhere.

**Machine-checking cannot close this, and this repo has the receipts.** `courts.oregon.gov` serves a **soft 404** — HTTP 200, body titled *"404 Page Not Found"*. `ecfr.gov` answers our crawler with a **bot-wall titled "Request Access"**, served with **HTTP 200**. A status check blesses both; a title check blesses the second. A socket cannot tell you it is looking at the wrong page. Only a person opening it can.

**What we do about it:**

- **The status travels with the source, everywhere, as a word.** Every row of the published site carries **UNVERIFIED — machine-checked, not human-confirmed** (a *word*, never a colour or an icon — WCAG 2.2 AA 1.4.1, because the caseworker most likely to be reading this with a screen reader is exactly who a red dot fails). `sources.json`, `changes.json` and every per-jurisdiction feed carry a machine-readable `verification_status` on every source; every change record carries a `source_verification` block; every RSS channel states the count and every RSS item carries the status as a `<category>`.
- **The site's front door says it above the fold**, before the coverage numbers and before the feed: these are *candidate* URLs, no human has confirmed them, do not rely on this list as authoritative guidance — and it says what the tool *does* claim (this URL changed; this is what changed in it) against what it never claims (what the law is).
- **It is structural, not editorial.** `publish()` **requires** the registry, so there is no code path that can write an artifact without the thing that knows each source's status, and a merge-blocking gate (`-m source_labelling`, stage 6, alongside the no-unreviewed-in-feed gate it mirrors) asserts on the **published bytes** that no source appears in any artifact without it.
- **`verified: true` cannot be asserted by a machine — or by a hurried human, or by an AI agent.** An entry claiming it without a **named verifier and a date** does not load. `sentinel verify` is the only writer, and it refuses to record a verification with no name. This one is deliberately aimed at the most likely way this project would become dishonest: not a lie, but a bulk edit that makes the file *look finished*.
- **And the actual fix is the work, which is now cheap.** `sentinel verify` turns each entry into one screen (title, text excerpt, one question) and records the human's answer with their name and the date, resumably, highest-value sources first. `docs/VERIFYING.md` states the question, states what the verifier must *not* judge (they are not verifying what the law says), and states the honest cost: **≈3.5 hours for all 152**.

**The residual risk, stated rather than implied.** A reader in a hurry can still take a table of one official-looking URL per state as a directory, no matter how many times the page says otherwise. **Disclosure reduces this risk; it does not eliminate it.** The only thing that eliminates it is 152 human verifications, and until they are done, the honest summary of this registry is: *every URL here is a lead, not a citation.*

**Status:** disclosed structurally and gated; **the underlying gap is open, named, and is the top-priority work (M1).**

---

## B. Auto-classifying legal significance is out of scope and forbidden

**The risk.** The tool observes that bytes at a URL changed. It is *one small step* from there to a system that says "Texas substantively changed its gender-marker policy" — a heuristic on diff size, a keyword list, an LLM reading the passage. That step must not be taken.

Such a classifier would be **right most of the time**, which is exactly what makes it dangerous. It would be believed — by legal-aid orgs, by A4TE, by a journalist, by a person deciding whether it is safe to travel or to update a document. And when it was wrong, it would be wrong *confidently, invisibly, and at scale*, with a machine's authority behind a legal claim that a machine is not competent to make. A hash comparison cannot read law.

The tool's job ends at: *"these bytes changed, here are the passages, here is the official URL."* A person takes it from there, and their name goes on the record.

**Enforcement — four independent layers, gated by `make no-auto-classification`:**

1. **The detector has no vocabulary to classify.** `ChangeRecord.observed()` — the only constructor the detection path uses — does not accept `significance`, `review_status`, or `reviewer` as parameters. "The tool auto-flagged it as substantive" is not a bug a careless caller can introduce; it is a sentence that cannot be typed.
2. **`reviewed_by()` refuses an unnamed reviewer**, and refuses to confirm a change without classifying it.
3. **The SQL schema rejects it.** `CHECK (significance = 'unclassified' OR (reviewer IS NOT NULL AND reviewer <> ''))`. This survives someone bypassing the Python types entirely — a migration script, a bulk import, a hand-edited row. The test writes raw SQL straight at the table and proves the database refuses it.
4. **The CLI requires `--reviewer`.**

A related trap, worth naming because it nearly landed: the store originally used `INSERT OR IGNORE` for idempotency. SQLite's `OR IGNORE` **swallows CHECK violations too** — it would have silently discarded exactly the rows layer 3 exists to reject, leaving the gate green while enforcing nothing. It is now `ON CONFLICT (change_id) DO NOTHING`, which forgives only the primary-key collision we actually want to tolerate. A safety constraint that fails open is worse than no constraint, because it is trusted.

**Explicitly forbidden future features:** an LLM that summarizes "what changed legally"; a significance heuristic; a "confidence score"; a pre-sort that suggests `substantive` (a classification wearing a hat).

**Status:** enforced, merge-blocking, four layers deep.

---

## C. A subscriber list would be a list of trans people

**The risk.** Anyone who subscribes to a feed of trans identity-document law changes is, with high probability, a trans person or someone working directly with trans people. In the current US environment, that list is a targeting artifact. It could be subpoenaed, breached, sold, or handed over. There is no security control that makes holding it safe.

**The mitigation is not to secure the list. It is to never create it.**

- **No account. No login. No email capture. No newsletter signup.** There is no user model in this codebase.
- The feed is **RSS + JSON over plain HTTP**, consumable by any reader, any script, any CMS, with no credential and no registration. Per-jurisdiction feeds (`feed-us-tx.xml`) require no account either — the mitigation does not weaken as the surface grows, which is the thing to watch when a project starts shipping more of it.
- **No tracking of any kind:** no analytics, no tracking pixel, no beacon, no UTM parameters, no third-party host in the published artifacts. `test_the_feed_requires_no_account_and_carries_no_tracking` asserts the published bytes contain none of these, and it runs in the merge-blocking `feed_integrity` gate.
- **The published site makes no third-party request at all** — no CDN script, no external stylesheet, no web font, no image, no iframe. This got a gate of its own (`test_the_published_site_makes_no_third_party_requests`) the moment a site existed, because an HTML page is where this promise dies: a stylesheet from a CDN is not "just a stylesheet", it is a request that tells that CDN who is reading about trans ID law, in a country where that list is a targeting artifact. **A page that surveils trans people while claiming to protect them would be a disgrace**, and the only way to be sure it does not is to have nothing on it that can. The CSS is inline; there are no images; there is no JavaScript.
- **We do not want to know who reads this.** Consequently, we cannot report readership, and we will never be able to. That is a deliberate trade: the metric is worth less than the risk. Note what that costs *us*: the one number a funder will ask for is the one number this project has permanently forfeited the ability to produce.

**Honest limit:** we cannot control the logs of whoever *hosts* the static feed (GitHub Pages, a CDN). Server access logs will exist and will contain IP addresses. What we control is that they are not *ours*, are not enriched with identity, and are not required to consume the feed. A consumer who wants no trace can fetch it over Tor, and nothing about the feed makes that harder.

**Status:** enforced, merge-blocking. Residual risk (host access logs) named and outside our control.

---

## D. Polite crawling of public infrastructure

**The risk.** These are government web servers, funded by the public this tool serves. A watcher that hammers them is taking from the commons it claims to protect — and a state IT department that notices an aggressive unknown crawler will block it, which degrades the tool for everyone.

**What we do:**

- **Low cadence.** Weekly, per source. There is nothing about ID-document policy that requires minute-by-minute polling, and pretending otherwise would be a self-serving excuse for a more aggressive crawl.
- **A descriptive User-Agent** that names the project and links to its repository: `id-churn-sentinel/0.1 (+https://github.com/ChelseaKR/id-churn-sentinel; weekly change detection over official ID-document pages)`. A server operator who wants to know who is hitting them weekly can read it straight out of the access log and find a human.
- **robots.txt is honoured** — fetched per host, cached, and obeyed without appeal. A robots.txt that loads and disallows us wins; we do not fetch the page. (A robots.txt that is missing or unreachable is treated as permissive, the standard crawler posture.)
- **Every socket is bounded.** A 20-second timeout on both the page fetch and the robots.txt fetch, and a bounded body size. We do not fetch robots.txt via `RobotFileParser.read()`, because it calls `urlopen` with *no timeout* — an unattended weekly job would hang forever against a server that accepts a connection and never answers.
- **We respect terms of service.** Where a source's terms forbid automated access, the correct response is to remove it from the registry, not to route around it.
- **Being blocked degrades us without corrupting us.** A block is a fetch failure, and a fetch failure is never drift. We would report the source as unreachable, hold the old hash, and publish nothing false.

**What this actually cost us, stated rather than implied.** Honouring these rules is why the registry has **12 named gaps** instead of 12 quietly-spoofed sources. `oscn.net` publishes `Disallow: /` — it forbids all automated access to the entire site, and it is where Oklahoma publishes both its courts *and* its statutes, so Oklahoma's name-change process is unwatchable, full stop. `dccourts.gov`, `dmv.vermont.gov`, `dmv.alaska.gov`, `dmv.colorado.gov` and `cdphe.colorado.gov` serve a browser and 403 us. Several hosts present TLS chains our trust store cannot verify. In every one of these cases there is an obvious two-line change — spoof a Chrome User-Agent, pass `verify=False`, ignore the robots.txt — that would "fix" coverage today. **We do not make it, and the gap is recorded instead.** A tool that lies about who it is, to a government server, on behalf of a population under surveillance, has not earned the trust it is asking for.

**And the honest counterpoint, because the paragraph above is the easy half.** Refusing to spoof is not the same as refusing to *try*. Michigan and New Hampshire were absent entirely for exactly one reason — `michigan.gov` and every `nh.gov` host 403 our User-Agent — and "we honour their block" is a complete moral defence and an *incomplete engineering one*. A state almost always publishes the same policy content on a **second official surface**: its statutes, its administrative code, a court's PDF form. Those surfaces answer us honestly, and watching one is not a workaround — it is a different, equally official page for the same document class, and the entry says exactly what it is. Sixteen jurisdictions were closed that way, MI and NH among them, without sending a single dishonest byte.

The line is worth stating precisely, because it is the line this section exists to hold: **changing which official page we watch is legitimate; changing who we claim to be is not.** A gap should be a fact about what the world will let us see honestly — never a fact about what we could not be bothered to look for.

**On TLS specifically.** Most of the broken-chain hosts here are not serving *invalid* certificates; they are serving **incomplete chains**, omitting an intermediate that a browser silently repairs by chasing the AIA extension. That chain *can* be completed correctly — by the server, which is whose job it is. We do not fetch the missing intermediate ourselves and we do not disable verification, and the reason is not pedantry: the population this tool serves is one for whom a silently-downgraded TLS connection to a government host is exactly the wrong thing to normalise. The defect is the server's; the gap is ours to record.

**The weekly job is bounded by construction.** `.github/workflows/watch.yml` runs `cron: "11 7 * * 1"` — weekly, offset off the hour so we are not part of a thundering herd hitting state websites at `:00`. Its `concurrency` group never cancels a run mid-crawl. `sentinel sources check --twice` doubles the load on each host and is therefore an operator's diagnostic run when the registry changes, **never** the weekly job — a fact stated in its own `--help` text, in the Makefile, and in its docstring, because a convenient-but-impolite command gets used conveniently.

**Status:** enforced structurally in `core/fetch.py`; the cost is paid in coverage and recorded in `sources/registry.json`.

---

## E. Harm to the incumbents we are trying to help

**The risk.** A tool that publishes "A4TE's page on Texas is out of date" could read as an attack on organizations doing hard work with fewer resources, and could be quoted that way by people hostile to all of them.

**What we do.** The feed reports **changes at official government sources**. It does not audit, score, grade, or name any advocacy organization, and it never will. It says "the Texas DPS page changed"; it does not say "A4TE is wrong." The framing throughout the repo is explicit that coverage is not the gap, that the incumbents' staleness is a *monitoring* problem rather than a competence problem, and that this repo is **infrastructure for them, not a competitor to them** (`docs/CONSUMERS.md`).

**Status:** a framing and scope commitment, not a code gate. Reviewed on every change to the feed's content.

---

## F. The reviewer is a single point of failure

**The risk.** The human-in-the-loop gate is load-bearing, and today the human is one person. A single reviewer who is unavailable, overloaded, or burned out becomes a bottleneck; a single reviewer who starts rubber-stamping turns the whole gate into theatre while every test stays green.

**What we do.** Noise suppression is treated as a *safety* feature rather than a polish feature, precisely because review cost drives rubber-stamping. Dismissal is as cheap as confirmation. The M2 milestone explicitly measures per-week review cost and dismissal rate, on the theory that a gate nobody can sustain is a gate that will fail quietly.

**Honest limit:** bus factor is 1. Multi-reviewer sign-off for `substantive` changes is an open question in `docs/ROADMAP.md` §10, not a solved problem.

**Status:** open, named, tracked.
