# Makefile — one command reproduces every gate. `make verify` is the full merge gate; CI
# runs exactly these targets, so green locally means green in CI. The env is managed by uv.
#
# Two of the seven stages are not ordinary code-quality gates. They are the safety
# properties this tool exists to hold, and they are merge-blocking for that reason:
#
#   [6] no-unreviewed-in-feed   unreviewed drift can never reach a consumer — AND no source
#                               can reach one stripped of its verification status
#   [7] no-auto-classification  the tool never calls a change "substantive" — a human does
#
# If either of those ever goes red, the correct response is to stop, not to weaken the test.
#
# Stage 6 holds two properties because they are one discipline aimed at two implicit claims:
# "a machine noticed this, so it must matter" and "this URL is in your list, so it must be the
# right page". Neither is a claim this tool has earned, and both would be made by omission.
.DEFAULT_GOAL := help
.PHONY: help install dev fmt lint type test cov security sources-validate sources-check \
        sources-stability coverage no-unreviewed-in-feed no-unlabelled-source \
        no-auto-classification verify verify-sources watch \
        watch-weekly baseline-write baseline-check publish serve clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

install: ## Create the env and install the project + dev tooling (Python 3.12+ via uv)
	uv sync --group dev
	uv run python -c "import sys; print('id-churn-sentinel env on Python', sys.version.split()[0])"

dev: install ## Alias for install. There is no server: this is a CLI + a static feed.
	@echo "No long-running surface by design. Try: uv run sentinel sources validate"

fmt: ## Auto-format and auto-fix
	uv run ruff format src tests
	uv run ruff check --fix src tests

# ---- gates -------------------------------------------------------------------------

lint: ## [1/7] Lint: correctness, security (bandit), import hygiene, marker hygiene
	uv run ruff check src tests
	uv run ruff format --check src tests
	@# A bare TODO/FIXME/HACK with no linked issue (#NNN) is not allowed to land.
	@matches=$$(grep -rnE 'TODO|FIXME|HACK' src tests --include='*.py' | grep -vE '#[0-9]+'); \
	if [ -n "$$matches" ]; then \
		echo "lint: bare TODO/FIXME/HACK without a linked issue (#NNN):"; \
		echo "$$matches"; \
		exit 1; \
	fi

type: ## [2/7] Strict type-check (mypy --strict)
	uv run mypy

test: ## Run the test suite (no coverage floor; use `make cov` for the gate)
	uv run pytest

cov: ## [3/7] Tests with coverage (enforces the >=90% floor)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=90

security: ## [4/7] Dependency vulnerability audit (pip-audit)
	uv run pip-audit

sources-validate: ## [5/7] Registry gate: valid entries, no dupes, AND no doc lying about coverage
	uv run sentinel sources validate
	@# The self-description gate, and it belongs in this stage rather than in a stage of its
	@# own: "is the registry well-formed?" and "does the project tell the truth about the
	@# registry?" are the same question asked of the same file. It derives every published
	@# coverage number from sources/registry.json and fails if a doc disagrees — and fails if
	@# any (state, core document class) pair is neither watched nor a NAMED GAP.
	@#
	@# Mirrors `gate-count` in the sibling repo trans-docs-navigator, which derives the number
	@# of merge gates from the Makefile and red-lights any doc that states a different one. A
	@# self-description is a fact about the artifact; compute it from the artifact.
	uv run sentinel coverage --check-docs

no-unreviewed-in-feed: ## [6/7] SAFETY GATES: no unreviewed drift in the feed, no unlabelled source in ANY artifact
	@# Two properties, one stage, because they are one discipline. The feed gate stops the
	@# claim "a machine noticed this, so it must matter". The labelling gate stops the claim
	@# "this URL is in your list, so it must be the right page" — which the product would
	@# otherwise make BY OMISSION, 152 times, to people who cannot afford to act on a wrong
	@# citation. 0 of 152 sources are human-verified, and every artifact says so, on every
	@# source, in a machine-readable field and in a word.
	uv run pytest -m "feed_integrity or source_labelling" -q

no-unlabelled-source: ## The labelling half of stage 6, on its own (tests/test_source_labelling.py)
	uv run pytest -m source_labelling -q

no-auto-classification: ## [7/7] SAFETY GATE: the tool never classifies a change without a human
	uv run pytest -m no_auto_classification -q

verify: ## The full merge gate — all seven stages, in order
	@echo "== [1/7] lint ==";                   $(MAKE) --no-print-directory lint
	@echo "== [2/7] typecheck ==";              $(MAKE) --no-print-directory type
	@echo "== [3/7] tests + coverage (>=90) =="; $(MAKE) --no-print-directory cov
	@echo "== [4/7] security (pip-audit) ==";   $(MAKE) --no-print-directory security
	@echo "== [5/7] sources-validate + coverage-drift =="; $(MAKE) --no-print-directory sources-validate
	@echo "== [6/7] no-unreviewed-in-feed + no-unlabelled-source =="; $(MAKE) --no-print-directory no-unreviewed-in-feed
	@echo "== [7/7] no-auto-classification =="; $(MAKE) --no-print-directory no-auto-classification
	@echo ""
	@echo "id-churn-sentinel: full gate green (7/7)"

# ---- operations (network; NEVER gates) ---------------------------------------------

coverage: ## Print the coverage numbers DERIVED from the registry (no network; never hand-written)
	uv run sentinel coverage

sources-check: ## Live-fetch every registry URL and report status. Liveness only — NOT verification.
	uv run sentinel sources check

verify-sources: ## THE HUMAN VERIFICATION QUEUE. 0 of 152 sources are human-verified; fix that.
	@# The most valuable command in this Makefile, and the only one a machine cannot run for
	@# you. It shows a human each source's title and text and records their yes/no WITH THEIR
	@# NAME — it refuses to record one without. ~3.5 hours for all 152, resumable, federal
	@# sources first. See docs/VERIFYING.md.
	@printf 'Your name (recorded in the registry against every source you confirm): '; \
	read -r name; \
	uv run sentinel verify --verifier "$$name" --federal-first

sources-stability: ## Fetch every source TWICE; name the false-drift sources. Run before adding a source.
	@# A page that re-rolls a rotating widget on every request hashes differently every week,
	@# forever. That source will train its reviewer to ignore the feed, which is worse than
	@# having no feed. This is how you find one BEFORE it reaches a queue. Doubles the load on
	@# each host, so it is an operator's diagnostic — never the weekly job.
	uv run sentinel sources check --twice

watch: ## Run a watch pass over every source (retains the bytes; produces passage diffs)
	uv run sentinel watch

baseline-write: ## Commit the store's current hashes to sources/baseline-hashes.json
	uv run sentinel baseline write

baseline-check: ## Drift vs the COMMITTED baseline. Needs no snapshot store — works on a clean clone.
	uv run sentinel baseline check

# ---- the weekly operational run (see .github/workflows/watch.yml) -------------------
#
# THE HOSTED WORKFLOW MAY NEVER RUN. This repo's owner has an account-wide GitHub Actions
# spending limit, and scheduled workflows are the first thing it stops. A monitoring service
# whose only trigger is someone else's billing system is not a monitoring service — so the
# weekly run is a Makefile target first and a workflow second. Put this in cron:
#
#   11 7 * * 1  cd /path/to/id-churn-sentinel && make watch-weekly >> var/watch.log 2>&1
#
# It does NOT publish, and it cannot: publication requires `sentinel review --reviewer`,
# a named human, and there is no path from this target to that one.
watch-weekly: ## The weekly operational run: watch, then print what a human must review
	uv run sentinel watch
	@echo ""
	@echo "Nothing above has been published. Anything found is UNCLASSIFIED/UNREVIEWED until a"
	@echo "named human runs:  sentinel diff <id>  →  sentinel review <id> --reviewer '<name>' …"
	@echo "Then, and only then:  make publish"

# The published surface lives in docs/, and that is a HOSTING CONSTRAINT, not a preference.
# Branch-based GitHub Pages serves exactly two source paths: the repository root, or /docs.
# Actions-based Pages could serve any directory — but this account has an account-wide Actions
# spending limit, so an Actions-driven deploy would never run, and a site that exists only once
# somebody else's billing system agrees to run a job is a site that does not exist. So: docs/,
# committed, served straight from the branch with no build step and no CI. See docs/README.md.
#
# FEED_URL is the canonical home stamped into every artifact's `feed_url`. It defaults to the
# repository because that URL resolves TODAY; point it at the Pages URL once Pages is switched on:
#   make publish FEED_URL=https://chelseakr.github.io/id-churn-sentinel/
FEED_URL ?= https://github.com/ChelseaKR/id-churn-sentinel

publish: ## Write the published site, the feeds, and the inventory — REVIEWED records only
	@# docs/index.html          the accessible front door + the GitHub Pages entry point
	@# docs/feed.xml            RSS 2.0, every jurisdiction
	@# docs/changes.json        the versioned JSON feed (docs/schema/changes-v1.schema.json)
	@# docs/feed-us-tx.xml      one feed per jurisdiction, so an org serving one state need
	@# docs/changes-us-tx.json  not consume all 52
	@# docs/sources.json        the inventory: every watched source AND every named gap
	@# docs/.nojekyll           stops Pages running Jekyll, which SILENTLY drops files
	@#
	@# It writes only those filenames: the prose docs alongside them (README, CONSUMERS,
	@# ROADMAP, RESPONSIBLE-TECH-AUDITS, VERIFYING, schema/) are never touched.
	uv run sentinel publish --out docs/ --feed-url "$(FEED_URL)"

serve: ## Serve the published site the way Pages does — under the /id-churn-sentinel/ SUBPATH
	@# NOT `python -m http.server` inside docs/. That serves the site at the root of a domain,
	@# which is the one configuration Pages will never use — and it is exactly the configuration
	@# in which a root-absolute link ("/feed.xml") looks perfectly fine and then 404s for every
	@# consumer on deploy day. Serve it where it will actually live, or you have tested nothing.
	@tmp=$$(mktemp -d); ln -s "$$PWD/docs" "$$tmp/id-churn-sentinel"; \
	echo "http://localhost:8000/id-churn-sentinel/"; \
	cd "$$tmp" && uv run python -m http.server 8000

clean: ## Remove build/test artifacts. NEVER touches docs/ (the product) or var/ (the store).
	@# docs/ is deliberately absent from this list. It is not a build artifact — it is the
	@# published feed, it is committed, and `rm -rf` on it would delete the product and the
	@# prose docs with it.
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
