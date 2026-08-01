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
  only new snapshots are written under v2. Operator note: a source whose HTML
  used a loose end-tag spelling will report drift once on the first v2 pass,
  with a diff that is a normalization artifact rather than a content change.
  Nothing auto-publishes — every change record is human-reviewed before it can
  reach the feed — so that pass is caught in review.

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
