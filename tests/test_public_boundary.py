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
    "ACCESSIBILITY-STANDARD.md": "93b9afc0c838fad129289128a798c392e22d32f55a84407694a6d953c8681131",
    "AI-DEVELOPMENT-MEASUREMENT-STANDARD.md": "aeeb72b735d6a2703ce211911fce6f5986e2605bebe8a6cedec968b2197da478",
    "AI-EVALUATION-STANDARD.md": "6234ebd92459bedb104a00b29cc844746da9a0f947c245548f720f671eaecdb7",
    "CI-CD-STANDARD.md": "7f51b70e49bcafa37aae61485c29388ca77f36973934e3e31810db56aaaa2ea1",
    "CODE-QUALITY-STANDARD.md": "2c598e6556bc665e743729318a61fe44c6734d3c088bab72d6ded043971c1e68",
    "DATA-GOVERNANCE-STANDARD.md": "817a83581336536e80a8820bc3f32cfaee590f67572fcfeb5472813da7b51fa4",
    "DOCUMENTATION-STANDARD.md": "be36d6477e252fee5508ffcd70ecfef2b223f46df7b6dc4fb36767b39712f2e0",
    "INCIDENT-RESPONSE-STANDARD.md": "a1919dff580b94d7954cb52d34d580e611562f4922f388cc8e67f135a8487347",
    "INTERNATIONALIZATION-STANDARD.md": "9de4dc0b2fd87823aea04fd4a5a6466129799ba1911a785c27aea3e7aafd8f44",
    "OBSERVABILITY-STANDARD.md": "5df2e531e9bb53cac124c6bae14004886e9ab72509b46078c505c07a928feeff",
    "PERFORMANCE-STANDARD.md": "6bc2e635d9303b97851bedc77b65c7ad480802d5c40a5c68036d6d610ac23ecd",
    "QUALITY-AND-METRICS-STANDARD.md": "5ea624eac05af1f33de89aee403506896c6cfbe446e7b56eeb1443f2067a098d",
    "README.md": "c9d9417abd7a114a97e7dcedf499ee076d468754785d8897792a67494940c558",
    "RELEASE-AND-VERSIONING-STANDARD.md": "333367e9e9cd2951d57de018e624a32b049dd5ee8d3decacdcfedc2c9ef7afdc",
    "RESPONSIBLE-TECH-FRAMEWORK.md": "40938200c0ccaa29c05833fd911509fe1d9512236c655306c44bae5dc83e66d3",
    "SECURITY-AND-SUPPLY-CHAIN-STANDARD.md": "ff863e2ed30d726e98d308c0096f71d1ae07839939dded18246a3593760565b2",
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
    assert "standards_version=v2.0.0" in (STANDARDS / ".standards-version").read_text(
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
    workflow = (ROOT / ".github" / "workflows" / "trufflehog.yml").read_text(encoding="utf-8")
    assert "version: latest" not in workflow
    assert re.search(r'version:\s*"?3\.\d+\.\d+', workflow), "scanner runtime is not pinned"
    assert "extra_args: --only-verified" in workflow


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
