# Improvement plan, 2026-08-28

An audit of this repository's own gates, on the rule that a check which cannot fail is worse
than no check at all. Everything below was observed by running the gates and reading the
committed artifacts, not by reading the documentation.

## What was already true

`make verify` passes, 7/7, exit 0: lint, mypy strict over `src` and `tests`, 515 tests at
94.08% branch against a 90% floor, pip-audit, `sources validate` + `coverage --check-docs`,
and the two safety stages. `uv sync --locked` re-reads `pyproject.toml` rather than
`--frozen`, and the Makefile says why. CI runs exactly the Makefile targets and adds no stage
of its own, so there is no gate that only exists on a runner.

Several traps were checked for and are not here. `pytest -m <marker>` exits 5 when nothing is
selected, so a marker gate cannot pass having run no tests: renaming a marker turns stage 6 or
stage 7 red rather than green. The `possibly_removed` and `changed` suites both assert their
fixture actually produced a finding before looping over it -- "or this proves nothing", in
their own words. `sources/baseline-hashes.json` is written only by an explicit
`make baseline-write`; no workflow regenerates it, so the baseline is not refreshed by the
code that checks it. The pre-commit gitleaks hook was given a staged, correctly-shaped,
non-allowlisted credential and refused it.

## Findings

### 1. The published site says the project watches 152 sources. The registry says 156

`docs/` is the product: GitHub Pages serves the committed bytes straight off the branch, with
no build step and no CI between the commit and the consumer. `docs/sources.json` and
`docs/index.html` were last written on 2026-07-19. `sources/registry.json` was last changed on
2026-08-22, by commit b0115c5, "reconcile the registry with a real end-to-end run".

So the served inventory is four sources, one gap count and one unreachable count behind what
this project actually watches:

| Published in `docs/sources.json` | The registry says |
|---|---|
| `registered_candidates: 152` | 156 |
| `unverified: 152` | 156 |
| `named_gaps: 12` | 8 |
| `registered_but_crawler_unreachable: 6` | 12 |
| 152 source entries | 156 |

Four of those published gaps are false confessions, and this repository already has the word
for why that matters, in `completeness_violations`: "a gap that claims we are blind to
something we actually watch is a false confession, and a consumer who reads it will go looking
elsewhere for information we already have."

Nothing noticed, for two separate reasons that have to be fixed separately.

`sentinel coverage --check-docs` reads `DOC_PATHS`, and `DOC_PATHS` names the prose --
README, ROADMAP, CONSUMERS, RESPONSIBLE-TECH-AUDITS, VERIFYING, and the registry itself. It
does not name a single published artifact. The gate that exists to stop a document lying about
the registry has never read the product.

And every test in `test_source_labelling.py` takes a `published` fixture that publishes into
`tmp_path`. They prove the publisher is correct. Nothing proves the commit is.

### 2. The one test that does read the committed feed asserts nothing

`test_the_committed_published_feed_holds_the_safety_property` calls itself "THE GATE, on the
bytes that are actually served", and its docstring is right about why it needs to exist. Its
body is:

```python
for change in payload["changes"]:
    assert change["review_status"] == "confirmed", ...
```

`docs/changes.json` holds zero changes, and has since it was written. The loop does not
execute. The test has never made an assertion, and it is marked `feed_integrity`, which makes
it part of the merge-blocking stage 6. It also reads only `changes.json`: `feed.xml` -- the
RSS a consumer actually subscribes to -- and the 53 per-jurisdiction feeds are served bytes
that no test looks at.

The repository already refuses this pattern two files away, in the suite that asserts
`report.changed` is non-empty "or this test proves nothing". It just was not applied to the
gate on the product.

### 3. `make help` prints a coverage number nothing checks, and it is wrong

The Makefile says "0 of 152 sources are human-verified" twice, once inside the help text that
`make help` prints, plus "152 times" and "all 152". The registry says 156. The Makefile is not
in `DOC_PATHS`, so `--check-docs` has never read it -- and CLAUDE.md's eighth guardrail is
"Never hand-write a coverage number. `sentinel coverage` derives them; `--check-docs` gates
them." The Makefile is the first file a maintainer opens and the one that prints itself.

`.github/workflows/watch.yml` carries the same stale count in a comment.

## Plan

1. **Gate the product, then fix it.** Add a merge-blocking check that the committed `docs/`
   inventory agrees with the registry it claims to describe -- written first, watched failing
   against the stale commit, and only then satisfied by republishing with `make publish`.
2. **Make the committed-feed gate unable to pass vacuously**, and give it the other served
   artifacts: the RSS feed and every per-jurisdiction feed, not `changes.json` alone.
3. **Put the Makefile in `DOC_PATHS`** so its coverage claim is derived-and-gated like every
   other, and correct the counts that are wrong. Reword the ungated ones rather than leaving a
   number nothing checks.
4. **Record what stays blocked**, below.

## Blocked, and why

- **Issue #38** (`watch.yml` never retitles the reused review-queue issue) is real and still
  live on `main`: there is no `issues.update` call anywhere in the workflow, and issue #10 is
  the live instance -- titled "Review queue: watched sources moved" with a most-recent comment
  reading "Nothing was checked this run." **PR #41 already fixes it, with a test**, and is
  left alone rather than duplicated. It is `CONFLICTING` only because #40 rewrote the same
  block afterwards, turning two findings into three; the resolution is to rebase it onto the
  three-finding `title` computation and re-apply the same `issues.update` call in the reuse
  branch. Deciding that is the author's, not this sweep's.
- **Issue #10** is not a defect. It is the operational review-queue issue the workflow reuses
  by design, and it stays open until a human works the verification queue.
- **The verification burn-down itself.** 0 of 156 sources are human-verified, and no machine
  may change that: guardrail 9 says never flip `verified: true`, and it is right. Nothing in
  this sweep touches it.
- **Secret scanning in CI covers verified secrets only.** `trufflehog --only-verified` will
  not report a credential it cannot confirm against a live service, which is most leaked
  private keys and internal tokens. `gitleaks` does catch them, and does so at the right
  moment -- it was given a staged, correctly-shaped, non-allowlisted token and refused it --
  but it runs only in the pre-commit hook, which a contributor has to install. Closing that
  means either adding a scanner to `make verify` (a new tool dependency for a project that
  currently has zero runtime dependencies and five dev ones) or adding a CI-only stage (which
  this repository deliberately does not have). Both are decisions with a cost, so both are
  named here rather than taken.
