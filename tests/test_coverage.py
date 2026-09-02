"""Coverage is derived, and the docs cannot lie about it.

This follows the earlier watcher's derived-gate pattern, which derives the number of merge
gates from the Makefile and fails the build if any doc states a different one. The
principle generalises: **a self-description is a fact about the artifact, so compute it from
the artifact.** Here the artifact is `sources/registry.json` and the facts are how many
sources there are, how many jurisdictions they cover, how many holes are named, and how many
registered sources our own crawler cannot reach.

The failure this prevents is not arithmetic, it is trust. Somebody adds twenty sources; the
README still says the old number; and the most-read document in the repo is now making a
false claim about coverage to the exact organisations deciding whether to depend on us.
Nobody lied. Nobody noticed. That is how honest projects become dishonest ones.

And the invariant that matters more than any count: **every (state, core document class) pair
is either watched or a NAMED GAP.** It found DC and RI missing an entire document class each,
neither of which appeared in the hand-written gap list — they were not decisions, they were
omissions wearing the costume of decisions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.coverage import (
    BASELINE_HASHES_PATH,
    DOC_PATHS,
    CoverageReport,
    check_docs,
    committed_baseline_hashes,
    completeness_violations,
    coverage,
    repo_root,
)
from id_churn_sentinel.core.registry import Gap, Registry, Source, load_registry


@pytest.fixture
def real_registry() -> Registry:
    return load_registry()


def test_the_committed_docs_agree_with_the_committed_registry(real_registry: Registry) -> None:
    """THE GATE, on the real repo. `make sources-validate` runs the same check via the CLI."""
    report = coverage(real_registry)

    assert check_docs(report, repo_root()) == []


def test_every_unwatched_pair_is_a_named_gap(real_registry: Registry) -> None:
    """THE GATE, second half. A hole nobody named is a hole nobody knows about — and this
    project's entire claim is that its silence can be trusted to mean something."""
    assert completeness_violations(real_registry) == []


def test_the_registry_covers_every_jurisdiction(real_registry: Registry) -> None:
    report = coverage(real_registry)

    assert report.jurisdictions_covered == report.jurisdictions_total
    assert {"MI", "NH"} <= real_registry.jurisdictions


def test_a_silent_thin_spot_fails_the_check(source: Source) -> None:
    """The DC/RI case, reproduced. A jurisdiction with one document class watched and two
    unwatched, and no gap naming them, is a registry claiming coverage it does not have."""
    registry = Registry(version="1.0", sources=(source,), gaps=())

    violations = completeness_violations(registry)

    assert any("TX/birth_certificate" in v for v in violations)
    assert any("TX/court_order_name_change" in v for v in violations)
    assert all(
        "is NOT a named gap" in v or "is a named gap but IS watched" in v for v in violations
    )


def test_a_stale_gap_also_fails_the_check(source: Source) -> None:
    """The other direction, and it is not symmetric bookkeeping. A gap that claims we are
    blind to something we actually watch is a FALSE CONFESSION: a consumer reads it and goes
    looking elsewhere for information we already have."""
    stale = Gap(
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        reason="blocked-403",
        hosts=("dps.texas.gov",),
        checked="2026-07-13",
        detail="Stale: we do in fact watch this.",
    )
    registry = Registry(version="1.0", sources=(source,), gaps=(stale,))

    violations = completeness_violations(registry)

    assert any(
        f"{source.jurisdiction}/{source.document_class} is a named gap but IS watched" in v
        for v in violations
    )


def _stub_gated_docs(root: Path, *, except_for: str, text: str = "52 of 52 jurisdictions") -> None:
    """Write a satisfying stub for every document in `DOC_PATHS` except the one under test.

    Derived from `DOC_PATHS` rather than mirrored by hand. The three tests below each carried
    their own copy of the list, and two of the copies were already incomplete — they passed
    only because they assert with `any(...)` and an extra "missing from disk" drift is
    harmless to that. Adding one document to `DOC_PATHS` then failed a test that has nothing
    to say about which documents are gated. A hand-maintained copy of the list the gate reads
    is the same drift this gate exists to refuse, one level up.
    """
    for relative in DOC_PATHS:
        if relative == except_for:
            continue
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_a_doc_that_states_the_wrong_number_fails(tmp_path: Path, real_registry: Registry) -> None:
    """The drift itself. A README claiming a coverage number the registry does not support is
    caught, named, and the correct number is printed — so the fix is mechanical."""
    (tmp_path / "README.md").write_text(
        "We watch 999 sources across 3 of 52 jurisdictions, with 7 named gaps, and "
        "1 of the 2 registered sources cannot currently be fetched. "
        "140 of 152 sources are human-verified.",
        encoding="utf-8",
    )
    _stub_gated_docs(tmp_path, except_for="README.md")

    drifts = check_docs(coverage(real_registry), tmp_path)

    assert any("999 sources" in d for d in drifts)
    assert any("3 of 52 jurisdictions" in d for d in drifts)
    assert any("7 named gaps" in d for d in drifts)
    assert any("cannot currently be fetched" in d for d in drifts)
    # The burn-down is gated in exactly the same grammar, and it is the number most likely to
    # go stale in the flattering direction: a doc claiming 140 of 152 verified when the
    # registry says 0 is a doc telling a legal-aid org this list has been checked.
    assert any("140 of 152 sources are human-verified" in d for d in drifts)


def test_a_doc_that_stops_describing_coverage_at_all_also_fails(
    tmp_path: Path, real_registry: Registry
) -> None:
    """The obvious way to defeat a drift gate without lying: delete the sentence, and the
    check has nothing left to check. A doc in DOC_PATHS is there because it is supposed to
    say what we watch."""
    (tmp_path / "README.md").write_text("A tool.", encoding="utf-8")
    _stub_gated_docs(tmp_path, except_for="README.md", text="nothing to see")

    drifts = check_docs(coverage(real_registry), tmp_path)

    assert any("states no coverage numbers at all" in d for d in drifts)
    assert any("must carry all five numbers" in d for d in drifts)


def test_a_third_partys_coverage_number_is_not_our_business(
    tmp_path: Path, real_registry: Registry
) -> None:
    """The README quotes Namesake's coverage — '2 of 51 jurisdictions' — as a cited fact
    about someone else's project. A gate that 'corrected' that number to ours would be
    rewriting a citation to make our own arithmetic work, which is a worse sin than the drift
    it is preventing. Their denominator is 51; ours is 52; the gate only reads ours."""
    (tmp_path / "README.md").write_text(
        "Namesake fully supports 2 of 51 jurisdictions. We watch 156 sources across "
        "52 of 52 jurisdictions, with 8 named gaps, and 12 of the 156 registered sources "
        "cannot currently be fetched. 0 of 156 sources are human-verified.",
        encoding="utf-8",
    )
    _stub_gated_docs(tmp_path, except_for="README.md")

    assert check_docs(coverage(real_registry), tmp_path) == []


def _satisfying_readme(report: CoverageReport, tail: str = "") -> str:
    """A README that states all five gated coverage numbers correctly, derived.

    Built from the report rather than typed, for the same reason the gate exists: a test
    fixture carrying hand-written `156`s is a second copy of the number the gate is there to
    stop anyone from hand-writing, and it goes stale on the day the registry grows.
    """
    return (
        f"We watch {report.sources_total} sources across "
        f"{report.jurisdictions_covered} of {report.jurisdictions_total} jurisdictions, "
        f"with {report.gaps_total} named gaps, and {report.unreachable_total} of the "
        f"{report.sources_total} registered sources cannot currently be fetched. "
        f"{report.verified_total} of {report.sources_total} sources are human-verified. "
        f"{tail}"
    )


def _write_baseline(root: Path, count: int) -> None:
    path = root / BASELINE_HASHES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"baselines": {str(i): {"sha256": "x"} for i in range(count)}}),
        encoding="utf-8",
    )


def test_a_doc_that_misstates_the_committed_baseline_fails(
    tmp_path: Path, real_registry: Registry
) -> None:
    """The drift this half of the gate was added for. The README said `sources/baseline-
    hashes.json` retained **125** historical hashes while the file on disk held **145** —
    understating, by twenty, how much memory a fresh clone actually has. Nothing derived it,
    so nothing corrected it, and the sentence sat in the bullet that claimed every number on
    the page was derived.
    """
    report = coverage(real_registry)
    _write_baseline(tmp_path, 2)
    (tmp_path / "README.md").write_text(
        _satisfying_readme(report, "The committed baseline retains 125 historical hashes."),
        encoding="utf-8",
    )
    _stub_gated_docs(tmp_path, except_for="README.md")

    drifts = check_docs(report, tmp_path)

    assert any("125 historical hashes" in d and "retains 2" in d for d in drifts)


def test_a_correct_baseline_hash_count_passes(tmp_path: Path, real_registry: Registry) -> None:
    report = coverage(real_registry)
    _write_baseline(tmp_path, 2)
    (tmp_path / "README.md").write_text(
        _satisfying_readme(report, "The committed baseline retains 2 historical hashes."),
        encoding="utf-8",
    )
    _stub_gated_docs(tmp_path, except_for="README.md")

    assert check_docs(report, tmp_path) == []


def test_a_hash_count_nobody_can_derive_fails_rather_than_passing_quietly(
    tmp_path: Path, real_registry: Registry
) -> None:
    """No baseline file on disk. The tempting behaviour is to skip the check — and that is
    how a gate becomes decorative: delete the artifact, and the claim about it sails through.
    An unreadable file is us not knowing, which is not the same as agreement.
    """
    report = coverage(real_registry)
    (tmp_path / "README.md").write_text(
        _satisfying_readme(report, "The committed baseline retains 145 historical hashes."),
        encoding="utf-8",
    )
    _stub_gated_docs(tmp_path, except_for="README.md")

    drifts = check_docs(report, tmp_path)

    assert any("could not be read" in d and "145 historical hashes" in d for d in drifts)


def test_describing_the_baseline_file_does_not_satisfy_the_says_something_check(
    tmp_path: Path, real_registry: Registry
) -> None:
    """A doc that states a hash count and nothing else has not described our coverage. If the
    baseline phrase counted as 'says something', the escape hatch this gate closes — delete
    the coverage sentence, leave the gate nothing to check — would reopen next to it.
    """
    report = coverage(real_registry)
    _write_baseline(tmp_path, 2)
    (tmp_path / "README.md").write_text("It retains 2 historical hashes.", encoding="utf-8")
    _stub_gated_docs(tmp_path, except_for="README.md")

    drifts = check_docs(report, tmp_path)

    assert any("states no coverage numbers at all" in d for d in drifts)


def test_the_committed_baseline_count_is_derivable_from_the_real_repo() -> None:
    """The default root, on the committed artifact: the number the docs quote must come from
    somewhere real. A malformed or missing file returns None, and None is never a count."""
    assert committed_baseline_hashes() == committed_baseline_hashes(repo_root())
    count = committed_baseline_hashes()
    assert count is not None
    assert count > 0


@pytest.mark.parametrize("payload", ["[]", "{}", '{"baselines": []}', "not json at all"])
def test_a_malformed_baseline_file_is_not_read_as_a_count(tmp_path: Path, payload: str) -> None:
    path = tmp_path / BASELINE_HASHES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")

    assert committed_baseline_hashes(tmp_path) is None


def test_the_cli_prints_the_derived_numbers_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["coverage"]) == 0

    out = capsys.readouterr().out
    assert "jurisdictions:  52 of 52" in out
    assert "named gaps:     8" in out


def test_the_cli_check_docs_gate_passes_on_the_committed_repo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["coverage", "--check-docs"]) == 0

    assert "coverage --check-docs: OK" in capsys.readouterr().out


def test_the_cli_emits_machine_readable_coverage(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    assert main(["coverage", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["jurisdictions_covered"] == 52
    assert payload["named_gaps"] == 8
    assert payload["human_verified"] == 0
    assert payload["unverified"] == 156
    assert payload["rejected_by_a_human"] == 0
