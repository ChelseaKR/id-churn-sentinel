"""The consumer-side GitHub Action: a subscription with no subscriber list.

`consumer-action/` runs in a *consumer's* repository. It fetches the published
`changes.json`, compares it with a state file the consumer commits, and opens one
issue per newly confirmed change in their scope. Nothing reaches this project:
no account, no callback, no telemetry. That is the design constraint that ruled
out email, solved.

Four properties are held here rather than in the action's prose.

**Gate 6 is re-applied at the consumer's edge, and cannot drift.** The action is
stdlib-only and standalone, so it carries its own copy of the publishability
predicate. `test_the_wire_predicate_agrees_with_the_publisher_over_a_matrix`
pins that copy against `ChangeRecord.publishable` across every combination that
matters, so the duplicate cannot quietly diverge from the thing it duplicates.

**A failed fetch is a failure, not an empty feed.** An HTTP error page, a
redirect to a login screen, or a truncated file all parse into "nothing changed
this week" if you let them — and silence in this feed is the one thing that must
never be produced by accident.

**A typo in the mapping file fails the run.** A jurisdiction that matches nothing
looks exactly like a quiet week. The vocabulary is read out of the fetched
document's own inventory, so it cannot drift; a document carrying no inventory
says *that*, rather than reporting every jurisdiction as unknown and sending the
reader to fix the wrong file.

**Every in-scope change gets exactly one action from a closed vocabulary.** Not
"the ones we decided to mention": `open`, `comment` or `unchanged`, asserted as
a partition, so a change that stops being planned for cannot be mistaken for one
that was deliberately left alone.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.core.changes import (
    ChangeRecord,
    IndependentReviewStatus,
    ReviewStatus,
    Significance,
)
from id_churn_sentinel.core.publish import changes_json
from id_churn_sentinel.core.registry import Registry, Source

ACTION_DIR = Path(__file__).resolve().parents[1] / "consumer-action"
SCRIPT = ACTION_DIR / "raise_issues.py"
ACTION_YML = ACTION_DIR / "action.yml"
MAP_SCHEMA = (
    Path(__file__).resolve().parents[1] / "docs" / "schema" / "consumer-watch-map-v1.schema.json"
)

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _load_action() -> Any:
    """Import the action's script by path. It is not a package, by design."""
    spec = importlib.util.spec_from_file_location("sentinel_consumer_action", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: `dataclasses` resolves annotations through
    # `sys.modules[cls.__module__]`, and a module that is not there yet fails to
    # build its own frozen dataclasses.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


action = _load_action()


# ---------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic_runner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient GitHub Actions variable may decide what this suite proves.

    `--repository` falls back to `$GITHUB_REPOSITORY` and the API call reads
    `$GITHUB_TOKEN`, both of which a GitHub-hosted runner sets for every job.
    Without this fixture the refusal test below passes on a laptop and fails in
    CI — which is how it was found — and, worse, the tests that DO supply a
    repository would silently be proving nothing about the fallback. The
    fallback gets its own test, with the variable set on purpose.
    """
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def _confirmed(record: ChangeRecord, *, significance: Significance) -> ChangeRecord:
    first = record.reviewed_by(
        reviewer="Chelsea Kelly-Reif",
        significance=significance,
        status=ReviewStatus.CONFIRMED,
        public_copy="The requirements section changed.",
    )
    if significance is Significance.EDITORIAL:
        return first
    return first.independently_reviewed_by(
        reviewer="Synthetic Independent Reviewer",
        status=IndependentReviewStatus.CONFIRMED,
        qualification_ref="tests/evidence/synthetic-independent-qualification.json",
        conflict_attestation_ref="tests/evidence/synthetic-independent-conflict.json",
    )


def _observed(source: Source, *, previous: str, new: str) -> ChangeRecord:
    return ChangeRecord.observed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        previous_hash=previous,
        new_hash=new,
        diff_excerpt="-a court order is not required\n+a court order is required",
        observed_at=NOW,
    )


@pytest.fixture
def texas_change(source: Source) -> ChangeRecord:
    return _confirmed(
        _observed(source, previous="a" * 64, new="b" * 64), significance=Significance.SUBSTANTIVE
    )


@pytest.fixture
def arizona_change(arizona_source: Source) -> ChangeRecord:
    return _confirmed(
        _observed(arizona_source, previous="c" * 64, new="d" * 64),
        significance=Significance.EDITORIAL,
    )


@pytest.fixture
def document(
    registry: Registry, texas_change: ChangeRecord, arizona_change: ChangeRecord
) -> dict[str, Any]:
    """The bytes `sentinel publish` actually writes, not a hand-shaped stand-in."""
    payload: dict[str, Any] = json.loads(
        changes_json(
            [texas_change, arizona_change],
            feed_url="https://example.invalid/",
            generated_at=NOW,
            registry=registry,
            eligibility_as_of=NOW.date(),
        )
    )
    return payload


def _withdraw(record: ChangeRecord) -> ChangeRecord:
    """Withdraw a record at a time the lifecycle rules will accept (never before review)."""
    latest = record.independent_reviewed_at or record.reviewed_at
    assert latest is not None
    return record.withdrawn_by(
        actor="Chelsea Kelly-Reif",
        reason="privacy_or_safety",
        decided_at=latest + timedelta(seconds=1),
    )


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def texas_map(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "map.json",
        {
            "schema_version": "1.0",
            "consumer": "Example legal-aid clinic",
            "jurisdictions": ["TX"],
            "document_classes": ["drivers_license"],
        },
    )


class RecordingGitHub:
    """A stand-in for the GitHub REST API that records what it was asked to do."""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail_after = fail_after
        self._next_number = 100

    def __call__(self, method: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise action.ActionError("GitHub refused POST: HTTP 403")
        self.calls.append((method, url, payload))
        self._next_number += 1
        return {"number": self._next_number}

    @property
    def opened(self) -> list[dict[str, Any]]:
        return [payload for method, url, payload in self.calls if url.endswith("/issues")]

    @property
    def comments(self) -> list[dict[str, Any]]:
        return [payload for method, url, payload in self.calls if url.endswith("/comments")]


def _run(
    tmp_path: Path,
    map_path: Path,
    document: dict[str, Any],
    *,
    github: RecordingGitHub | None = None,
    extra: list[str] | None = None,
) -> tuple[int, RecordingGitHub, Path]:
    changes = _write(tmp_path / "changes.json", document)
    state = tmp_path / "state.json"
    client = github or RecordingGitHub()
    code = action.run(
        [
            "--map",
            str(map_path),
            "--changes",
            str(changes),
            "--state",
            str(state),
            "--repository",
            "example-org/guidance",
            *(extra or []),
        ],
        github=client,
    )
    return code, client, state


# ---------------------------------------------------------------------------------------
# gate 6, at the consumer's edge
# ---------------------------------------------------------------------------------------


def test_the_wire_predicate_agrees_with_the_publisher_over_a_matrix(source: Source) -> None:
    """The action's own copy of the publishability rule, pinned to the real one.

    The action is standalone and stdlib-only, so it cannot import
    `ChangeRecord.publishable`; it reimplements it on the wire format. A
    reimplementation nothing compares is a reimplementation that will drift, and
    the direction it drifts in is "surface an unreviewed hash change to a
    legal-aid tracker".
    """
    observed = _observed(source, previous="a" * 64, new="b" * 64)
    candidates = [
        observed,
        observed.reviewed_by(
            reviewer="R",
            significance=Significance.EDITORIAL,
            status=ReviewStatus.DISMISSED,
        ),
        _confirmed(observed, significance=Significance.EDITORIAL),
        _confirmed(observed, significance=Significance.SUBSTANTIVE),
        # Substantive and confirmed by ONE human: the case gate 6 exists for.
        observed.reviewed_by(
            reviewer="R",
            significance=Significance.SUBSTANTIVE,
            status=ReviewStatus.CONFIRMED,
            public_copy="The requirements section changed.",
        ),
    ]
    assert len({record.publishable for record in candidates}) == 2, (
        "the matrix stopped covering both outcomes; the agreement below would be vacuous"
    )
    for record in candidates:
        assert action.is_surfaceable(record.to_dict()) is record.publishable, record.to_dict()


def test_a_dismissed_or_singly_reviewed_change_is_never_surfaced(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], source: Source
) -> None:
    """`--changes` accepts any conforming file, including one carrying these."""

    observed = _observed(source, previous="e" * 64, new="f" * 64)
    document["changes"] = [
        observed.to_dict(),
        observed.reviewed_by(
            reviewer="R", significance=Significance.EDITORIAL, status=ReviewStatus.DISMISSED
        ).to_dict(),
        observed.reviewed_by(
            reviewer="R",
            significance=Significance.SUBSTANTIVE,
            status=ReviewStatus.CONFIRMED,
            public_copy="The requirements section changed.",
        ).to_dict(),
    ]
    code, client, _ = _run(tmp_path, texas_map, document)
    assert code == action.EXIT_OK
    assert client.opened == []


# ---------------------------------------------------------------------------------------
# planning and idempotence
# ---------------------------------------------------------------------------------------


def test_a_first_run_opens_one_issue_for_the_in_scope_change(
    tmp_path: Path, texas_map: Path, document: dict[str, Any]
) -> None:
    code, client, state = _run(tmp_path, texas_map, document)
    assert code == action.EXIT_OK
    assert len(client.opened) == 1
    title = client.opened[0]["title"]
    assert title.startswith("[TX] drivers_license changed — ")
    assert json.loads(state.read_text(encoding="utf-8"))["changes"]


def test_a_second_run_over_the_same_document_opens_nothing(
    tmp_path: Path, texas_map: Path, document: dict[str, Any]
) -> None:
    """Idempotence is the property that makes this schedulable at all."""

    changes = _write(tmp_path / "changes.json", document)
    state = tmp_path / "state.json"
    argv = [
        "--map",
        str(texas_map),
        "--changes",
        str(changes),
        "--state",
        str(state),
        "--repository",
        "example-org/guidance",
    ]
    first = RecordingGitHub()
    assert action.run(argv, github=first) == action.EXIT_OK
    second = RecordingGitHub()
    assert action.run(argv, github=second) == action.EXIT_OK
    assert len(first.opened) == 1
    assert second.calls == []


def test_the_arizona_change_is_out_of_scope_for_a_texas_map(
    tmp_path: Path, texas_map: Path, document: dict[str, Any]
) -> None:
    _, client, _ = _run(tmp_path, texas_map, document)
    assert all("[AZ]" not in payload["title"] for payload in client.opened)


def test_a_named_source_id_is_in_scope_whatever_the_jurisdiction_filter_says(
    tmp_path: Path, document: dict[str, Any], arizona_source: Source
) -> None:
    scoped = _write(
        tmp_path / "map.json",
        {
            "schema_version": "1.0",
            "jurisdictions": ["TX"],
            "document_classes": ["drivers_license"],
            "source_ids": [arizona_source.id],
        },
    )
    _, client, _ = _run(tmp_path, scoped, document)
    assert len(client.opened) == 2


def test_omitting_document_classes_means_every_class_in_those_jurisdictions(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    wide = _write(tmp_path / "map.json", {"schema_version": "1.0", "jurisdictions": ["TX", "AZ"]})
    _, client, _ = _run(tmp_path, wide, document)
    assert len(client.opened) == 2


def test_every_in_scope_change_gets_exactly_one_action_from_the_closed_vocabulary(
    tmp_path: Path, texas_map: Path, document: dict[str, Any]
) -> None:
    """A change that stops being planned for is indistinguishable from one left alone."""

    scope = action.load_map(texas_map)
    in_scope = [
        change
        for change in document["changes"]
        if action.is_surfaceable(change) and scope.covers(change)
    ]
    planned = action.plan(document, scope, {"changes": {}})
    assert [item.change_id for item in planned] == [change["id"] for change in in_scope]
    assert {item.action for item in planned} <= set(action.PLAN_ACTIONS)
    assert len({item.change_id for item in planned}) == len(planned)


# ---------------------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------------------


def test_a_lifecycle_move_comments_on_the_existing_issue_instead_of_opening_a_second(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], texas_change: ChangeRecord
) -> None:
    changes = _write(tmp_path / "changes.json", document)
    state = tmp_path / "state.json"
    argv = [
        "--map",
        str(texas_map),
        "--changes",
        str(changes),
        "--state",
        str(state),
        "--repository",
        "example-org/guidance",
    ]
    first = RecordingGitHub()
    assert action.run(argv, github=first) == action.EXIT_OK

    withdrawn = _withdraw(texas_change)
    document["changes"] = [
        withdrawn.to_dict() if item["id"] == texas_change.id else item
        for item in document["changes"]
    ]
    _write(tmp_path / "changes.json", document)
    second = RecordingGitHub()
    assert action.run(argv, github=second) == action.EXIT_OK
    assert second.opened == []
    assert len(second.comments) == 1
    assert "withdrawn" in second.comments[0]["body"]


def test_a_change_first_seen_already_withdrawn_is_recorded_but_not_opened(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], texas_change: ChangeRecord
) -> None:
    """An issue whose first sentence is "this was withdrawn" is noise, not news."""

    withdrawn = _withdraw(texas_change)
    document["changes"] = [
        withdrawn.to_dict() if item["id"] == texas_change.id else item
        for item in document["changes"]
    ]
    code, client, state = _run(tmp_path, texas_map, document)
    assert code == action.EXIT_OK
    assert client.calls == []
    assert texas_change.id in json.loads(state.read_text(encoding="utf-8"))["changes"]


# ---------------------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------------------


def test_an_unknown_jurisdiction_refuses_the_run_and_opens_nothing(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    """A jurisdiction that matches nothing looks exactly like a quiet week."""

    typo = _write(tmp_path / "map.json", {"schema_version": "1.0", "jurisdictions": ["TXS"]})
    code, client, state = _run(tmp_path, typo, document)
    assert code == action.EXIT_REFUSED
    assert client.calls == []
    assert not state.exists()


def test_an_unknown_document_class_refuses_the_run(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    typo = _write(
        tmp_path / "map.json",
        {"schema_version": "1.0", "jurisdictions": ["TX"], "document_classes": ["drivers_licence"]},
    )
    code, _, _ = _run(tmp_path, typo, document)
    assert code == action.EXIT_REFUSED


def test_a_document_with_no_inventory_says_so_rather_than_blaming_the_map(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    document["sources"] = []
    code, _, _ = _run(tmp_path, texas_map, document)
    assert code == action.EXIT_REFUSED
    assert "carries no `sources` inventory" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body", [b"<html><body>404 Not Found</body></html>", b'{"ok": true}', b"", b"\xff\xfe"]
)
def test_a_response_that_is_not_a_changes_document_is_a_failure_not_an_empty_feed(
    tmp_path: Path, texas_map: Path, body: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state.json"
    code = action.run(
        [
            "--map",
            str(texas_map),
            "--changes",
            "https://example.invalid/changes.json",
            "--state",
            str(state),
            "--repository",
            "example-org/guidance",
        ],
        fetch=lambda url: body,
        github=RecordingGitHub(),
    )
    assert code == action.EXIT_REFUSED
    assert "not an empty feed" in capsys.readouterr().err
    assert not state.exists()


def test_a_map_naming_neither_a_jurisdiction_nor_a_source_is_refused(tmp_path: Path) -> None:
    empty = _write(tmp_path / "map.json", {"schema_version": "1.0"})
    with pytest.raises(action.ActionError, match="at least one jurisdiction or source_id"):
        action.load_map(empty)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "0.9", "jurisdictions": ["TX"]},
        {"schema_version": "1.0", "jurisdictions": "TX"},
        {"schema_version": "1.0", "jurisdictions": ["TX"], "extra": 1},
        ["not", "an", "object"],
    ],
)
def test_a_malformed_map_is_refused(tmp_path: Path, payload: object) -> None:
    with pytest.raises(action.ActionError):
        action.load_map(_write(tmp_path / "map.json", payload))


def test_a_missing_map_is_refused(tmp_path: Path) -> None:
    with pytest.raises(action.ActionError, match="not found"):
        action.load_map(tmp_path / "absent.json")


def test_an_unreadable_state_file_is_refused_rather_than_read_as_a_first_run(
    tmp_path: Path,
) -> None:
    """Treating it as a first run would re-open every issue already filed."""

    corrupt = tmp_path / "state.json"
    corrupt.write_text("{ not json", encoding="utf-8")
    with pytest.raises(action.ActionError, match="re-open every issue"):
        action.load_state(corrupt)


def test_a_state_file_that_is_not_a_state_document_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"changes": []}), encoding="utf-8")
    with pytest.raises(action.ActionError, match="not a state document"):
        action.load_state(path)


def test_a_missing_state_file_is_a_first_run(tmp_path: Path) -> None:
    assert action.load_state(tmp_path / "absent.json")["changes"] == {}


# ---------------------------------------------------------------------------------------
# what the issue says
# ---------------------------------------------------------------------------------------


def test_the_issue_body_reproduces_the_record_and_asserts_nothing_about_the_law(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], texas_change: ChangeRecord
) -> None:
    _, client, _ = _run(tmp_path, texas_map, document)
    body = client.opened[0]["body"]
    assert texas_change.previous_hash in body
    assert texas_change.new_hash in body
    assert "Chelsea Kelly-Reif" in body
    assert "Synthetic Independent Reviewer" in body
    assert "a court order is required" in body
    # The verification status rides along, so the citation never travels naked.
    assert "source verification:" in body
    assert "not a statement about what the law is" in body
    assert f"{action.MARKER}: {texas_change.id}" in body


def test_configured_labels_are_applied_and_no_label_is_invented(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    labelled = _write(
        tmp_path / "map.json",
        {"schema_version": "1.0", "jurisdictions": ["TX"], "labels": ["sentinel", "policy"]},
    )
    _, client, _ = _run(tmp_path, labelled, document)
    assert client.opened[0]["labels"] == ["sentinel", "policy"]
    assert all("labels" not in url for _, url, _ in client.calls)


# ---------------------------------------------------------------------------------------
# operating modes
# ---------------------------------------------------------------------------------------


def test_a_dry_run_opens_nothing_and_writes_no_state(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    code, client, state = _run(tmp_path, texas_map, document, extra=["--dry-run"])
    assert code == action.EXIT_OK
    assert client.calls == []
    assert not state.exists()
    assert "nothing was opened and no state was written" in capsys.readouterr().out


def test_a_github_refusal_exits_2_and_still_records_what_was_opened(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    """An aborted run that forgot the issues it just opened would file them twice."""

    wide = _write(tmp_path / "map.json", {"schema_version": "1.0", "jurisdictions": ["TX", "AZ"]})
    client = RecordingGitHub(fail_after=1)
    code, _, state = _run(tmp_path, wide, document, github=client)
    assert code == action.EXIT_API
    assert len(client.opened) == 1
    assert state.exists()
    recorded = json.loads(state.read_text(encoding="utf-8"))["changes"]
    assert sum(1 for entry in recorded.values() if entry.get("issue")) == 1


def test_a_run_without_a_repository_is_refused_before_anything_is_written(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    changes = _write(tmp_path / "changes.json", document)
    state = tmp_path / "state.json"
    code = action.run(
        ["--map", str(texas_map), "--changes", str(changes), "--state", str(state)],
        github=RecordingGitHub(),
    )
    assert code == action.EXIT_REFUSED
    assert "--repository" in capsys.readouterr().err
    assert not state.exists()


def test_the_repository_falls_back_to_the_runner_environment(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the refusal above: a workflow never types its own name.

    `action.yml` passes `${{ github.repository }}` through the environment, so
    the fallback is the path every real run takes. It is asserted here rather
    than left to whichever ambient variable the runner happens to export.
    """
    monkeypatch.setenv("GITHUB_REPOSITORY", "example-org/guidance")
    changes = _write(tmp_path / "changes.json", document)
    state = tmp_path / "state.json"
    client = RecordingGitHub()
    code = action.run(
        ["--map", str(texas_map), "--changes", str(changes), "--state", str(state)],
        github=client,
    )
    assert code == action.EXIT_OK
    assert len(client.opened) == 1
    assert all("example-org/guidance" in url for _, url, _ in client.calls)


def test_the_script_runs_as_an_executable_against_a_local_document(
    tmp_path: Path, texas_map: Path, document: dict[str, Any]
) -> None:
    """The action invokes a file, not an import. Exercise it the way the action does."""

    changes = _write(tmp_path / "changes.json", document)
    completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [
            sys.executable,
            str(SCRIPT),
            "--map",
            str(texas_map),
            "--changes",
            str(changes),
            "--state",
            str(tmp_path / "state.json"),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "open      " in completed.stdout
    assert "nothing was opened" in completed.stdout


# ---------------------------------------------------------------------------------------
# the action definition and the published schema
# ---------------------------------------------------------------------------------------


def _declared_inputs() -> set[str]:
    """The input names `action.yml` declares, read out of the file.

    Text, not a parser: this repository's existing workflow gates read YAML as
    text (`tests/test_trufflehog_workflow.py`), and the project carries no
    runtime dependency that would justify adding one for a test.
    """
    text = ACTION_YML.read_text(encoding="utf-8")
    block = text.split("\ninputs:\n", 1)[1].split("\noutputs:\n", 1)[0]
    return {
        line.strip().rstrip(":")
        for line in block.splitlines()
        if re.fullmatch(r"  [a-z][a-z-]*:", line)
    }


def _script_flags() -> set[str]:
    """The long options the script's own parser accepts, asked of the parser."""
    return {
        option.lstrip("-")
        for act in action.build_parser()._actions
        for option in act.option_strings
        if option.startswith("--")
    }


#: The script flags with no action input. `repository` is supplied from
#: `${{ github.repository }}` through the environment, because a consumer naming
#: their own repository twice is a consumer who will one day name someone else's;
#: `help` is argparse's.
_ENV_SUPPLIED_FLAGS = {"repository", "help"}

#: The action input with no script flag, and the reason it must stay that way: a
#: token passed as a command-line argument is a token in the runner's process
#: table and in any `set -x` trace of the step. It travels in the environment,
#: where `_github_call` reads it, and nowhere else.
_ENV_SUPPLIED_INPUTS = {"token"}


def test_the_action_and_its_script_declare_the_same_surface() -> None:
    """A two-way set check, both sides derived live.

    Not a count and not a retyped list: an input added to `action.yml` without a
    flag, or a flag added without an input, has to turn this red. Two such
    changes landing in the same merge would collapse a count check into a
    no-op — the failure mode measured across this portfolio today — and leave a
    set check exactly as strict as it was.
    """
    declared = _declared_inputs()
    flags = _script_flags()
    assert declared, "no inputs parsed out of action.yml; the block markers moved"
    assert declared - _ENV_SUPPLIED_INPUTS == flags - _ENV_SUPPLIED_FLAGS, (
        f"action.yml declares {sorted(declared)}; the script accepts "
        f"{sorted(flags - _ENV_SUPPLIED_FLAGS)}"
    )
    assert flags >= _ENV_SUPPLIED_FLAGS, "a flag documented as env-supplied no longer exists"
    assert declared >= _ENV_SUPPLIED_INPUTS, "an input documented as env-only no longer exists"


def test_the_token_never_becomes_a_command_line_argument() -> None:
    """It would land in the runner's process table and in any `set -x` trace."""

    text = ACTION_YML.read_text(encoding="utf-8")
    run_block = text.split("run: |", 1)[1]
    assert "--token" not in run_block
    assert "GITHUB_TOKEN" not in run_block
    assert "GITHUB_TOKEN: ${{ inputs.token }}" in text
    assert "--token" not in SCRIPT.read_text(encoding="utf-8")


def test_every_environment_variable_the_action_sets_is_one_it_uses() -> None:
    """The other half of the wiring, also as a set: nothing set unused, nothing used unset."""

    text = ACTION_YML.read_text(encoding="utf-8")
    assigned = set(re.findall(r"^\s+(SENTINEL_[A-Z_]+):", text, flags=re.MULTILINE))
    consumed = set(re.findall(r"\$(?:\{)?(SENTINEL_[A-Z_]+)", text))
    assert assigned, "no SENTINEL_* variables found; the run block was restructured"
    assert assigned == consumed, f"set {sorted(assigned)}, used {sorted(consumed)}"


def test_the_action_is_composite_and_invokes_the_script_it_ships_with() -> None:
    text = ACTION_YML.read_text(encoding="utf-8")
    assert "using: composite" in text
    assert "raise_issues.py" in text
    assert "--dry-run" in text


def test_the_action_pins_no_third_party_action_at_all() -> None:
    """A composite action that pulls in another action inherits its supply chain."""

    text = ACTION_YML.read_text(encoding="utf-8")
    uses = [line for line in text.splitlines() if line.strip().startswith("- uses:")]
    assert uses == []


def test_the_published_map_schema_describes_exactly_what_the_loader_accepts() -> None:
    """A schema an integrator builds against, checked against the code that reads it."""

    schema = json.loads(MAP_SCHEMA.read_text(encoding="utf-8"))
    # Both sides derived: the schema's properties, and the loader's own key set.
    # "Every key the loader accepts is described, and every key described is
    # accepted" — a set, so two keys added in two branches both survive the merge
    # and this still holds or still fails, never silently neither.
    assert set(schema["properties"]) == set(action.MAP_KEYS)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["enum"] == [action.MAP_SCHEMA_VERSION]


# ---------------------------------------------------------------------------------------
# the transports
# ---------------------------------------------------------------------------------------
#
# The two functions that actually open sockets. They are the surfaces where the
# portfolio's dominant defect would live — an HTTP error page read as content, a
# GitHub error body echoed into a log — so they are exercised against a stubbed
# `urlopen` rather than left to a consumer's first scheduled run.


class _Response:
    """The two attributes the code reads off `urlopen`, as a context manager."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_the_fetcher_refuses_a_non_200_and_a_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(action.urllib.request, "urlopen", lambda *a, **k: _Response(503, b"{}"))
    with pytest.raises(action.ActionError, match="HTTP 503"):
        action._http_get("https://example.invalid/changes.json")

    def _boom(*args: object, **kwargs: object) -> None:
        raise action.urllib.error.URLError("no route to host")

    monkeypatch.setattr(action.urllib.request, "urlopen", _boom)
    with pytest.raises(action.ActionError, match="could not be fetched"):
        action._http_get("https://example.invalid/changes.json")


def test_the_fetcher_returns_the_body_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        action.urllib.request, "urlopen", lambda *a, **k: _Response(200, b'{"changes": []}')
    )
    assert action._http_get("https://example.invalid/changes.json") == b'{"changes": []}'


def test_the_github_call_needs_a_token_and_never_echoes_an_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GitHub error body can quote the request headers, one of which is the token."""

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(action.ActionError, match="GITHUB_TOKEN is not set"):
        action._github_call("POST", "https://api.github.com/repos/o/r/issues", {})

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_notarealtoken")

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise action.urllib.error.HTTPError(
            url="https://api.github.com/repos/o/r/issues",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(action.urllib.request, "urlopen", _forbidden)
    with pytest.raises(action.ActionError) as raised:
        action._github_call("POST", "https://api.github.com/repos/o/r/issues", {})
    assert "HTTP 403" in str(raised.value)
    assert "ghp_notarealtoken" not in str(raised.value)


def test_the_github_call_reports_an_unreachable_api_and_returns_a_created_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_notarealtoken")

    def _unreachable(*args: object, **kwargs: object) -> None:
        raise action.urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(action.urllib.request, "urlopen", _unreachable)
    with pytest.raises(action.ActionError, match="could not be reached"):
        action._github_call("POST", "https://api.github.com/repos/o/r/issues", {})

    monkeypatch.setattr(
        action.urllib.request, "urlopen", lambda *a, **k: _Response(201, b'{"number": 7}')
    )
    assert action._github_call("POST", "https://api.github.com/repos/o/r/issues", {}) == {
        "number": 7
    }


def test_an_unreadable_local_changes_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(action.ActionError, match="could not be read"):
        action.fetch_changes(str(tmp_path / "absent.json"), action._http_get)


def test_a_lifecycle_move_on_a_change_with_no_recorded_issue_opens_no_second_issue(
    tmp_path: Path, texas_map: Path, document: dict[str, Any], texas_change: ChangeRecord
) -> None:
    """State that remembers the change but not its issue number: comment on nothing.

    The alternative — open a fresh issue — files one whose first sentence is
    about a withdrawal, in a tracker where it reads as a new finding.
    """

    withdrawn = _withdraw(texas_change)
    document["changes"] = [
        withdrawn.to_dict() if item["id"] == texas_change.id else item
        for item in document["changes"]
    ]
    state = tmp_path / "state.json"
    _write(state, {"schema_version": "1.0", "changes": {texas_change.id: {"lifecycle": "stale"}}})
    changes = _write(tmp_path / "changes.json", document)
    client = RecordingGitHub()
    code = action.run(
        [
            "--map",
            str(texas_map),
            "--changes",
            str(changes),
            "--state",
            str(state),
            "--repository",
            "example-org/guidance",
        ],
        github=client,
    )
    assert code == action.EXIT_OK
    assert client.calls == []


def test_a_map_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(action.ActionError, match="not valid JSON"):
        action.load_map(path)
