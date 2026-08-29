"""No reader-facing document states a hand-typed figure about this test suite.

`docs/ROADMAP.md` already states the rule, in its own metrics table:

    | Branch coverage | >=90% floor (the measurement is printed by `make cov`, not
      recorded here -- nothing re-derives it into this table) | `make cov` | merge-blocking |

Twenty lines later it recorded the measurement anyway: *"7 merge-blocking gates; 143 tests,
~99% coverage, all offline."* The suite is 533 tests at 94.06% branch. Nothing in the
repository could have noticed, because nothing read that sentence -- 390 tests and five
percentage points of drift, in the direction that overstates, sitting under the heading
**shipped**.

`pyproject.toml` records the same lesson about itself, having already removed a note reading
*"Measured 93% on 2026-07-16 (357 tests)"*: "The measured percentage is deliberately NOT
recorded here. It moves with every test added, nothing derives it into this file." The README
states it a third time. Three documents articulate the policy; one violated it.

So this gate holds the policy rather than the figure, which is the only version of it that
does not need maintaining:

1. **No document states a test count.** Not "143 tests", not a corrected "533 tests" -- the
   right number today is the wrong number after the next commit, and a gate that demanded the
   right one would go red on every test added, which is how a rule gets deleted.
2. **Every coverage percentage a document states is the configured floor.** A floor is a fact
   about the configuration and is derived from it here; a measurement is a fact about one run
   and belongs in that run's output.

**Scope is `core.coverage.DOC_PATHS`**, the set this repository already curates as "the
documents that describe this project to a reader ... a claim nobody checks is a claim that
will eventually be wrong". Adding a document there is an established discipline, so this
needs no exception list of its own -- and dated audit records under `docs/plans/`, which are
lab notebooks rather than live claims, stay outside it on the same existing rule.
"""

from __future__ import annotations

import re
from pathlib import Path

from id_churn_sentinel.core.coverage import DOC_PATHS, repo_root

#: A count of tests, in any of the shapes prose uses for one.
TEST_COUNT = re.compile(r"\b([0-9][0-9,]*)[\s-]tests?\b", re.IGNORECASE)

#: A percentage whose surrounding words make it a claim about code coverage. The window is
#: what separates "94.06% branch coverage" from "52 of 52 jurisdictions", which is a
#: different coverage number entirely and is gated by `sentinel coverage --check-docs`.
#:
#: It reaches BOTH ways, and that is not symmetry for its own sake: the sentence this file
#: exists because of writes the noun after the number ("~99% coverage"), and a
#: backwards-only window read it as an uncontextualised percentage and let it through. The
#: guard test below is what caught that, which is the whole reason it is there.
_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
_COVERAGE_CONTEXT = re.compile(r"coverage|branch", re.IGNORECASE)
_WINDOW = 60


def _configured_floor() -> int:
    """The branch-coverage floor, read from the two places that enforce it.

    Both are read, and they have to agree: a floor configured in `pyproject.toml` and a
    different one passed on `make cov`'s command line would mean the documents could be
    truthful about one gate and wrong about the one that actually runs.
    """
    root = repo_root()
    declared = re.search(
        r"^fail_under = ([0-9]+)$", (root / "pyproject.toml").read_text(encoding="utf-8"), re.M
    )
    enforced = re.search(
        r"--cov-fail-under=([0-9]+)", (root / "Makefile").read_text(encoding="utf-8")
    )
    assert declared is not None, "pyproject.toml sets no [tool.coverage.report] fail_under"
    assert enforced is not None, "the Makefile passes no --cov-fail-under"
    assert declared.group(1) == enforced.group(1), (
        f"pyproject.toml declares a {declared.group(1)}% floor and `make cov` enforces "
        f"{enforced.group(1)}%; the documents cannot be right about both"
    )
    return int(declared.group(1))


def coverage_percentages(text: str) -> list[str]:
    """Every percentage in `text` that its preceding words make a coverage claim."""

    found = []
    for match in _PERCENT.finditer(text):
        before = text[max(0, match.start() - _WINDOW) : match.start()]
        after = text[match.end() : match.end() + _WINDOW]
        if _COVERAGE_CONTEXT.search(before) or _COVERAGE_CONTEXT.search(after):
            found.append(match.group(1))
    return found


def _documents() -> list[tuple[str, str]]:
    root = repo_root()
    documents = [
        (relative, (root / relative).read_text(encoding="utf-8"))
        for relative in DOC_PATHS
        if (root / relative).exists()
    ]
    assert len(documents) == len(DOC_PATHS), (
        f"a curated document is missing from disk: "
        f"{sorted(set(DOC_PATHS) - {name for name, _ in documents})}"
    )
    return documents


def test_the_readers_find_the_figures_they_are_meant_to_police() -> None:
    """The guard against a green run that matched nothing.

    Both assertions below are "nothing found". That is worth exactly as much as the ability
    of these two patterns to find something, so prove it on the sentence this file exists
    because of -- the real one, verbatim, as `docs/ROADMAP.md` carried it.
    """
    fossil = "7 merge-blocking gates; 143 tests, ~99% coverage, all offline."
    assert TEST_COUNT.findall(fossil) == ["143"]
    assert coverage_percentages(fossil) == ["99"]

    # And the shapes it must NOT flag: a floor, and the registry's jurisdiction coverage.
    assert TEST_COUNT.findall("pytest, coverage floor **90%**") == []
    assert coverage_percentages("Coverage is now **52 of 52 jurisdictions**") == []


def test_no_reader_facing_document_states_a_test_count() -> None:
    """A count of tests is a measurement, and it is stale one commit after it is written."""

    offenders = {
        name: TEST_COUNT.findall(text) for name, text in _documents() if TEST_COUNT.search(text)
    }
    assert not offenders, (
        f"these documents state a test count: {offenders}. The number is right until the next "
        f"commit and nothing re-derives it, so state the gate instead of the measurement -- "
        f"`make cov` prints today's figure. (docs/ROADMAP.md's own metrics table: 'the "
        f"measurement is printed by make cov, not recorded here'.)"
    )


def test_every_stated_coverage_percentage_is_the_configured_floor() -> None:
    """A floor is derived from the configuration; a measurement belongs in a run's output."""

    floor = str(_configured_floor())
    offenders = {
        name: [value for value in coverage_percentages(text) if value != floor]
        for name, text in _documents()
    }
    offenders = {name: values for name, values in offenders.items() if values}
    assert not offenders, (
        f"these documents state a coverage percentage that is not the configured {floor}% "
        f"floor: {offenders}. If it is a measurement, remove it; if the floor moved, move it "
        f"in pyproject.toml and the Makefile first and let this gate pull the docs along."
    )


def test_the_floor_this_gate_reads_is_the_floor_the_gate_set_enforces() -> None:
    """`make cov` is the coverage gate, and stage 3 of `make verify` is what runs it.

    Read from the Makefile rather than assumed, so a floor moved out of `cov` into a target
    `verify` does not call cannot leave this comparing against a number nothing enforces.
    """
    makefile = (repo_root() / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^cov:.*\n((?:\t.*\n)+)", makefile, re.M)
    assert recipe is not None, "the Makefile has no `cov` target"
    assert f"--cov-fail-under={_configured_floor()}" in recipe.group(1)
    assert re.search(r"^\t.*\$\(MAKE\).*\bcov\b", makefile, re.M), (
        "`make verify` no longer runs `cov`, so the floor this gate reads is not enforced"
    )


def test_the_curated_document_set_includes_the_readme_and_the_roadmap() -> None:
    """The two documents a reader actually opens are in scope, asserted rather than assumed.

    `DOC_PATHS` is maintained for `sentinel coverage --check-docs` and this gate borrows it.
    Borrowing means it could be narrowed elsewhere and quietly narrow this gate too.
    """
    assert {"README.md", "docs/ROADMAP.md"} <= set(DOC_PATHS), (
        f"DOC_PATHS no longer covers the README and the roadmap: {DOC_PATHS}"
    )
    assert Path(repo_root() / "docs" / "ROADMAP.md").exists()
