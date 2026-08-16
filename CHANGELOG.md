# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/). There is no tagged release yet — the project is
a pre-1.0 technical alpha, and everything below has landed on `main` untagged.

## [Unreleased]

### Added

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

- **Relicensed from MIT to AGPL-3.0-or-later** (sole-author relicense): keeps
  derivatives and network deployments open; prior released snapshots remain MIT.
- Monitoring readiness made explicit (2026-07-17): the public site and feeds
  identify the deployment as a technical alpha, `sources.json` v2 carries the
  exact dated attempt-eligibility decision, exclusion reasons, and fetch-policy
  outcome per source, and `sentinel baseline check` routes through the same
  dated eligibility predicate as `sentinel watch`.
