# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/). There is no tagged release yet — the project is
a pre-1.0 technical alpha, and everything below has landed on `main` untagged.

## [Unreleased]

### Added

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
