"""Current-tip publication-boundary and vendored-standards contracts."""

from __future__ import annotations

import json
import re
import subprocess
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).parents[1]
STANDARDS = ROOT / "docs" / "standards"
PRIVATE_IDENTIFIERS = (
    "trans-" + "docs-" + "navigator",
    "self-" + "osint-" + "monitor",
)
EXPECTED_STANDARDS_SHA256 = {
    "ACCESSIBILITY-STANDARD.md": "97419f9804127c63b351eda49915b533a66822f4dd60010d5376cf5b72974e7c",
    "AI-DEVELOPMENT-MEASUREMENT-STANDARD.md": "b3d38529fe63cd48a1d9a63688988c128cc33862d599a415442c0a2acab34e8f",
    "AI-EVALUATION-STANDARD.md": "793ddaa85a71bd4d74ac8268c5aa1c6455cb83d92a0065526b2223120a3b81e5",
    "CI-CD-STANDARD.md": "aca1b8c8b749545b4b5d9cf9a225064ead34492303a5a4bedb02f0250a5639df",
    "CODE-QUALITY-STANDARD.md": "9f1148da61a09ac8c594564b32d9031ade872d9ae2100e046b8eac20d8a8e0d3",
    "DATA-GOVERNANCE-STANDARD.md": "16c84ab92de6c22c2897ecec5e3f28933e6f075b4db5d11195cdaec76b3a8a2f",
    "DISCOVERY-AND-ADOPTION-STANDARD.md": "6a0fc8edfda8aec551993035b70138262d76b753a6b80502ecd059a42beb5b23",
    "DOCUMENTATION-STANDARD.md": "9411f112f36be4eacc5e256194bf4d9e5fc2b8441b79e3762470d3560061af45",
    "INCIDENT-RESPONSE-STANDARD.md": "5fef4e7b5395b9117a52baddf9e0df7dbc5e04f50b893f368e6b741c10a016c8",
    "INTERNATIONALIZATION-STANDARD.md": "dcef940a7b2ce972012455c6eae6dc1eff17fde0dfa46416e2e2a31c34f59697",
    "OBSERVABILITY-STANDARD.md": "33b3d97c923dc0d9f600a99cb4de4f4fe76aa6407ec7a285eb143ce5a3830c43",
    "PERFORMANCE-STANDARD.md": "6bc2e635d9303b97851bedc77b65c7ad480802d5c40a5c68036d6d610ac23ecd",
    "QUALITY-AND-METRICS-STANDARD.md": "6fdf745a71747fbfd6dc030d20844ac3a1fa06c866e84df29952c3bfe39b8220",
    "README.md": "ad46efa909148f990741389ee2f3dfb6b95b430bf9d36c21d64a50e9658e482d",
    "RELEASE-AND-VERSIONING-STANDARD.md": "eabd48199ef6d11c93cbf6c1b07fddf3a4e0e16cd49cd2f42b3d8a4ad635c0c8",
    "RESPONSIBLE-TECH-FRAMEWORK.md": "1269b2b72c5d2d0fa6e7698434cf4ef7433a8832d5eb9371fd3d247ae0507369",
    "SECURITY-AND-SUPPLY-CHAIN-STANDARD.md": "f3aa55a3e7a3b63d2785d28abad072eb233b678f0c27cc13df5a3477ebbbb38c",
}


def _current_project_text() -> list[tuple[Path, str]]:
    """Every file that would actually reach the public repo — git-tracked content, not the
    working directory.

    A raw `ROOT.rglob("*")` walk previously scanned the working tree wholesale, which means it
    also scanned build artifacts no publication boundary applies to: `coverage.xml` embeds the
    absolute path pytest-cov ran from, and on a checkout at `/Users/<name>/...` that is a false
    "local absolute path" finding this test cannot tell apart from a real one, on a file that
    is `.gitignore`d and never committed. `git ls-files` is what a clone actually receives, so
    it is the correct universe for a test about what a reader of this public repo can see —
    and it fixes the false positive as a side effect, on any contributor's machine, not just
    one whose home directory happens to collide with the check.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 — repo-relative `git`, not attacker-controlled
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    documents: list[tuple[Path, str]] = []
    for name in tracked.split("\0"):
        if not name:
            continue
        relative = Path(name)
        if relative.parts[:2] == ("docs", "standards"):
            continue
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        documents.append((relative, text))
    return documents


def test_current_project_text_has_no_private_source_references() -> None:
    findings: list[str] = []
    private_root = "/" + "STANDARDS"
    local_root = "/users/" + "chelsea/"
    for path, text in _current_project_text():
        lowered = text.lower()
        for identifier in PRIVATE_IDENTIFIERS:
            if identifier in lowered:
                findings.append(f"{path}: private repository identifier")
        if local_root in lowered:
            findings.append(f"{path}: local absolute path")
        if private_root in text:
            findings.append(f"{path}: private standards path")
    assert not findings, "\n".join(findings)


def test_vendored_standards_projection_is_complete_and_pinned() -> None:
    manifest = json.loads((STANDARDS / ".standards-manifest.json").read_text(encoding="utf-8"))
    declared = manifest["files"]
    assert manifest["schema_version"] == 1
    assert declared == sorted(EXPECTED_STANDARDS_SHA256)
    assert set(declared) == {path.name for path in STANDARDS.glob("*.md")}
    observed = {
        name: sha256((STANDARDS / name).read_bytes()).hexdigest()
        for name in EXPECTED_STANDARDS_SHA256
    }
    assert observed == EXPECTED_STANDARDS_SHA256
    assert "standards_version=v3.0.1" in (STANDARDS / ".standards-version").read_text(
        encoding="utf-8"
    )


def test_secret_scan_pins_its_runtime_and_never_floats_to_latest() -> None:
    # The property that matters is that the scanner runtime is pinned, not which
    # pin. Two fixes for the same Lob-detector regression landed independently:
    # this branch pinned back to 3.95.8 with every detector enabled, while `main`
    # pinned forward to 3.96.0 and excluded the one false-positive detector.
    # `main`'s is the newer decision and the one in the tree, so this asserts the
    # invariant both satisfy — the action's `version` input defaults to "latest",
    # which is what silently changed the scanner underneath a SHA-pinned action.
    #
    # This used to also assert `extra_args: --only-verified`, which pinned a tier
    # selection that CANNOT FAIL on a revoked credential: TruffleHog files a
    # credential the provider has rejected under `unverified`, and revocation is
    # the normal end state of a real leak. That assertion has been replaced by the
    # detector exclusion it was really carrying — the Lob half of the same fix —
    # and the result tiers are now asserted, with the measurement behind them, in
    # tests/test_trufflehog_workflow.py.
    workflow = (ROOT / ".github" / "workflows" / "trufflehog.yml").read_text(encoding="utf-8")
    assert "version: latest" not in workflow
    assert re.search(r'version:\s*"?3\.\d+\.\d+', workflow), "scanner runtime is not pinned"
    assert "--exclude-detectors=Lob" in workflow


#: Workflows that run on `push:`, where a ref-only concurrency key silently drops a verdict.
_PUSH_TRIGGERED_WORKFLOWS = ("ci.yml", "trufflehog.yml")


def test_push_triggered_workflows_key_concurrency_per_commit_not_per_branch() -> None:
    """A commit on `main` must keep its own run; a canceled run is no verdict, not a pass.

    With the group at `${{ github.ref }}` alone, every push to `main` shared one group and
    `cancel-in-progress: true` canceled the run still working on the previous commit. Push twice
    inside one run's duration — a merge plus a follow-up, the normal shape here — and the first
    commit is verified by nothing. It never goes red, so nothing looks wrong afterwards: GitHub
    reports the run as `cancelled`, which is no signal at all rather than a failure.

    Asserted against the workflow text, the established pattern here for `.github/workflows`
    invariants (see `test_secret_scan_pins_its_runtime_and_never_floats_to_latest`), because the
    property lives in the YAML and not in any importable module.

    `codeql.yml` is deliberately not in this list: it has no `push:` trigger, so its ref-only key
    only ever groups pull-request and weekly-schedule runs, where canceling the stale run is what
    you want.
    """
    for name in _PUSH_TRIGGERED_WORKFLOWS:
        workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        group = re.search(r"^concurrency:\n(?:\s*#.*\n)*\s*group:\s*(.+)$", workflow, re.MULTILINE)
        assert group is not None, f"{name} declares no concurrency group"
        key = group.group(1)
        assert "github.sha" in key, (
            f"{name} keys concurrency on the ref alone, so a second push to main cancels the "
            f"previous commit's run and that commit gets no verdict: {key}"
        )
        assert "github.event_name == 'pull_request'" in key, (
            f"{name} must still collapse superseded pull-request runs by branch: {key}"
        )
        # The trigger this protects has to actually be present, or the assertion above is a
        # tautology that would keep passing if `push:` were ever removed.
        assert re.search(r"^on:\n(?:.*\n)*?\s*push:", workflow, re.MULTILINE), (
            f"{name} is listed as push-triggered but declares no push trigger"
        )


def test_watch_workflow_branches_on_what_it_read_not_only_on_what_it_selected() -> None:
    """The weekly job must not read an all-blind run as a quiet one.

    `attempted_count` is deliberately reachability-blind, so it proves sources were SELECTED
    and never that any was READ. A run in which every host refused to answer prints a
    non-zero denominator with every drift count at zero — byte-identical to a complete pass
    over pages that all matched — and this job branched only on those. It concluded
    `needs-review=false`, filed nothing, and went green for a run that read no page at all.

    Asserted against the workflow text, the established pattern here for `.github/workflows`
    invariants (see `test_secret_scan_pins_its_runtime_and_never_floats_to_latest`), because
    the property lives in the YAML and not in any importable module.
    """
    workflow = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")

    # The numerator is parsed, and a missing marker is loud rather than assumed to be zero.
    assert "baseline-check-observed-count" in workflow
    assert "cannot tell whether any page was actually read" in workflow
    assert "baseline-check-unreachable-count" in workflow

    # It is branched on, and the branch reaches the human-review queue.
    assert 'echo "nothing-observed=true" >> "$GITHUB_OUTPUT"' in workflow
    blind_branch = re.search(
        r'elif \[\[ "\$observed_count" -eq 0 \]\]; then\n(?:.*\n)*?\s*echo "needs-review=true"',
        workflow,
    )
    assert blind_branch, "a run that read nothing must reach the review queue"

    # And it is not allowed to be green.
    assert "steps.check.outputs.nothing-observed == 'true'" in workflow

    # The backstop: a non-zero exit no branch here explains must not default to green.
    assert "steps.check.outputs.check-status != '0'" in workflow


def test_watch_workflow_sees_a_source_that_was_compared_against_nothing() -> None:
    """A source with no committed baseline is the one bucket `observed` cannot expose.

    A blind or unreachable page is subtracted from the observation numerator, so a pass made
    entirely of them trips the all-blind refusal above. A source with no committed hash is
    not: the page was read, it counts as observed, and it was still compared against nothing.
    With no marker and no branch, a pass in which not one source had a baseline printed a
    healthy numerator, zero drift, `needs-review=false`, and went green (issue #51).
    """
    workflow = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")

    # The marker is parsed, and a missing one is loud rather than assumed to be zero.
    assert "baseline-check-unbaselined-count" in workflow
    assert "cannot tell how many sources were compared against nothing" in workflow

    # And it reaches the human-review queue, on the same branch as the other buckets that
    # were read but not compared.
    review_branch = re.search(
        r'elif \[\[ "\$moved_count" -gt 0(?:[^\n]*)"\$unbaselined_count" -gt 0 \]\]; then\n'
        r'(?:.*\n)*?\s*echo "needs-review=true"',
        workflow,
    )
    assert review_branch, "a source compared against nothing must reach the review queue"


def test_watch_workflow_never_fails_for_a_source_merely_being_down() -> None:
    """The rule the gate above must not break, pinned so a later edit cannot quietly widen it.

    An outage at a watched source is the tool WORKING. If this job goes red for one state's
    website being down, the humans learn to ignore the badge and then ignore it on the week a
    page is quietly rewritten. So the failure condition is the *observation count* being zero,
    never the unreachable count being non-zero.
    """
    workflow = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")

    failure_conditions = re.findall(r"^\s*if: (steps\.check\.outputs\..*)$", workflow, re.MULTILINE)
    gating = [c for c in failure_conditions if "needs-review" not in c]
    assert gating, "the workflow has no failure gate at all"
    for condition in gating:
        assert "unreachable-count" not in condition, (
            f"a source being unreachable must never fail the run on its own: {condition}"
        )
        assert "no-text-count" not in condition, (
            f"an unreadable page must never fail the run on its own: {condition}"
        )


def test_watch_workflow_reports_an_observation_rate_whose_remainder_it_can_name() -> None:
    """The rate the job reports must not sit next to an unexplained shortfall.

    The blind-run refusal fires at `observed == 0` and nowhere else, so a run that read 1 of
    156 sources and found it unchanged prints every drift count at zero and exits 0 —
    byte-identical to a complete pass. Issue #52 is the question of where that line belongs;
    the answer to it is a product judgment and is deliberately not made here. What *is* made
    here is that the rate is reported, and that the reader can account for the whole of it.

    `check_baselines` puts every attempted source in exactly one bucket, and `observed`
    excludes exactly the two that were not read, so

        observed + unreachable + no-text == attempted

    is an identity (pinned on the Python side by
    `tests/test_baseline.py::test_the_observation_deficit_accounts_for_every_source_attempted`).
    Naming only the unreachable count leaves the reader to complete the sentence, and the
    completion anyone reaches for is the reassuring one — "the rest were read and were fine".
    So the summary names both halves, and the step refuses to print a rate over a population
    it cannot describe rather than printing one and hoping.

    Asserted against the workflow text, the established pattern here for `.github/workflows`
    invariants (see `test_secret_scan_pins_its_runtime_and_never_floats_to_latest`), because
    the property lives in the YAML and not in any importable module.
    """
    workflow = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")

    # The rate is derived from what was read over what was attempted — never from a drift
    # count, and never hand-written.
    rate = re.search(
        r'observation_rate="\$\(awk -v o="\$observed_count" -v a="\$attempted_count"',
        workflow,
    )
    assert rate, "the observation rate is not computed from observed over attempted"

    # It reaches a human on EVERY run via the job summary, and the runs that already reach one
    # carry it in the review-queue issue body too.
    assert 'echo "observation-rate=${observation_rate}" >> "$GITHUB_OUTPUT"' in workflow
    assert '} >> "$GITHUB_STEP_SUMMARY"' in workflow
    assert "OBSERVATION_RATE: ${{ steps.check.outputs.observation-rate }}" in workflow
    assert "const observationRate = process.env.OBSERVATION_RATE;" in workflow

    # The remainder is named in full: a page that answered with nothing in it was not read,
    # exactly as a host that never answered was not.
    summary = workflow[workflow.index("### Observation rate") :]
    summary = summary[: summary.index('} >> "$GITHUB_STEP_SUMMARY"')]
    assert "${unreachable_count}" in summary, "the summary does not name the unreachable bucket"
    assert "${no_text_count}" in summary, (
        "the summary names only the unreachable half of the deficit; an unreadable page was "
        "not read either, and a remainder the reader cannot account for is read as 'fine'"
    )

    # And the identity is checked rather than assumed, loudly, like every other marker here.
    assert (
        'unaccounted_count="$(( attempted_count - observed_count '
        '- unreachable_count - no_text_count ))"'
    ) in workflow, "the observation arithmetic is not reconciled against the attempt denominator"
    reconcile = re.search(
        r'if \[\[ "\$unaccounted_count" -ne 0 \]\]; then\n(?:.*\n)*?\s*exit 1',
        workflow,
    )
    assert reconcile, "an unreconcilable observation count must fail rather than print a rate"


def test_watch_workflow_retitles_the_review_queue_issue_when_reusing_it() -> None:
    """`watch.yml` posts one of three mutually exclusive findings each run — "nothing was
    attempt-eligible", "every attempted source was unreadable", or "watched sources moved" —
    and reuses one open `review-queue` issue across runs rather than filing a new one every
    week (issue #10 has stood open since the first run). Reusing the issue without also
    recomputing its title lets an issue opened by one finding keep that title forever, even
    after a later run's comment reports a different one (issue #38): a reviewer who triages by
    title — the normal way anyone scans open issues — is told the wrong thing by the one field
    they read first. That is the same reassuring-label-persists failure the rest of this repo
    is built to refuse, applied to the one label a human actually reads.

    So the reuse branch must retitle the issue it is commenting on, with the freshly computed
    `title` for *this* run. Asserted against the workflow text, the established pattern here
    for `.github/workflows` invariants (see
    `test_secret_scan_pins_its_runtime_and_never_floats_to_latest`), because the property
    lives in the YAML and not in any importable module.
    """
    workflow = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")
    start = workflow.index("if (existing.data.length > 0) {")
    end = workflow.index("} else {", start)
    reuse_branch = workflow[start:end]

    # 1. The reuse branch retitles at all.
    update_call = re.search(r"issues\.update\(\{(?P<args>[^}]*)\}", reuse_branch, re.DOTALL)
    assert update_call, (
        "the reuse branch must retitle the existing issue with issues.update — otherwise a "
        "stale finding-type title from a past run persists silently onto a run whose finding "
        "is a different one (issue #38)"
    )
    update_args = update_call.group("args")

    # 2. With the title this run computed, not a literal. `title` is the const defined once
    #    above the branch, so the reuse path and the create path cannot drift apart.
    assert re.search(r"(^|,)\s*title\s*,", update_args), (
        "issues.update must pass the freshly computed `title` variable, not a hardcoded string"
    )

    # 3. Aimed at the issue the comment goes to. Retitling a *different* issue than the one
    #    being commented on would satisfy every assertion above and still leave the reused
    #    issue carrying its stale title, which is the whole bug.
    comment_call = re.search(r"issues\.createComment\(\{(?P<args>[^}]*)\}", reuse_branch, re.DOTALL)
    assert comment_call, "the reuse branch must still comment on the existing issue"
    target = r"issue_number:\s*existing\.data\[0\]\.number"
    assert re.search(target, update_args), "the retitle must target the reused issue"
    assert re.search(target, comment_call.group("args")), (
        "the comment and the retitle must address the same issue"
    )

    # 4. And the three findings must not share a title, or retitling could not carry the
    #    finding in the first place.
    titles = re.findall(r"^\s*(?:\?|:)?\s*'(Watch [^']+|Review queue: [^']+)'", workflow, re.M)
    assert len(titles) == 3, f"expected three distinct finding titles, found {titles}"
    assert len(set(titles)) == 3, (
        f"two findings share a title, so a retitle cannot tell them apart: {titles}"
    )


# --- live integrity sentinel: an unreadable remote is not a deploy -------------------------

LIVE_INTEGRITY = ROOT / ".github" / "workflows" / "live-integrity.yml"


def _live_integrity_shell() -> str:
    """The literal shell of the one `run:` step in `live-integrity.yml`.

    Extracted rather than retyped, so this test cannot pass against a copy of the script
    while the workflow ships something else.
    """
    lines = LIVE_INTEGRITY.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "run: |"]
    assert len(starts) == 1, f"expected exactly one run block, found {len(starts)}"
    start = starts[0]
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    body = []
    for line in lines[start + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip())) < indent:
            break
        body.append(line[indent:] if len(line) >= indent else "")
    return "\n".join(body)


def _run_live_integrity(
    tmp_path: Path, *, verify_rc: int, remote_sha: str | None, head_sha: str = "a" * 40
) -> subprocess.CompletedProcess[str]:
    """Run that shell with `git` and `python3` stubbed, so the branching is what is tested.

    `remote_sha=None` stands for the read failing outright — `git ls-remote` exiting
    non-zero with nothing on stdout, which is what a network blip, an auth failure or a
    missing ref actually looks like here.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    if remote_sha is None:
        ls_remote = 'echo "fatal: could not read from remote repository" >&2; exit 128'
    else:
        ls_remote = f'printf "%s\\trefs/heads/main\\n" "{remote_sha}"'
    git.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        f'  "rev-parse HEAD") echo "{head_sha}" ;;\n'
        f'  "ls-remote --exit-code") {ls_remote} ;;\n'
        '  *) echo "unexpected git call: $*" >&2; exit 99 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    python3 = bin_dir / "python3"
    python3.write_text(f"#!/bin/sh\nexit {verify_rc}\n", encoding="utf-8")
    python3.chmod(0o755)

    script = tmp_path / "step.sh"
    script.write_text(_live_integrity_shell(), encoding="utf-8")
    return subprocess.run(  # noqa: S603
        ["/bin/bash", str(script)],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        cwd=tmp_path,
        check=False,
    )


def test_an_unreadable_remote_never_excuses_a_live_surface_mismatch(tmp_path: Path) -> None:
    """The regression: a failed `git ls-remote` must not be read as "main moved on".

    This step is the only check in the repository that looks at the bytes a consumer
    actually receives. It excuses a mismatch in exactly one case — a newer commit deployed
    while the comparison ran — and establishes that case by re-reading the remote. But an
    unread remote leaves `remote_sha` empty, and the empty string is unequal to every real
    sha, so "we never found out where main is" took the same branch as "main moved" and
    exited 0. A network blip could turn a genuinely stale live feed green, which is this
    project's primary failure mode wearing a different hat: absence of evidence rendered as
    evidence of absence.
    """
    result = _run_live_integrity(tmp_path, verify_rc=1, remote_sha=None)
    assert result.returncode == 1, (
        "a live-surface mismatch must survive a remote we could not read — an unknown "
        f"excuses nothing (stdout: {result.stdout!r}, stderr: {result.stderr!r})"
    )
    assert "could not read origin/main" in result.stdout, (
        "the unreadable remote must be said out loud, not silently folded into the result"
    )


def test_a_real_deploy_race_is_still_excused(tmp_path: Path) -> None:
    """The rule the fix must not break: a newer commit deploying mid-run is the deploy
    working, and must not page anyone. Pinned in both directions so a later tightening
    cannot quietly turn an ordinary deploy into a red daily job."""
    result = _run_live_integrity(tmp_path, verify_rc=1, remote_sha="b" * 40)
    assert result.returncode == 0, "a genuine deploy race must still be excused"
    assert "main moved to" in result.stdout


def test_a_mismatch_on_an_unmoved_main_is_reported(tmp_path: Path) -> None:
    """And the ordinary failing case: main is exactly where this checkout thinks it is, so
    a mismatch is a real one and the step must go red."""
    result = _run_live_integrity(tmp_path, verify_rc=1, remote_sha="a" * 40)
    assert result.returncode == 1


def test_a_matching_live_surface_stays_green_even_if_the_remote_is_unreadable(
    tmp_path: Path,
) -> None:
    """The fix must not invent a failure either: when the comparison itself passed there is
    nothing to excuse and nothing to report, whatever the remote read did."""
    result = _run_live_integrity(tmp_path, verify_rc=0, remote_sha=None)
    assert result.returncode == 0
