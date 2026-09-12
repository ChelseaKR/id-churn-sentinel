"""What this repository publishes about its own releases, held to the tags it has.

Nothing here read the repository's tags before this module existed, so both
directions of one fact were unguarded. A branch could delete every sentence
saying no version has been cut and merge green with the tag list still empty,
and a tag could be pushed with all of those sentences left standing. Both are
false published claims, and a suite that reads only the working tree cannot see
either, because the fact they describe does not live in the tree.

Two independent halves answer that, deliberately, because a denylist alone is
not a guarantee:

* **the structural half** compares values against the tag list and needs no
  vocabulary at all — `CITATION.cff`'s release date, the manifest's declared
  version, and the changelog section that has to exist for a version something
  carries;
* **the prose half** is a denylist, and its one honest guarantee is stated in
  the comment above the vocabulary: it finds a phrasing this project has
  already written, and it cannot find one nobody has thought of yet. It exists
  because the same fact is *also* restated in prose, in four tracked files, and
  prose is where a sentence about a release outlives the release.

The scan is applied over `git ls-files` rather than to a list of filenames.
Three documents restate the fact today and each was written against the code
rather than against the repository; a fourth is a comment at the top of a
workflow, which is exactly the kind of place a hand-maintained list of three
names never reaches.

This module states no present-tense fact about what has or has not been tagged
here, and `test_the_claim_vocabulary_is_real_and_not_self_matching` holds every
one of its docstrings to that rather than to this sentence. Describe the rule;
let the checks describe the day.

A missing tag and an unfetched tag are indistinguishable from inside a
checkout, so nothing below draws a conclusion from a checkout that could not
have shown a tag. `_require_readable_tags` splits that three ways instead of
two: a tree with no git in it at all is not a repository and is skipped; a
repository whose clone was made shallow or with `--no-tags` is **refused**,
because there the answer is recoverable and reading the empty list as evidence
is absence rendered as a value; and only a repository that could have shown a
tag is measured. `test_the_workflows_that_run_this_gate_fetch_the_tags_it_reads`
is the other half of that: `actions/checkout` fetches one commit and no tags by
default, so without it these checks would land in the skip on the one run that
gates a merge.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CITATION = ROOT / "CITATION.cff"
README = ROOT / "README.md"
SECURITY = ROOT / "SECURITY.md"
CHANGELOG = ROOT / "CHANGELOG.md"
WORKFLOWS = ROOT / ".github" / "workflows"

#: A release tag for this project, with or without the `v` the workflow requires.
RELEASE_TAG = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+.].+)?)$")

#: `date-released:` matched line by line rather than by a YAML parse, so that a
#: field commented out with an explanation stays commented out. The file
#: explains itself in comments and a parser would silently read past them.
CITATION_DATE = re.compile(r"^date-released:\s*\"?(\d{4}-\d{2}-\d{2})\"?\s*$", re.MULTILINE)

#: The two sentences pinned in both directions: required for exactly as long as
#: no tag carries the declared version, and refused once one does. They are the
#: sentences a reader forms an impression from — the conformance table a
#: reviewer reads, and the supported-versions section a security reporter reads
#: — and each is pinned in the file it is written in so that removing it while
#: nothing is tagged fails loudly rather than quietly.
#:
#: The README's carries three words more than it needs to. `no tag yet` alone is
#: a fragment, and a fragment cannot tell an assertion from the subordinate
#: clause of a rule — `while there is no tag yet, this asserts nothing` is a
#: sentence this repository is very likely to write, and it would redden the
#: gate on prose that is correct on the one day this must not cry wolf.
README_SAYS_UNTAGGED = "Pre-1.0, no tag yet"
SECURITY_SAYS_UNTAGGED = "there is no tagged release yet"


def _git(*args: str) -> str | None:
    """Run git in the checkout. `None` means the answer is unavailable."""
    executable = shutil.which("git")
    if executable is None:  # pragma: no cover - git absent; every caller skips first
        return None
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, resolved path, no shell
            [executable, "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError:  # pragma: no cover - git present but unusable
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def _not_a_repository() -> str | None:
    """Why this tree holds no tags to read at all, or `None` if it is a repository.

    An installed or exported tree is not a repository and never was one, so it
    has no tag list to be wrong about. That is the only state in which an
    unanswerable question is not a defect in the checkout.
    """
    if not (ROOT / ".git").exists():
        return f"no .git in {ROOT}: an exported tree carries no tags to read"
    if shutil.which("git") is None:  # pragma: no cover - git is present wherever this runs
        return "no git executable on PATH"
    if _git("rev-parse", "--is-inside-work-tree") != "true":  # pragma: no cover - .git but no tree
        return "not a git work tree"
    return None


def _why_the_tag_list_would_prove_nothing() -> str | None:
    """Why an empty tag list in this repository would not be evidence, or `None`.

    Both reads answer "could this checkout have shown me a tag?", which is the
    question an empty list cannot answer for itself. A shallow clone and a
    `--no-tags` clone both report no tags over a repository that has them.
    """
    if _git("rev-parse", "--is-shallow-repository") == "true":
        return (
            "shallow checkout: tags were not fetched, so an empty tag list is not evidence. "
            "Run `git fetch --unshallow --tags`, or check out with fetch-depth: 0 and "
            "fetch-tags: true"
        )
    if (_git("config", "--get", "remote.origin.tagOpt") or "") == "--no-tags":
        return (
            "clone configured with tagOpt=--no-tags, so tags were never fetched and an empty "
            "tag list is not evidence. Run `git fetch --tags`"
        )
    return None


def _release_tags() -> list[str]:
    """Release tags in this repository, newest first."""
    listed = _git("tag", "--list", "--sort=-v:refname") or ""
    return [tag for tag in listed.splitlines() if RELEASE_TAG.match(tag.strip())]


def _require_readable_tags() -> list[str]:
    """The tag list, or a skip where there is no repository and a failure where there is.

    The distinction is the whole point of reading the two git config answers
    rather than the tag list alone. "I could not ask" and "the answer is none"
    are different sentences, and only one of them is recoverable by the person
    running this.
    """
    if (missing := _not_a_repository()) is not None:
        pytest.skip(f"not a repository, so it holds no tags to be wrong about: {missing}")
    unreadable = _why_the_tag_list_would_prove_nothing()
    assert unreadable is None, (
        f"this is a repository, and its tag list cannot be trusted: {unreadable}. These checks "
        "refuse rather than skip here: skipping would report the same clean result over a "
        "checkout that was never able to answer as over one whose answer is genuinely none."
    )
    return _release_tags()


def _tag_version(tag: str) -> str:
    matched = RELEASE_TAG.match(tag)
    assert matched is not None, tag
    return matched.group(1)


def _manifest_version() -> str:
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    version = manifest["project"]["version"]
    assert isinstance(version, str)
    return version


# ---- the structural half: values compared against the tag list ----------------------


def test_the_citation_file_dates_a_release_exactly_while_one_exists() -> None:
    """`date-released` names a day something was released, so it needs a release.

    This repository's own `docs/standards/DOCUMENTATION-STANDARD.md` states the
    rule in terms — omit the release-specific fields before the first tag — and
    nothing enforced it, which is how the field came to carry a date for a
    version no artifact has ever carried. Held in both directions, because the
    other half of the same mistake is a first release whose citation still has
    no date on it.
    """
    tags = _require_readable_tags()
    dated = CITATION_DATE.findall(CITATION.read_text(encoding="utf-8"))

    if not tags:
        assert not dated, (
            f"CITATION.cff carries date-released {dated[0]!r} and this repository has no tag, "
            "so it dates a release that was never cut. The project's own documentation "
            "standard says to omit the release-specific fields until the first tag."
        )
        return

    assert dated, f"CITATION.cff carries no date-released and {tags[0]} exists"


def test_the_declared_version_is_carried_by_a_tag_or_the_documents_say_it_is_not() -> None:
    """A declared version no artifact carries is fine. Not saying so is not.

    `pyproject.toml` is the one place the number is written down. While nothing
    carries it, the two documents pinned here have to keep saying so; once
    something does, one of the tags has to be it.
    """
    tags = _require_readable_tags()
    declared = _manifest_version()

    if not tags:
        assert README_SAYS_UNTAGGED in README.read_text(encoding="utf-8"), (
            f"pyproject.toml declares {declared}, no tag exists, so nothing carries that "
            f"version — and README.md no longer says so ({README_SAYS_UNTAGGED!r} is gone). "
            "Either restore the sentence or push the tag."
        )
        assert SECURITY_SAYS_UNTAGGED in SECURITY.read_text(encoding="utf-8"), (
            f"SECURITY.md no longer says {SECURITY_SAYS_UNTAGGED!r}, and no tag exists. A "
            "reporter reads that section to learn which versions get a fix."
        )
        return

    newest = tags[0]
    assert declared in {_tag_version(tag) for tag in tags}, (
        f"pyproject.toml declares {declared} and no tag carries it. Newest tag: {newest}. "
        f"Tags: {', '.join(tags)}. Either the declared version is unreleased — in which case "
        "the documents have to say so — or the tag is missing."
    )


def test_the_changelog_records_the_declared_version_once_a_tag_carries_it() -> None:
    """A tagged version with no changelog section is a release with no record of its changes.

    While nothing is tagged this asserts nothing, deliberately: pre-writing a
    dated heading for a release that has not happened is the same defect in a
    different file. `release.yml` runs the equivalent check at the tagged
    commit; this one runs before the tag, where it can still be fixed by a
    commit rather than by deleting a tag.
    """
    tags = _require_readable_tags()
    if not tags:
        return
    declared = _manifest_version()
    heading = f"## [{declared}]"
    assert heading in CHANGELOG.read_text(encoding="utf-8"), (
        f"{tags[0]} exists and CHANGELOG.md has no {heading!r} section"
    )


# ---- the prose half: a denylist, and the floors that keep it from going vacuous -----


#: Sentences in this tree that assert, in the present tense, that nothing here
#: has been tagged. Every entry is verbatim from a tracked file, and
#: `test_every_claim_in_the_vocabulary_is_a_sentence_this_repository_wrote`
#: holds that to a measurement rather than to this comment — an entry borrowed
#: from a sibling project reads as coverage while covering nothing, which is how
#: the same check in another repository here shipped eight entries covering six.
#:
#: An entry has to be a *sentence* rather than a fragment. This tree writes
#: "never run" and "could never run" nine times and every one of them is about
#: an Actions-driven Pages deploy under a spending limit, not about a release;
#: a denylist cannot tell two subjects apart, so a fragment that short reddens
#: the gate on prose that is correct.
#:
#: This is a DENYLIST, and its one guarantee is the whole of what it claims: it
#: finds a phrasing somebody has already written here, and it cannot find one
#: nobody has thought of yet. The structural half above needs no vocabulary and
#: is where the real guarantee lives. This exists because the same fact is also
#: stated in prose, in four files, only three of which any hand-maintained list
#: would have named.
CLAIMS_OF_NO_RELEASE: tuple[str, ...] = (
    README_SAYS_UNTAGGED,
    SECURITY_SAYS_UNTAGGED,
    "everything below has landed on `main` untagged",
    "dormant until the first tag",
    "tag — none exists yet",
)

#: Suffixes worth reading. A lockfile, a captured fixture, a feed or a schema
#: does not carry a sentence a reader takes a fact from.
PROSE_SUFFIXES = frozenset({".md", ".cff", ".py", ".toml", ".yml", ".yaml", ".txt"})

#: `CHANGELOG.md` is exempt as a *file*: its sections are the record of what was
#: true on the day of each entry rather than a claim about today, and rewriting
#: a shipped section so a past sentence reads true now would destroy the record
#: this check exists to protect. It stays in the *observation* universe below,
#: because a phrasing recorded in the changelog is still a phrasing this project
#: wrote — otherwise correcting a sentence out of the tree would retire the
#: entry that caught it.
CLAIM_SCAN_EXEMPT = frozenset({"CHANGELOG.md"})

THIS_FILE = Path(__file__).resolve()

#: This module is exempt as a file too — the tuple above puts every entry in it
#: verbatim, so scanning the file would make every entry vouch for itself,
#: including a borrowed one. Its **docstrings** are read instead, all of them.
#: `__doc__` alone would be one paragraph of the file's prose and the rest would
#: be text no reader and no check ever opens, which is the exact place this
#: whole module says a stale sentence hides. The floor is here because an empty
#: list is what a parse that stopped finding the file returns, and an empty list
#: passes every check downstream.
MIN_DOCSTRINGS_IN_THIS_MODULE = 15

#: The floor under the file scan. 131 tracked files carry prose today; a scan
#: that has stopped finding the tree reports the same clean result as one that
#: read all of them.
MIN_PROSE_FILES = 100

#: Markdown and YAML wrap prose, and a wrapped claim is invisible to a plain
#: substring match: the release workflow's header states this fact across a line
#: break behind `#` comment markers, and only normalising finds it.
_LINE_MARKERS = re.compile(r"^\s*(?:[>#*\-]|//)*\s*", re.MULTILINE)


def _normalized(text: str) -> str:
    """Line markers stripped and whitespace collapsed, so a wrap cannot hide a claim."""
    return re.sub(r"\s+", " ", _LINE_MARKERS.sub(" ", text)).lower()


def _claims_in(text: str) -> list[str]:
    normalized = _normalized(text)
    return [claim for claim in CLAIMS_OF_NO_RELEASE if claim.lower() in normalized]


def _tracked_prose_files() -> list[Path]:
    """Every tracked file whose text a reader could take a fact from.

    `git ls-files` rather than a directory walk, for the reason
    `tests/test_public_boundary.py` gives: a raw walk reads build artifacts and
    reports findings about files nobody ships. The cost is that an unstaged new
    file is invisible here, so stage before trusting a local run.
    """
    listed = _git("ls-files", "-z")
    if listed is None:  # pragma: no cover - git unusable; every caller skips first
        return []
    paths: list[Path] = []
    for name in listed.split("\0"):
        if not name or name in CLAIM_SCAN_EXEMPT:
            continue
        path = ROOT / name
        if path.suffix in PROSE_SUFFIXES and path.is_file():
            paths.append(path)
    return paths


def _own_docstrings() -> list[str]:
    """Every docstring in this module: the prose of the one file the scan skips.

    Parsed from the source on disk rather than walked on the module object, so
    that this measures the same bytes the scan around it measures.
    """
    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            text = ast.get_docstring(node, clean=False)
            if text is not None:
                found.append(text)
    return found


def _texts_of(path: Path) -> list[str]:
    if path.resolve() == THIS_FILE:
        return _own_docstrings()
    return [path.read_text(encoding="utf-8")]


def _stale_claims(tags: list[str]) -> list[str]:
    """Tracked prose still saying nothing was tagged, given the tags that exist.

    Taking the tag list as an argument rather than reading it is what makes this
    measurable before a first tag exists. With none, the live call returns an
    empty list — which is also what a scan that has stopped finding the tree
    returns. Passing a tag in exercises the same code over the same files
    without creating one.
    """
    if not tags:
        return []
    stale: list[str] = []
    for path in _tracked_prose_files():
        for text in _texts_of(path):
            stale.extend(f"{path.relative_to(ROOT)}: {claim!r}" for claim in _claims_in(text))
    return stale


def _where_each_claim_is_written() -> dict[str, list[str]]:
    """Where each vocabulary entry is actually written, over the tracked tree.

    The exempt names are added back here: this asks what the project has said,
    not what it says today, and those are different questions asked of the same
    files.
    """
    where: dict[str, list[str]] = {claim: [] for claim in CLAIMS_OF_NO_RELEASE}
    universe = [*_tracked_prose_files(), *(ROOT / name for name in sorted(CLAIM_SCAN_EXEMPT))]
    for path in universe:
        if not path.is_file():  # pragma: no cover - an exempt name that is not in the tree
            continue
        for text in _texts_of(path):
            for claim in _claims_in(text):
                where[claim].append(str(path.relative_to(ROOT)))
    return where


def test_the_claim_vocabulary_is_real_and_not_self_matching() -> None:
    """The floor under the two scans below, and the reason this file may read itself.

    Four ways they could pass while examining nothing: an empty vocabulary, a
    scan that has stopped finding the tree, a normalisation that has stopped
    collapsing wrapped prose, and this module quietly exempting itself from a
    rule it applies to every other file. The two pinned sentences are in the
    vocabulary by construction, and none of the entries appears in any docstring
    here — which is what makes reading those docstrings a measurement rather
    than a way of not reading this file at all.
    """
    assert CLAIMS_OF_NO_RELEASE, "an empty vocabulary scans every file and finds nothing"
    for pinned in (README_SAYS_UNTAGGED, SECURITY_SAYS_UNTAGGED):
        assert pinned in CLAIMS_OF_NO_RELEASE, (
            f"the vocabulary does not cover {pinned!r}, one of the two sentences already "
            "pinned in both directions above, so it generalises nothing"
        )

    own = _own_docstrings()
    assert len(own) >= MIN_DOCSTRINGS_IN_THIS_MODULE, (
        f"only {len(own)} docstring(s) parsed out of this module: the walk has stopped reading "
        "the file, and no docstring is what it returns either way"
    )
    for text in own:
        found = _claims_in(text)
        assert not found, (
            f"a docstring in this module asserts, in the present tense, that nothing here has "
            f"been tagged: {found}. This file is exempt as a file, so nothing else will ever "
            "read it, and a docstring is the one piece of prose in a Python project nobody "
            f"opens. Describe the rule, not the day.\n{' '.join(text.split())[:400]}"
        )

    assert _claims_in("# so this is wired but dormant\n# until the first tag."), (
        "a claim wrapped across two commented lines is not found, so the normalisation these "
        "scans depend on has stopped working and every wrapped sentence is invisible to them"
    )
    assert not _claims_in("while there is no tag yet, this check asserts nothing"), (
        "an entry is matching a subordinate clause stating the rule rather than an assertion "
        "about today. A denylist cannot tell the two apart, so the entry is too short: it will "
        "redden this gate on correct prose on the day a tag is cut, which is the one day it "
        "must not cry wolf."
    )


def test_every_claim_in_the_vocabulary_is_a_sentence_this_repository_wrote() -> None:
    """A denylist entry that matches nothing is coverage that is not there.

    An entry costs nothing to read and makes the tuple look longer than its
    reach, so the wording has to be observed somewhere in the tracked tree or it
    stops earning its place. That is the self-limiting refusal an exemption list
    wants and rarely has: the check fails until somebody deletes the entry or
    corrects the wording it was aimed at.

    This paragraph quotes none of the entries, and that is not fussiness — a
    gate that forbids a wording fires on the sentence explaining why the wording
    is forbidden, and the docstring check above is what catches it.
    """
    _require_readable_tags()
    where = _where_each_claim_is_written()
    unobserved = sorted(claim for claim, files in where.items() if not files)
    assert not unobserved, (
        f"these vocabulary entries appear nowhere in this repository: {unobserved}. An entry "
        "that matches nothing is not coverage; it reads as coverage. Either the sentence has "
        "been corrected — delete the entry — or this wording is not the tree's wording, in "
        "which case the real sentence is going unwatched."
    )


def test_while_nothing_carries_the_declared_version_the_documents_still_say_so() -> None:
    """The direction a scan for stale sentences cannot cover.

    A branch that deletes every sentence saying nothing has been tagged, while
    nothing has been tagged, publishes that a release exists. Nothing about that
    branch is detectable by looking for sentences: the defect is that they are
    gone. So the two pinned sentences are required here, and the count of files
    stating the fact has a floor under it, which is what stops a rewrite from
    quietly reducing four statements to one.
    """
    tags = _require_readable_tags()
    if tags:
        return
    where = _where_each_claim_is_written()
    files = {name for names in where.values() for name in names}
    assert "README.md" in files and "SECURITY.md" in files, (
        f"no tag carries the declared version and the two pinned documents no longer say so. "
        f"Files still stating it: {sorted(files)}"
    )
    assert len(files) >= 3, (
        f"no tag exists and only {len(files)} tracked file(s) still say so: {sorted(files)}. "
        "Either the sentences were removed ahead of a tag that does not exist yet, or the "
        "vocabulary has stopped matching the wording they use."
    )


def test_no_document_says_this_repository_is_untagged_once_it_is() -> None:
    """A sentence saying nothing was tagged has to go when something is.

    The two pinned sentences are held in both directions in one file each. This
    is the same rule applied to every tracked prose file, so that a first tag
    cannot leave three other documents asserting it does not exist — including
    the workflow header that no list of document names would have covered.
    """
    tags = _require_readable_tags()
    scanned = _tracked_prose_files()
    assert len(scanned) > MIN_PROSE_FILES, (
        f"only {len(scanned)} tracked prose file(s) to read: this scan has stopped finding the "
        "tree, and a scan that reads nothing reports the same clean result as one that read "
        "everything"
    )
    stale = _stale_claims(tags)
    assert not stale, (
        f"{tags[0]} exists, and these still say nothing here has ever been tagged: "
        f"{'; '.join(stale)}. Tags carried: {', '.join(tags)}. Either the sentences are stale "
        "or the tag should not be there."
    )


def test_the_scan_names_every_file_a_first_tag_would_make_stale() -> None:
    """The measurement the live check above cannot make while nothing is tagged.

    With no tag, `_stale_claims` returns an empty list for the same reason a
    scan of an empty file list would, so a green run above is not evidence that
    the scan works. Running it over this tree with a tag supplied is: it reads
    the real tracked files and reports what the first real tag will make false.

    The assertions are a floor rather than a pinned inventory — naming an exact
    set here would be a hand-maintained list gated on equality, which jams every
    branch that adds a document. What has to hold is that the scan reaches the
    tree, finds the sentence already pinned in the README, and finds the fact in
    several files, because "it is stated in more places than the rule was
    applied to" is the entire finding.
    """
    _require_readable_tags()
    stale = _stale_claims(["v0.0.0-not-a-tag-in-this-repository"])
    files = {entry.split(":", 1)[0] for entry in stale}
    assert "README.md" in files, (
        "the scan does not reach README.md, whose sentence is pinned in both directions above "
        "— so it is reading something other than the working tree"
    )
    assert ".github/workflows/release.yml" in files, (
        "the scan does not reach the release workflow's header, which states this fact in a "
        "comment across a line break. That file is the reason the scan reads `git ls-files` "
        f"and normalises rather than taking a list of document names. Files found: {sorted(files)}"
    )
    assert len(files) > 2, (
        "the scan finds this fact in fewer files than the tree states it in. Either the "
        "documents restating it have been corrected — in which case narrow this check and say "
        f"so — or the scan is no longer reading them. Files found: {sorted(files)}"
    )


# ---- the checkout the gate runs in ---------------------------------------------------


def _jobs(workflow: str) -> dict[str, str]:
    """Split a workflow into its jobs. Cheaper and more robust here than a YAML parse.

    PyYAML implements YAML 1.1, where a bare `on:` key loads as the boolean
    `True` — a reader that asks a workflow mapping for `"on"` gets nothing over
    a file that plainly declares triggers. The established pattern in this
    repository for workflow invariants is a text match, and this keeps to it.
    """
    lines = workflow.splitlines()
    try:
        first = next(index for index, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:  # pragma: no cover - a workflow with no jobs
        return {}
    starts = [
        (index, matched.group(1))
        for index in range(first + 1, len(lines))
        if (matched := re.match(r"^  ([A-Za-z0-9_-]+):\s*$", lines[index])) is not None
    ]
    bounds = [*[index for index, _ in starts], len(lines)]
    return {name: "\n".join(lines[bounds[n] : bounds[n + 1]]) for n, (_, name) in enumerate(starts)}


def _runs(body: str, command: str) -> bool:
    """Does this job run the command, rather than mention it in a comment?

    Four conformance checks across this portfolio once passed because a tool was
    named in a comment. Comments are prose; a `run:` line is the job.
    """
    return any(command in line for line in body.splitlines() if not line.lstrip().startswith("#"))


def test_the_workflows_that_run_this_gate_fetch_the_tags_it_reads() -> None:
    """Otherwise every check above refuses in CI, on the run that gates a merge.

    `actions/checkout` fetches one commit and no tags by default, which is the
    shape `_require_readable_tags` refuses to draw a conclusion from. Both
    workflows that run the gate have to ask for them — the release one most of
    all, because it runs at a tagged commit and a checkout that cannot see that
    tag would take the whole file down the untagged branch of every check here.
    """
    gate = "make verify"
    checked = 0
    for name in ("ci.yml", "release.yml"):
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        running = {job: body for job, body in _jobs(workflow).items() if _runs(body, gate)}
        assert running, f".github/workflows/{name} has no job that runs {gate!r}"
        for job, body in running.items():
            assert "actions/checkout" in body, f"{name}: job {job!r} runs the gate with no checkout"
            assert "fetch-depth: 0" in body, (
                f"{name}: job {job!r} checks out shallow, so tests/test_release_claims.py "
                "refuses there rather than measuring anything"
            )
            assert "fetch-tags: true" in body, (
                f"{name}: job {job!r} does not fetch tags, so tests/test_release_claims.py "
                "refuses there rather than measuring anything"
            )
            checked += 1
    assert checked >= 2, f"only {checked} job(s) matched: the job split has stopped working"


def test_an_unanswerable_tag_list_is_refused_and_a_missing_repository_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is unreachable from a healthy checkout, so it is driven directly.

    Every run of this suite happens in a full clone, so nothing above ever
    executes the branch that separates "I could not ask" from "the answer is
    none" — and an unreachable guard reads as a guard in review while being one
    that can never fire. Both git reads are driven here, and the skip path is
    asserted to still be a skip, because turning a legitimately tagless export
    into a failure would make the gate cry wolf on every consumer who unpacks a
    source archive.
    """
    answers = {
        ("rev-parse", "--is-inside-work-tree"): "true",
        ("rev-parse", "--is-shallow-repository"): "false",
        ("config", "--get", "remote.origin.tagOpt"): "",
    }

    def fake_git(*args: str) -> str | None:
        return answers.get(args)

    monkeypatch.setattr(f"{__name__}._git", fake_git)

    assert _why_the_tag_list_would_prove_nothing() is None

    answers[("rev-parse", "--is-shallow-repository")] = "true"
    shallow = _why_the_tag_list_would_prove_nothing()
    assert shallow is not None and "shallow" in shallow

    answers[("rev-parse", "--is-shallow-repository")] = "false"
    answers[("config", "--get", "remote.origin.tagOpt")] = "--no-tags"
    no_tags = _why_the_tag_list_would_prove_nothing()
    assert no_tags is not None and "--no-tags" in no_tags

    monkeypatch.setattr(f"{__name__}.ROOT", ROOT / "not-a-checkout")
    assert _not_a_repository() is not None
    with pytest.raises(BaseException, match="not a repository"):
        _require_readable_tags()
