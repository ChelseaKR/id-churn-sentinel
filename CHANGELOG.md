# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/). There is no tagged release yet — the project is
a pre-1.0 technical alpha, and everything below has landed on `main` untagged.

## [Unreleased]

### Added

- **Five named gaps closed with real, independently re-verified government sources**
  (2026-08-21): AK drivers_license (`akleg.gov`, AS 28.15 — scoped via the print
  view's `secStart`/`secEnd` query parameters, not the `#fragment` links the
  original attempt tried, which a server never sees), AR drivers_license
  (`dfa.arkansas.gov/office/mydmv` — the 2026-07-13 TLS chain defect the gap
  recorded appears to have been fixed since), DC court_order_name_change
  (`code.dccouncil.gov`, D.C. Official Code § 16-2501, under a chapter titled
  "Change of Name or Gender"), LA drivers_license (`legis.la.gov`, RS 32 Ch. 2 —
  a different host from the JS-shell OMV portal the gap named), and SD
  drivers_license (`sdlegislature.gov`'s server-rendered `/api/Statutes/...`
  endpoint, avoiding the same SPA-shell trap the gap named on a different path).
  Every one of the five was fetched, passage-counted, and hash-compared across
  two fetches by this pass directly (not accepted from an unverified report);
  152 → 156 sources, 12 → 8 named gaps. Following the exact convention this
  registry already uses (README's "Closing the map without lying to get
  there"): a different government host on the *same* jurisdiction's domain,
  never a guessed replacement authority. No jurisdiction outside the registry's
  declared scope (50 states + DC + the US federal bucket, 52 of 52) was
  considered — territorial expansion is explicitly deferred pending
  governance/capacity review per `docs/00-V1-PLAN.md` and `docs/12-ROADMAP.md`.
- `docs/THRESHOLD-EVIDENCE.md` (2026-08-19): the audit behind `REMOVAL_THRESHOLD`,
  recording what observation history actually exists (one 74-minute session on
  2026-07-13; empty per-attempt evidence tables; no failure ever observed
  *ending*, so every outage on record is right-censored), why an outage-length
  distribution cannot be derived from it, the units defect it did find, and the
  retention and sampling changes required before the question is answerable.
  Includes the caveat that a weekly sampler cannot resolve sub-weekly outages in
  principle, however long it runs.
- An immutable v2.0.0 public standards projection, managed manifest, Renovate
  tracking, and regression tests for projection completeness and current-tip
  publication boundaries.
- `sentinel review --list` (2026-08-04): the store-backed twin of `sentinel
  verify --list` — prints every change still `unreviewed` (drift and
  `possibly_removed` escalations alike), optionally filtered by
  `--jurisdiction`, straight from the local snapshot store. No network, no
  prompts, no writes. For a reviewer who no longer has `watch`'s output on
  screen or whose review-queue issue already closed, this was previously a
  choice between re-running `watch` against live government servers or
  reading the SQLite file by hand.
- An owned internationalization declaration (`docs/I18N.md`) now fixes the V1
  Spanish metadata scope, independent-review workflow, fail-closed English
  fallback, and 2026-11-13 target without claiming translations already exist.
- Cited change detection for US transgender identity-document law and process:
  polite weekly fetch of registry-declared government sources, normalized-text
  hashing with passage diffs for HTML/text, byte-change detection for binaries.
- Source registry: 152 sources across 52 of 52 jurisdictions, machine-checked
  (`sources-validate`) with named gaps as data, none yet human-verified — and
  every published artifact says so per source.
- Human-in-the-loop review and publication: `sentinel verify` review aid,
  append-only review and corrections, no path from the watcher to publication.
- Watcher run receipts: eligible/attempted/successful/observed sets persisted
  per run; `status.json` and the site distinguish quiet, partial, failed,
  running, and stale health; eligibility is always evaluated on today's UTC
  date and `sentinel watch` fails closed while the honest attempt denominator
  is zero.
- Versioned evidence provenance pages and branch-served published site under
  `docs/` (RSS feed, per-jurisdiction change JSON, status page).
- Seven-stage merge gate (`make verify`): ruff lint + format, mypy strict,
  pytest with a 90% branch-coverage floor, pip-audit, registry validation +
  coverage-drift check, and the two safety gates — no unreviewed drift in the
  feed / no unlabelled source, and no automatic `substantive` classification.
- Standards-conformance sweep (2026-07-16): security workflows (CodeQL,
  TruffleHog), release gate workflow, SECURITY.md, CONTRIBUTING.md,
  CITATION.cff, pre-commit config, ADR log, this changelog, and a README
  conformance table.
- Per-host crawl spacing in `HttpFetcher` (2026-07-17): consecutive page
  requests to the same host are held at least a minimum interval apart
  (default 2s), structurally, so no call path can burst a government server.

### Fixed

- **Six registered sources were recorded as reachable when they are not, and one
  pointed at a dead URL** (issue #10, 2026-08-22). Every `checked` block written
  in this pass came from an actual fetch through this repo's own
  robots-respecting `HttpFetcher`, not from a previous pass's report:
  `travel.state.gov` (both federal passport sources) and `ldh.la.gov` (both
  Louisiana vital-records sources) answer HTTP 403; `legislature.mi.gov`'s two
  MCL PDFs fail TLS verification with `CERTIFICATE_VERIFY_FAILED` and never get
  an HTTP status at all. All six now carry `reachable: false` with the
  registry's existing machine vocabulary (`blocked`, `tls_chain_broken`), so
  none of them can be counted as unchanged. Watched-in-name-only goes 6 → 12,
  derived by `sentinel coverage`, never hand-written.
- **NY courts' name-change URL moved, and is recorded as moved-but-still-unreadable**
  (issue #10). The 2026-07-13 URL returns 404 and the replacement path returns
  403 — the server's own 404-vs-403 split is the evidence that the old path was
  removed while the new one exists, and the URL is corrected on that basis. It
  is **not** counted as fixed: this tool could not read the replacement on any
  of 5 attempts (4 spaced 120s apart, fresh robots cache each time).
  `www.nycourts.gov` refuses this tool's User-Agent host-wide and `/robots.txt`
  itself 403s; because an unreadable robots.txt is treated as permissive
  (`core/fetch.py`), this is a source we cannot fetch, not a `robots-disallowed`
  gap. An earlier pass had reported two successful reads of the new URL with
  real on-topic content; that did not reproduce, and it is recorded in the
  entry's notes as a prior observation rather than as its status.
- **The published claim that every unreachable source carries no baseline hash
  was not true any more.** Six of the twelve were reachable when the 2026-07-13
  baseline was written and still hold the last hash actually observed. README
  and `docs/RESPONSIBLE-TECH-AUDITS.md` now state both cases and the reason the
  distinction matters — a carried-forward hash keeps an outage from becoming
  drift, and must never turn an outage into a clean bill of health.
- **`sources/registry.json` was being rewritten in a non-canonical style**, and
  there was no gate to notice. A plain `json.dumps(indent=2)` explodes every
  inline `checked`/`verification`/`fetch_policy` block, growing the file from
  ~1750 to ~2400 lines and turning a two-field edit into a ~960-line diff; this
  had happened twice. The file is written through `dump_registry_text` again,
  and `test_committed_registry_is_written_in_the_canonical_style` now fails the
  build if it drifts. This protects the `sentinel verify` audit trail, whose
  value depends on each of up to 156 rewrites being a one-line diff naming a
  human.
- **New invariant over the committed registry:**
  `test_no_committed_entry_renders_a_failed_fetch_as_a_reachable_one` fails the
  build if any entry pairs an HTTP status of 400+ (or no status at all) with
  `reachable: true` — issue #10's harm expressed as a merge gate rather than a
  convention.
- **`courts.michigan.gov` tightened its robots.txt to a site-wide `Disallow:
  /`** (issue #10, 2026-08-21), confirmed directly against the live host
  (independent of this tool's own fetcher, to rule out a fetcher bug) and
  reconfirmed on a second check. The SCAO-approved PC 51 name-change petition
  PDF this registry watched from that host is now a named gap
  (`robots-disallowed`) rather than a source we quietly kept fetching in
  violation of it; a search for an equivalent Michigan government host serving
  the same form did not find one. No other registry entry used this host.
- **`protect-main` required zero status checks** (#24): the ruleset had only
  `deletion` and `non_fast_forward` rules, so `main` reported as protected
  while `ci.yml`'s `verify`, both CodeQL matrix jobs, and the TruffleHog scan
  were all advisory — a red check could not block a merge. `protect-main` now
  names all four as `required_status_checks`, matched against the real
  check-run contexts on `main`'s own HEAD commit rather than guessed from
  workflow YAML. Verified this actually blocks a merge, not just reports one:
  a throwaway PR with a deliberate lint failure went `mergeStateStatus:
  BLOCKED` and `gh pr merge` was refused outright ("the base branch policy
  prohibits the merge"), closed without merging once confirmed.
- **Working the whole verification queue left the attempt denominator at
  zero** (#18). `verify.confirm()` was the only writer of a verification in the
  codebase and wrote `status`, `verifier`, `at` and `note`, while
  `core/eligibility.py` also requires an evidence reference and an in-date
  recheck expiry — and nothing in `src/` could write a fetch-policy decision at
  all, so the only path to one was hand-editing `sources/registry.json`, which
  `docs/VERIFYING.md` never mentioned. Measured: confirming all 152 sources
  published a site headlining *all 152 sources are human-verified* next to
  `attempt_eligible: 0`, a feed that would stay empty, and nothing anywhere
  saying a second step existed. Now a confirmation records all four fields: the
  evidence reference points at a receipt `sentinel verify` writes at the moment
  of the decision (URL, fetch time, HTTP status, the page's own title, the
  excerpt the human read, and the content hash — and for a source we cannot
  fetch, a receipt that says so and claims no title or text), and the expiry is
  dated 180 days forward. `sentinel sources policy` records the dated
  robots/terms decision (`SRC-03`) from a named reviewer with evidence, reason
  and expiry, and refuses blanks and `unreviewed`. `verify --list`, the end of
  a `verify` session, and `sources policy` each print how many sources are
  attempt-eligible and what is blocking the rest, and the first screen of
  `docs/VERIFYING.md` says both decisions are required.
- **A zero attempt denominator no longer renders as a clean run** (#18). The
  site said *attempted 0 of 0 eligible sources; 0 successful retrievals*, which
  is arithmetically true and reads as a run that had nothing to do and did it
  perfectly; completeness over an empty denominator is not a number and is now
  reported as not measurable. A registry in which every source is verified and
  none is attempt-eligible no longer headlines the verification.
- **`sentinel sources check --twice` reported a page with no readable text as
  `stable`.** A JS shell, an empty 200 and a bot-wall all normalize to zero
  passages, whose detection hash is `sha256("")` — and that digest matches
  itself on two back-to-back fetches, so every blind page passed the check as
  stable. It passed *silently*, too: the command prints a line only for
  `UNSTABLE` and `unreach`, so the page appeared nowhere on stdout and only in
  the reassuring half of the summary. That is the worst place in the codebase
  for that particular false all-clear, because CLAUDE.md guardrail #7 makes this
  command the gate a maintainer runs *before* adding a source — so the check
  answered "safe to watch" about exactly the pages `sentinel watch` can never
  observe, and which it correctly routes to `no_text` on every run. `watch()`
  and `check_baselines()` already refused this comparison (issue #19); this was
  the third and last comparison in the codebase still making it. Such a source
  now lands in its own `StabilityReport.no_text` bucket, is never counted as
  `stable` or `UNSTABLE`, gets a `NO TEXT` line and a named clause in the
  summary, and is judged on the *first* fetch so the second request is not spent
  on a page we already know we cannot read. A page that is readable once and
  blind once is `no_text` as well, rather than `UNSTABLE` — the two hashes do
  differ, but naming that as a rotating widget would send a maintainer hunting
  for something that is not there. Binary sources are exempt, as everywhere
  else: a PDF's empty normalized text is by design and its hash covers the raw
  bytes, so comparing two fetches of it remains a real measurement.
- **The install step in front of every gate could not see lockfile drift.**
  `make install` ran `uv sync --frozen --group dev`, and `make verify` depends on
  `install`, so this was the first thing every merge gate did. `--frozen`
  installs from `uv.lock` without reading `pyproject.toml`; it cannot notice the
  two disagree and exits 0 on a drifted lock, so a dependency added or bumped
  without relocking would have installed cleanly and passed all seven stages
  while the test environment quietly stopped matching the declared dependencies.
  Now `uv sync --locked`, which re-resolves and exits 1 on drift. That matters
  more here than elsewhere: with an Actions spending limit in play, local
  `make verify` is the gate that always exists, so it has to be the strict one.
  `CONTRIBUTING.md` and the `verify` help text are updated to match.
- **Four standards were undeclared in the README conformance table**, which the
  section's own preamble promises states the position honestly: Performance, AI
  Development Measurement, Incident Response, and Data Governance. All four are
  now declared with their current state, each pointing at the artifact that
  already carries the work (`docs/10-OPERATIONS-SRE.md` for the runbooks and the
  freshness gates, `docs/05-DATA-AND-EVIDENCE.md` for the evidence and retention
  plan) and naming what has not been done.

- **`sentinel baseline check` reported a run that examined nothing as a clean
  run.** With an empty attempt denominator it printed `0 source(s): 0 match the
  committed baseline, 0 MOVED`, emitted `baseline-check-moved-count: 0`, and
  exited 0 — byte-for-byte what a complete run over sources that all matched
  emits. `watch.yml` branches on those numbers, so it concluded
  `needs-review=false` and went green. With the registry at 0/152
  attempt-eligible that is the branch it took on every weekly run to date, all
  four of which reported success while checking zero sources. The command now
  emits `baseline-check-attempted-count:` on every run, says `NO SOURCE WAS
  CHECKED`, and exits 1 when the denominator is empty; the workflow branches on
  the denominator first, files a review issue that says what actually happened,
  and only then goes red. Drift and unreachable sources still exit 0 — a state
  website being down is the tool working.
- **A committed baseline hash could be compared against a page it was never
  taken from.** `write_baselines` records each hash's URL; `load_baselines`
  dropped it, so re-pointing a source in the registry turned page A's hash into
  "page B MOVED" — a false change, filed by the weekly job as *a watched
  official source is no longer what the committed baseline says it was*.
  `watch()` has always refused this comparison and re-baselined instead; the
  loader now keeps the URL so `baseline check` can make the same refusal, in its
  own `url_changed` bucket and its own count, never folded into MOVED or into a
  match. A test pins that no committed entry cites a page the registry no longer
  watches.
- **A redirect walked past both of the fetcher's guards.** The https-only check
  and the robots check applied to the URL the module was handed and to no other,
  while `HTTPRedirectHandler` follows `http` as happily as `https` and never
  reconsults a policy — so a page that 301'd to cleartext was read in cleartext,
  and a page that redirected to another host was read without that host's
  robots.txt ever being fetched. Both are now refused before any body is read,
  with the hops taken beforehand kept as evidence. A host's declared
  `Crawl-delay` is also honoured now when it exceeds the 2s floor; a shorter one
  does not speed us up.
- **Two published surfaces read as "finished" while the tool watched nothing.**
  `index.html` headlined *All N sources are human-verified* and every RSS
  channel description carried *All N sources in TX are HUMAN-VERIFIED* whenever
  the verification flag was set — true of the flag, and read as *and therefore
  watched* at a moment when the feed could not populate. Both claims are kept
  but conditioned on the sources being attempt-eligible. The run-health block no
  longer renders `attempted 0 of 0 eligible sources` as a ratio.
- Current documentation, source comments, registry metadata, and tests no
  longer expose private sibling repository names or source paths; existing Git
  history remains unchanged.
- **A source with no extractable text could be quietly baselined, then report
  `unchanged` forever** (#19). `sha256("")` is a real, stable hash — it is
  what a JS shell, an empty 200, and an HTTP-200 bot-wall all normalize to —
  and the detector had no concept that a comparison against "nothing" means
  nothing. Measured: an injected fetcher serving a script-only page baselined
  as `new` on first sighting and reported `unchanged` on every run after,
  identically to a genuinely stable page, with nothing in the run receipt or
  `sentinel watch` output distinguishing the two. A text/HTML fetch that
  normalizes to zero passages now lands in its own `no_text` bucket instead —
  never baselined, never folded into `unchanged`, printed loudly by
  `sentinel watch` on every run it recurs, for as long as it recurs. Binary
  content (PDFs) is unaffected: an empty normalized text there is documented,
  honest behaviour, not this failure. `sentinel sources check` also now
  prints each reachable text/HTML source's passage count and `<title>`, so
  the same trap is visible before a source is added, without a second
  command or opening the URL by hand.
- **And the three things that bucket did not fix** (#19, second pass). Routing
  the fetch to a bucket happened *after* it was written to the snapshot store,
  so `sha256("")` still became the source's latest snapshot — which is its
  baseline: the row `sentinel baseline write` commits, and the row next week's
  fetch is compared against. Measured on an injected fetcher: a page that went
  blank for one run and came back **byte-identical** was reported as `changed`,
  with a diff claiming the whole page had just been added; six blank runs
  evicted the last readable snapshot through retention, destroying the evidence
  a diff is reproduced from. Now: the check happens before any write, so no
  snapshot row is created, the last real baseline stands untouched, and a
  recovered page reports `unchanged`. The failure streak is no longer reset by
  such a fetch either — `record_success` means "the source answered", and a
  bot-wall has not. The run `state` gap named in the entry above is closed:
  a run holding an unmeasured source records as `partial`, never `quiet`, and
  `finish_watch_run` refuses `quiet`/`complete` independently of the detector.
  `status.json` (schema 1.1) and the site now carry `unmeasured_source_ids`,
  `unmeasured_count` and `observed_source_count` beside the successful
  retrievals, `sentinel baseline check` reports unreadable pages in their own
  bucket and emits `baseline-check-no-text-count:` for CI, and the weekly
  workflow files the review-queue issue on that count as well as on MOVED.
  `baseline write` refuses to commit a hash of nothing even from an older
  store, and `load_baselines` refuses to load one.
- Normalizer end-tag matching (2026-08-01): `</script >`, `</style\t>` and
  `</script foo="bar">` are all valid ways to close an element and every
  browser honours them, but the strip regexes required the tight `</script>`
  spelling. On a page using any other spelling the element never matched, so
  its *body* — minified JavaScript full of cache-busting build ids, CSRF
  tokens and timestamps that re-roll on every request — was hashed as page
  text, making the page look like it changed on every fetch. That is the
  permanent-false-alarm failure the normalizer exists to prevent, and it
  failed silently. `page_title` had the same defect and returned an empty
  title for `</title >`, losing the best signal a human has for telling a real
  page from a bot-wall. Found by CodeQL (`py/bad-tag-filter`).
- **Normalizer version bumped to `passage-text-v2`** as a consequence, with a
  new append-only `representation_contracts` row (migration 6). Existing
  snapshots keep their `passage-text-v1` label and stay exactly as recorded;
  only new snapshots are written under v2.
- **The detector no longer compares hashes across normalizer versions**
  (2026-08-01). A hash means nothing except relative to the normalizer that
  produced it, and `detect.py` compared them regardless — so any version bump
  turned every affected page into a change record whose diff was a
  re-normalization artifact the tool had manufactured about itself, handed to
  a reviewer as drift. `passage-text-v2` made that live. The detector now
  **re-derives the baseline under the current contract** from the snapshot's
  retained bytes before comparing, so both sides of every comparison come out
  of the same normalizer. A version bump therefore cannot report drift — and,
  equally important, cannot hide it: a page that really changed during the
  same pass still produces a change record, with a like-for-like diff and a
  stated note that its baseline was re-derived. The first v2 pass over a v1
  corpus reports one grouped `re-baselined onto a new normalizer, NOT drift`
  block naming the transition, rather than one alarm per source. A baseline
  that cannot be re-derived claims no drift in either direction and is
  reported in its own bucket, because "we cannot make this comparison valid"
  must never be dressed up as "the page changed".
- **`sentinel baseline check` labels a cross-version comparison instead of
  presenting it as drift** (2026-08-01). The committed
  `sources/baseline-hashes.json` holds hashes and no bytes, so unlike
  `sentinel watch` it structurally cannot re-derive an old baseline. Refusing
  every pre-v2 hash would leave a clean checkout unable to say anything, which
  is the hole that file exists to fill — so a MOVED hash recorded under a
  different normalizer is still reported, and reported *as* a comparison that
  may be measuring our normalizer rather than the page, on the affected
  sources only. Baseline entries now record `normalizer_version` /
  `extractor_version`; an entry written before that field loads as
  `unrecorded` rather than being assumed to be v1.

### Removed

- Internal planning notes not relevant to the public repository (2026-07-19).

### Changed

- **An escalation to `possibly_removed` now requires elapsed silence, not just a
  count of failed fetches** (2026-08-19). `REMOVAL_THRESHOLD` counted *runs*
  while its own documentation described *weeks*, and nothing in the code
  connected the two. In the only observation session this repository has
  retained, six sources reached a streak of three inside 74 minutes because the
  watcher was run three times in one sitting — three weeks by the constant's
  stated reasoning, three minutes in fact. Escalation now additionally requires
  `MIN_REMOVAL_SILENCE` (14 days) of unbroken silence, `source_health` records
  when a streak began (migration 8), the reviewer's excerpt reports the duration
  beside the count, and a streak whose start was never recorded does not
  escalate on the count alone. `sentinel watch --min-removal-silence-days`
  exposes the floor. `REMOVAL_THRESHOLD` itself is unchanged at 3 and is still
  documented as an unmeasured guess, because it still is one — see
  `docs/THRESHOLD-EVIDENCE.md` for the audit that tried to re-derive it, the
  reason it could not, and what has to be retained before it can be.
- **Relicensed from MIT to AGPL-3.0-or-later** (sole-author relicense): keeps
  derivatives and network deployments open; prior released snapshots remain MIT.
- Monitoring readiness made explicit (2026-07-17): the public site and feeds
  identify the deployment as a technical alpha, `sources.json` v2 carries the
  exact dated attempt-eligibility decision, exclusion reasons, and fetch-policy
  outcome per source, and `sentinel baseline check` routes through the same
  dated eligibility predicate as `sentinel watch`.
