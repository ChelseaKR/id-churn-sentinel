# Contributing to id-churn-sentinel

Thank you for considering a contribution. This tool watches government sources
for changes that affect trans people's identity documents, and a named human
stands between detection and publication — so contributing here carries
obligations beyond the usual. Read
[`README.md`](README.md) (especially *"Read this before you rely on anything
here"*) for what the project is and is not,
[`SECURITY.md`](SECURITY.md) for how to report a vulnerability, and
[`docs/RESPONSIBLE-TECH-AUDITS.md`](docs/RESPONSIBLE-TECH-AUDITS.md) for the
risks the design exists to hold.

Note the **commercial hold** (see
[`docs/COMMERCIAL-STATUS.md`](docs/COMMERCIAL-STATUS.md)): only noncommercial
public-interest research, documentation, safety analysis, and open-source
technical work is in scope right now.

## The rules that are not negotiable

- **Never weaken gates 6 and 7.** *No unreviewed drift in the feed* and *no
  automatic `substantive` classification* are the safety properties this tool
  exists to hold. If either goes red, the correct response is to stop, not to
  loosen the test.
- **Never degrade the fetcher's politeness or integrity.** No spoofed
  User-Agent, no disabled certificate verification, no routing around
  robots.txt, no lowered crawl cadence "just to test" — use `--jurisdiction`
  locally instead.
- **The test suite makes no network calls.** The fetcher is injected and
  tests hand it fixtures. A test that depends on a state government's website
  being up is a bug in the test.
- **Do not hand-edit published files under `docs/`** (`feed.xml`,
  `changes*.json`, `status.json`, the site) — `make publish` overwrites them,
  and a merge-blocking test asserts the committed feed holds the safety
  property. [`docs/README.md`](docs/README.md) says which files are generated.
- **No new runtime dependencies** without a very good argument: the zero-dep
  posture is a deliberate decision (see `pyproject.toml`'s comments), not an
  accident.

## Getting set up

The project targets Python 3.12+ and uses [`uv`](https://docs.astral.sh/uv/)
for a reproducible, frozen environment:

```sh
make install
```

Run `make help` to see every target. Optionally install the pre-commit hooks
(`uvx pre-commit install`) — they run the same ruff/mypy/gitleaks the gate
runs.

## The merge gate

A change merges when the full gate is green. Reproduce it locally with:

```sh
make verify
```

`make verify` runs all seven merge-blocking stages — lint (ruff check +
format + bare-TODO gate), `mypy --strict`, pytest with the **90% branch
coverage floor**, `pip-audit`, registry validation + coverage-drift check,
and the two safety gates — exactly what `ci.yml` runs, on the same pinned
(`uv sync --frozen`) toolchain. Green locally means green in CI; there is no
CI-only gate and no local-only gate. (And because the account has an Actions
spending limit, *local* `make verify` is the gate that always exists —
never rely on CI to catch what you didn't run.)

See the **Gates** table in [`README.md`](README.md#gates) for what each stage
holds and why.

## Pull requests

- Keep PRs focused; explain *why*, not just *what*.
- Structural decisions get an ADR in [`docs/adr/`](docs/adr/) (see ADR 0000).
- Changes worth telling a consumer about get a line under `## [Unreleased]`
  in [`CHANGELOG.md`](CHANGELOG.md).
- Never commit secrets, real personal data, or anything about identifiable
  users of identity-document processes. There are none in this repo today;
  keep it that way.
