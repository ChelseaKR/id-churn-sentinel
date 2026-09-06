"""The registry's own history, derived rather than remembered.

The property under test throughout is that a change to the registry cannot reach the log as
silence. Every other assertion here supports that one: the vocabulary is closed so a consumer
can branch exhaustively, the output has no clock so two identical inputs are byte-identical,
and the diff refuses to return at all if it can see a field difference no event reports.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.registry import (
    FETCH_POLICY_ALLOW,
    GAP_REASONS,
    REJECTED,
    UNVERIFIED,
    VERIFIED,
    Registry,
    default_registry_path,
    load_registry,
)
from id_churn_sentinel.core.registry_changelog import (
    CHANGELOG_SCHEMA_VERSION,
    EVENT_KINDS,
    RegistryChangelogError,
    RegistryEvent,
    changelog_document,
    default_changelog_path,
    diff_registries,
    dumps_changelog,
    gap_key,
    load_changelog,
    read_registry_at,
    reconcile,
    seed_document,
)
from tests.test_schema import _validate


def _source(
    source_id: str = "tx-dps-name-change",
    *,
    url: str = "https://www.dps.texas.gov/section/driver-license/change-name",
    authority: str = "Texas Department of Public Safety",
    jurisdiction: str = "TX",
    document_class: str = "drivers_license",
    verification: dict[str, Any] | None = None,
    fetch_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": source_id,
        "jurisdiction": jurisdiction,
        "document_class": document_class,
        "url": url,
        "authority": authority,
        "verified": bool(verification and verification.get("status") == VERIFIED),
        "notes": "fixture",
    }
    if verification:
        entry["verification"] = verification
    if fetch_policy:
        entry["fetch_policy"] = fetch_policy
    return entry


def _gap(
    jurisdiction: str = "VT",
    document_class: str = "drivers_license",
    reason: str = "robots-disallowed",
    checked: str = "2026-05-01",
) -> dict[str, Any]:
    return {
        "jurisdiction": jurisdiction,
        "document_class": document_class,
        "reason": reason,
        "hosts": ["dmv.vermont.gov"],
        "checked": checked,
        "detail": "fixture gap",
    }


def _registry(tmp_path: Path, name: str, sources: list[Any], gaps: list[Any]) -> Registry:
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps({"registry_version": "1.0", "sources": sources, "gaps": gaps}, indent=2),
        encoding="utf-8",
    )
    return load_registry(path)


_CONFIRMED = {
    "status": VERIFIED,
    "verifier": "A Named Human",
    "at": "2026-06-01",
    "evidence": "tests/evidence/fixture.json",
    "expires_at": "2026-11-28",
}


# ---- the three "done when" criteria -------------------------------------------------------


def test_one_swap_one_new_gap_and_one_verification_yield_exactly_three_events(
    tmp_path: Path,
) -> None:
    """The proposal's acceptance criterion, asserted on the kinds and not merely the count."""
    before = _registry(
        tmp_path,
        "before",
        [_source(), _source("az-mvd-name-change", url="https://azdot.gov/old", jurisdiction="AZ")],
        [],
    )
    after = _registry(
        tmp_path,
        "after",
        [
            _source(verification=_CONFIRMED),
            _source("az-mvd-name-change", url="https://azdot.gov/deeper", jurisdiction="AZ"),
        ],
        [_gap()],
    )

    events = diff_registries(before, after)

    # Sorted by (subject, subject_id, kind): gaps sort before sources, so the ordering is a
    # property of the output rather than of the order the diff happened to discover things in.
    assert [event.kind for event in events] == ["gap_opened", "url_changed", "verified"]
    assert events[0].subject_id == gap_key("VT", "drivers_license")
    assert events[0].reason == "robots-disallowed"
    swap = events[1]
    assert swap.subject_id == "az-mvd-name-change"
    assert (swap.from_value, swap.to_value) == ("https://azdot.gov/old", "https://azdot.gov/deeper")
    assert events[2].actor == "A Named Human"
    assert (events[2].from_value, events[2].to_value) == (UNVERIFIED, VERIFIED)


def test_a_registry_diffed_against_itself_yields_nothing_byte_identically(
    tmp_path: Path,
) -> None:
    """No events, and no clock: the same inputs serialize to the same bytes, twice."""
    registry = _registry(tmp_path, "same", [_source()], [_gap()])

    assert diff_registries(registry, registry) == ()

    first = dumps_changelog(changelog_document((), unrecorded_before="deadbeef"))
    second = dumps_changelog(changelog_document((), unrecorded_before="deadbeef"))
    assert first == second
    assert "generated_at" not in first, (
        "the changelog carries a timestamp, so two runs over identical revisions disagree — "
        "which makes it impossible to tell a real registry change from a re-run"
    )


def test_a_changelog_naming_a_source_in_neither_revision_fails_reconciliation(
    tmp_path: Path,
) -> None:
    """The hand-edit case: an event about a source this registry has never contained."""
    registry = _registry(tmp_path, "live", [_source()], [])
    document = {
        "schema_version": CHANGELOG_SCHEMA_VERSION,
        "unrecorded_before": {"revision": "0" * 64, "statement": "fixture"},
        "events": [
            {
                "kind": "url_changed",
                "subject": "source",
                "subject_id": "ok-invented-source",
                "jurisdiction": "OK",
                "document_class": "drivers_license",
                "from_revision": "0" * 64,
                "to_revision": "1" * 64,
                "from": "https://example.invalid/a",
                "to": "https://example.invalid/b",
            }
        ],
    }

    violations = reconcile(document, registry)

    assert len(violations) == 1
    assert "ok-invented-source" in violations[0]


# ---- the vocabulary is closed, and every kind means one thing -----------------------------


def test_a_human_finding_the_wrong_page_is_not_reported_as_an_expiry(tmp_path: Path) -> None:
    """`verified -> rejected` is a person's finding, not a lapsed date.

    The proposal's vocabulary had one kind for leaving `verified`. Publishing "this lapsed"
    over the top of "a named human opened this URL and found it is NOT the official page"
    would be a wrong label on the most consequential fact this registry records.
    """
    before = _registry(tmp_path, "v", [_source(verification=_CONFIRMED)], [])
    after = _registry(
        tmp_path,
        "r",
        [
            _source(
                verification={
                    "status": REJECTED,
                    "verifier": "A Named Human",
                    "at": "2026-07-02",
                    "note": "not the official page",
                }
            )
        ],
        [],
    )

    (event,) = diff_registries(before, after)

    assert event.kind == "verification_rejected"
    assert (event.from_value, event.to_value) == (VERIFIED, REJECTED)


def test_leaving_verified_carries_the_recheck_date_rather_than_a_conclusion(
    tmp_path: Path,
) -> None:
    """`verification_expired` reports a transition and hands over the date it did not judge.

    A diff of two files has no `as_of`, so nothing here may claim the recheck date passed.
    The date travels on the event instead, and a reader draws their own conclusion.
    """
    before = _registry(tmp_path, "v", [_source(verification=_CONFIRMED)], [])
    after = _registry(tmp_path, "u", [_source()], [])

    (event,) = diff_registries(before, after)

    assert event.kind == "verification_expired"
    assert (event.from_value, event.to_value) == (VERIFIED, UNVERIFIED)
    assert event.expires_at == "2026-11-28"


def test_a_source_replaced_by_a_named_gap_is_one_event_not_two(tmp_path: Path) -> None:
    """One act, one event — and the event carries the reason the silence is now accounted for."""
    before = _registry(tmp_path, "b", [_source(), _source("mi-scao", jurisdiction="MI")], [])
    after = _registry(
        tmp_path, "a", [_source()], [_gap("MI", "drivers_license", "robots-disallowed")]
    )

    (event,) = diff_registries(before, after)

    assert event.kind == "moved_to_gap"
    assert event.subject_id == "mi-scao"
    assert event.reason == "robots-disallowed"


def test_a_gap_that_appears_with_no_predecessor_source_is_reported_as_opened(
    tmp_path: Path,
) -> None:
    before = _registry(tmp_path, "b", [_source()], [])
    after = _registry(tmp_path, "a", [_source()], [_gap()])

    (event,) = diff_registries(before, after)

    assert event.kind == "gap_opened"
    assert event.subject == "gap"


def test_a_recorded_fetch_policy_decision_is_an_event_with_its_reviewer(tmp_path: Path) -> None:
    before = _registry(tmp_path, "b", [_source()], [])
    after = _registry(
        tmp_path,
        "a",
        [
            _source(
                fetch_policy={
                    "outcome": FETCH_POLICY_ALLOW,
                    "reviewer": "A Policy Reader",
                    "at": "2026-06-02",
                    "expires_at": "2026-11-29",
                    "evidence": "tests/evidence/robots.txt",
                    "reason": "robots.txt permits",
                }
            )
        ],
        [],
    )

    (event,) = diff_registries(before, after)

    assert event.kind == "policy_decision_recorded"
    assert (event.from_value, event.to_value) == ("unreviewed", FETCH_POLICY_ALLOW)
    assert event.actor == "A Policy Reader"


def test_an_event_omits_the_fields_it_has_no_value_for(tmp_path: Path) -> None:
    """Absence as absence. A blank `reason` would read as a reason of ""."""
    before = _registry(tmp_path, "b", [_source(url="https://a.example/x")], [])
    after = _registry(tmp_path, "a", [_source(url="https://b.example/x")], [])

    (payload,) = [event.to_dict() for event in diff_registries(before, after)]

    assert "reason" not in payload
    assert "actor" not in payload
    assert payload["from_revision"] != payload["to_revision"]


def test_every_kind_the_module_can_emit_is_in_the_published_vocabulary() -> None:
    """A consumer branching exhaustively on `kind` must not be handed a surprise."""
    assert len(EVENT_KINDS) == 13
    assert "gap_opened" in EVENT_KINDS
    assert "verification_rejected" in EVENT_KINDS


def test_an_event_reason_outside_the_gap_vocabulary_is_refused() -> None:
    document = {
        "schema_version": CHANGELOG_SCHEMA_VERSION,
        "unrecorded_before": {"revision": "0" * 64, "statement": "fixture"},
        "events": [
            {
                "kind": "gap_opened",
                "subject": "gap",
                "subject_id": gap_key("VT", "drivers_license"),
                "jurisdiction": "VT",
                "document_class": "drivers_license",
                "from_revision": "0" * 64,
                "to_revision": "1" * 64,
                "reason": "we could not get it",
            }
        ],
    }
    path = Path(__file__).parent / "does-not-exist.json"
    assert not path.exists()
    with pytest.raises(RegistryChangelogError, match="closed gap vocabulary"):
        _load_document(document)


def _load_document(document: dict[str, Any], tmp: Path | None = None) -> dict[str, Any]:
    target = (tmp or Path(__file__).parent) / "_changelog-under-test.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    try:
        return load_changelog(target)
    finally:
        target.unlink()


# ---- the diff refuses to drop a change ----------------------------------------------------


def test_the_diff_refuses_to_return_when_a_field_change_has_no_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control for the completeness assertion itself.

    A field is removed from the kind-coverage map, simulating a registry field that gains a
    value but no event kind. The diff must raise rather than return a log that reports
    nothing — because "nothing to report" is the one claim this project may not make by
    accident.
    """
    from id_churn_sentinel.core import registry_changelog as module

    before = _registry(tmp_path, "b", [_source(url="https://a.example/x")], [])
    after = _registry(tmp_path, "a", [_source(url="https://b.example/x")], [])

    assert module._KIND_COVERS["url_changed"] == frozenset({"url"}), (
        "the sabotage targets a mapping that has moved; it would silently no-op"
    )
    monkeypatch.setitem(module._KIND_COVERS, "url_changed", frozenset())
    assert module._KIND_COVERS["url_changed"] == frozenset(), "the sabotage did not land"

    with pytest.raises(RegistryChangelogError, match="would have dropped"):
        diff_registries(before, after)


def test_a_field_may_only_go_unreported_by_being_named_with_a_reason() -> None:
    from id_churn_sentinel.core import registry_changelog as module

    assert set(module._NON_EVENTFUL_FIELDS) == {"notes", "checked", "verified", "active"}
    for field_name, reason in module._NON_EVENTFUL_FIELDS.items():
        assert len(reason) > 40, f"{field_name} is excluded without a stated reason"


def test_an_operator_note_never_becomes_a_public_event(tmp_path: Path) -> None:
    """Free-form registry rationale stays internal, as it does in every other artifact."""
    before = _registry(tmp_path, "b", [_source()], [])
    entry = _source()
    entry["notes"] = "internal rationale that must never be republished"
    after = _registry(tmp_path, "a", [entry], [])

    assert diff_registries(before, after) == ()


# ---- the committed log --------------------------------------------------------------------


def test_the_committed_changelog_loads_and_reconciles_with_the_committed_registry() -> None:
    document = load_changelog()
    violations = reconcile(document, load_registry())
    assert not violations, violations
    assert len(document["unrecorded_before"]["revision"]) == 64


def test_the_committed_changelog_says_where_recorded_history_begins() -> None:
    """A log that does not say where it starts invites its first event to be read as the first."""
    document = load_changelog()
    statement = document["unrecorded_before"]["statement"]
    assert "UNRECORDED" in statement
    assert default_changelog_path().name == "registry-changelog.json"


def test_a_log_without_a_starting_revision_is_refused() -> None:
    with pytest.raises(RegistryChangelogError, match="unrecorded_before"):
        _load_document(
            {"schema_version": CHANGELOG_SCHEMA_VERSION, "events": []},
        )


def test_a_log_with_an_unknown_event_field_is_refused() -> None:
    with pytest.raises(RegistryChangelogError, match="unknown field"):
        _load_document(
            {
                "schema_version": CHANGELOG_SCHEMA_VERSION,
                "unrecorded_before": {"revision": "0" * 64, "statement": "fixture"},
                "events": [
                    {
                        "kind": "added",
                        "subject": "source",
                        "subject_id": "tx-dps-name-change",
                        "jurisdiction": "TX",
                        "document_class": "drivers_license",
                        "from_revision": "0" * 64,
                        "to_revision": "1" * 64,
                        "significance": "substantive",
                    }
                ],
            }
        )


def test_a_log_whose_last_reported_url_disagrees_with_the_registry_fails(tmp_path: Path) -> None:
    """The log and the registry cannot disagree about the present.

    The log is the one that will be believed, because it is the one that looks like a record.
    """
    registry = _registry(tmp_path, "live", [_source(url="https://now.example/x")], [])
    document = {
        "schema_version": CHANGELOG_SCHEMA_VERSION,
        "unrecorded_before": {"revision": "0" * 64, "statement": "fixture"},
        "events": [
            {
                "kind": "url_changed",
                "subject": "source",
                "subject_id": "tx-dps-name-change",
                "jurisdiction": "TX",
                "document_class": "drivers_license",
                "from_revision": "0" * 64,
                "to_revision": "1" * 64,
                "from": "https://then.example/x",
                "to": "https://stale.example/x",
            }
        ],
    }

    violations = reconcile(document, registry)

    assert len(violations) == 1
    assert "https://now.example/x" in violations[0]


def test_the_seed_document_is_empty_and_names_the_registry_it_starts_at() -> None:
    document = seed_document(load_registry())
    assert document["events"] == []
    assert len(document["unrecorded_before"]["revision"]) == 64


# ---- reading a revision -------------------------------------------------------------------


def test_a_revision_is_read_through_the_same_validator_as_the_live_registry(
    tmp_path: Path,
) -> None:
    """A historical revision is subject to every rule the current one is."""
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"registry_version": "1.0", "sources": []}), encoding="utf-8")
    with pytest.raises(Exception, match="non-empty list"):
        read_registry_at(str(path))


def test_an_unresolvable_reference_names_what_it_tried(tmp_path: Path) -> None:
    with pytest.raises(RegistryChangelogError, match="neither a readable file nor a git revision"):
        read_registry_at("no-such-revision-anywhere")


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 — fixed argument vector in a throwaway repo
        ["git", *args],  # noqa: S607 — resolved from PATH, as `read_registry_at` itself does
        cwd=root,
        check=True,
    )


def _git_repo(tmp_path: Path, revisions: list[list[Any]]) -> Path:
    """A throwaway git repository whose history is a sequence of registries.

    The suite deliberately does not read *this* repository's history. `actions/checkout`
    fetches a shallow tree, so a test that resolves a real commit passes locally and fails in
    CI with `invalid object name` — which is a test that only runs on a developer's machine,
    and therefore a gate that cannot fail where it matters. Building the history the test
    needs exercises exactly the same `git show` path with no dependency on checkout depth.
    """
    root = tmp_path / "repo"
    (root / "sources").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "Test")
    for index, sources in enumerate(revisions):
        (root / "sources" / "registry.json").write_text(
            json.dumps({"registry_version": "1.0", "sources": sources, "gaps": []}, indent=2),
            encoding="utf-8",
        )
        _git(root, "add", "sources/registry.json")
        _git(root, "commit", "-q", "-m", f"revision {index}")
    return root


def test_a_revision_is_resolved_out_of_git_and_diffed_against_the_working_tree(
    tmp_path: Path,
) -> None:
    """The `git show <rev>:sources/registry.json` path, end to end."""
    root = _git_repo(
        tmp_path,
        [
            [_source(url="https://old.example/x")],
            [_source(url="https://new.example/x")],
        ],
    )

    before = read_registry_at("HEAD~1", root=root)
    after = read_registry_at("HEAD", root=root)
    (event,) = diff_registries(before, after)

    assert event.kind == "url_changed"
    assert (event.from_value, event.to_value) == (
        "https://old.example/x",
        "https://new.example/x",
    )


def test_a_revision_may_also_be_given_as_an_explicit_git_pathspec(tmp_path: Path) -> None:
    root = _git_repo(tmp_path, [[_source()], [_source(url="https://moved.example/x")]])
    registry = read_registry_at("HEAD:sources/registry.json", root=root)
    assert registry.sources[0].url == "https://moved.example/x"


# ---- the published schema -----------------------------------------------------------------
#
# Validated with `tests/test_schema.py`'s own validator, not `jsonschema`. Two reasons, and
# the second is the important one: this project deliberately carries no runtime dependency
# tree, and `pytest.importorskip` would turn every check below into a test that passes by not
# running — a gate that cannot fail, on the document an integrator is told to build against.

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "schema" / "registry-changelog-v1.schema.json"
)


def _schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return loaded


def _violations(document: Any) -> list[str]:
    schema = _schema()
    return _validate(document, schema, schema, "$")


def test_the_validator_this_file_relies_on_still_rejects_something() -> None:
    """Guard the guard, as `test_schema.py` does: a validator that always returns `[]` would
    make every assertion below pass while checking nothing."""
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    assert _validate({}, schema, schema, "$") != []


def test_the_committed_changelog_validates_against_its_published_schema() -> None:
    assert _violations(load_changelog()) == []


def test_a_populated_log_validates_against_the_schema(tmp_path: Path) -> None:
    """Not an empty document: one carrying an event of every kind the diff can produce."""
    before = _registry(
        tmp_path,
        "b",
        [_source(), _source("nv-old", jurisdiction="NV"), _source("mi-scao", jurisdiction="MI")],
        [_gap("AK", "drivers_license", "blocked-403")],
    )
    after = _registry(
        tmp_path,
        "a",
        [
            _source(url="https://swapped.example/x", verification=_CONFIRMED),
            _source("az-new", jurisdiction="AZ"),
        ],
        [
            _gap("MI", "drivers_license", "robots-disallowed"),
            _gap("VT", "drivers_license", "tls-unverifiable"),
        ],
    )
    events = diff_registries(before, after)
    kinds = {event.kind for event in events}
    assert {
        "url_changed",
        "verified",
        "added",
        "removed",
        "moved_to_gap",
        "gap_opened",
        "gap_closed",
    } <= kinds, kinds
    assert _violations(changelog_document(events, unrecorded_before="0" * 64)) == []


def test_the_schema_enum_matches_the_code() -> None:
    """A schema that permits a kind the code cannot emit, or forbids one it can, is worse than
    no schema: a consumer validates against it and concludes the vocabulary is closed."""
    enum = set(_schema()["$defs"]["event"]["properties"]["kind"]["enum"])
    assert enum == set(EVENT_KINDS)


def test_the_schema_reason_enum_matches_the_registry_vocabulary() -> None:
    enum = set(_schema()["$defs"]["event"]["properties"]["reason"]["enum"])
    assert enum == set(GAP_REASONS)


def test_the_schema_rejects_a_blanked_optional_field() -> None:
    """Absence must be absence. `"reason": ""` is the shape this schema exists to refuse."""
    document = changelog_document((), unrecorded_before="0" * 64)
    document["events"] = [
        {
            "kind": "added",
            "subject": "source",
            "subject_id": "tx-dps-name-change",
            "jurisdiction": "TX",
            "document_class": "drivers_license",
            "from_revision": "0" * 64,
            "to_revision": "1" * 64,
            "to": "",
        }
    ]
    assert _violations(document) != []


def test_the_schema_rejects_an_event_kind_outside_the_vocabulary() -> None:
    document = changelog_document((), unrecorded_before="0" * 64)
    document["events"] = [
        {
            "kind": "looks_substantive",
            "subject": "source",
            "subject_id": "tx-dps-name-change",
            "jurisdiction": "TX",
            "document_class": "drivers_license",
            "from_revision": "0" * 64,
            "to_revision": "1" * 64,
        }
    ]
    assert _violations(document) != []


# ---- the command, and the gate it feeds ---------------------------------------------------


def _swap_pair(tmp_path: Path) -> list[str]:
    """A prior revision of the COMMITTED registry, one URL back.

    The later side is the committed registry itself, so the derived event's `to` is the URL the
    registry actually carries and `--append` reconciles. A fixture pair would not: `reconcile`
    would correctly refuse a log describing a registry this repository has never had.
    """
    raw = json.loads(default_registry_path().read_text(encoding="utf-8"))
    subject = raw["sources"][0]
    previous = json.loads(json.dumps(raw))
    previous["sources"][0]["url"] = "https://an-earlier-page.example/x"
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(previous, indent=2), encoding="utf-8")
    after.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    assert subject["url"] != previous["sources"][0]["url"]
    return ["--from", str(before), "--to", str(after)]


def test_the_command_prints_a_document_derived_from_two_revisions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["registry", "changelog", *_swap_pair(tmp_path)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert [event["kind"] for event in document["events"]] == ["url_changed"]
    assert (
        document["events"][0]["subject_id"]
        == json.loads(default_registry_path().read_text(encoding="utf-8"))["sources"][0]["id"]
    )


def test_the_command_output_is_byte_identical_across_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["registry", "changelog", *_swap_pair(tmp_path)]
    assert main(argv) == 0
    first = capsys.readouterr().out
    assert main(argv) == 0
    assert capsys.readouterr().out == first


def test_the_command_refuses_to_guess_an_earlier_revision(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Defaulting `--from` would silently choose which history got recorded."""
    assert main(["registry", "changelog"]) == 1
    assert "--from is required" in capsys.readouterr().err


def test_check_docs_goes_red_when_the_changelog_does_not_describe_this_registry(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control for the gate this change adds.

    The sabotage is asserted to have landed before the gate is run: a fault that silently
    no-ops reads exactly like a pass, which has already happened once in this repository.
    """
    from id_churn_sentinel import cli as cli_module

    good = load_changelog()
    assert reconcile(good, load_registry()) == [], "the committed log already fails; no control"

    poisoned = json.loads(json.dumps(good))
    poisoned["events"].append(
        {
            "kind": "url_changed",
            "subject": "source",
            "subject_id": "zz-a-source-that-never-existed",
            "jurisdiction": "OK",
            "document_class": "drivers_license",
            "from_revision": "0" * 64,
            "to_revision": "1" * 64,
            "from": "https://example.invalid/a",
            "to": "https://example.invalid/b",
        }
    )
    monkeypatch.setattr(cli_module, "load_changelog", lambda *a, **k: poisoned)
    # Read the sabotage back out of the module under test. A fault that silently no-ops reads
    # exactly like a pass, and this repository has already shipped one that did.
    patched: Any = getattr(cli_module, "load_changelog")  # noqa: B009 — deliberate dynamic read
    assert patched()["events"][-1]["subject_id"] == "zz-a-source-that-never-existed", (
        "the sabotage did not land in the module under test"
    )

    assert main(["coverage", "--check-docs"]) == 1
    captured = capsys.readouterr()
    assert "THE REGISTRY CHANGELOG DOES NOT DESCRIBE THIS REGISTRY" in captured.err
    assert "zz-a-source-that-never-existed" in captured.err


def test_check_docs_passes_over_the_committed_log() -> None:
    assert main(["coverage", "--check-docs"]) == 0


def test_init_refuses_to_overwrite_recorded_history(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert default_changelog_path().exists()
    assert main(["registry", "changelog", "--init"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_append_refuses_a_revision_pair_the_log_already_records(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Appending the same diff twice would report one swap as two."""
    from id_churn_sentinel import cli as cli_module

    target = tmp_path / "registry-changelog.json"
    target.write_text(dumps_changelog(seed_document(load_registry())), encoding="utf-8")
    monkeypatch.setattr(cli_module, "default_changelog_path", lambda *a, **k: target)

    argv = ["registry", "changelog", *_swap_pair(tmp_path), "--append"]
    assert main(argv) == 0
    appended = json.loads(target.read_text(encoding="utf-8"))
    assert len(appended["events"]) == 1

    assert main(argv) == 1
    assert "would report one change twice" in capsys.readouterr().err
    assert json.loads(target.read_text(encoding="utf-8"))["events"] == appended["events"]


# ---- fail-closed refusals -----------------------------------------------------------------
#
# Each of these is a path that must raise rather than return something a caller would go on to
# treat as a valid log. They are cheap to write and expensive to be missing: a loader that
# tolerates a malformed event hands a consumer an artifact this project has certified.


def test_an_event_constructed_with_an_unknown_kind_is_refused() -> None:
    with pytest.raises(RegistryChangelogError, match="unknown event kind"):
        RegistryEvent(
            kind="looks_substantive",
            subject="source",
            subject_id="x",
            jurisdiction="TX",
            document_class="drivers_license",
            from_revision="0" * 64,
            to_revision="1" * 64,
        )


def test_an_event_constructed_with_an_unknown_subject_is_refused() -> None:
    with pytest.raises(RegistryChangelogError, match="unknown event subject"):
        RegistryEvent(
            kind="added",
            subject="jurisdiction",
            subject_id="x",
            jurisdiction="TX",
            document_class="drivers_license",
            from_revision="0" * 64,
            to_revision="1" * 64,
        )


def test_an_event_constructed_with_a_free_text_reason_is_refused() -> None:
    """The reason vocabulary is closed at construction, not merely at load."""
    with pytest.raises(RegistryChangelogError, match="closed gap vocabulary"):
        RegistryEvent(
            kind="gap_opened",
            subject="gap",
            subject_id=gap_key("VT", "drivers_license"),
            jurisdiction="VT",
            document_class="drivers_license",
            from_revision="0" * 64,
            to_revision="1" * 64,
            reason="we could not get it",
        )


def test_a_missing_log_is_an_error_rather_than_an_empty_history(tmp_path: Path) -> None:
    """The absence of the file is not the absence of events. Reading it as `[]` would let a
    deleted log pass the gate it exists to feed."""
    with pytest.raises(RegistryChangelogError, match="not found"):
        load_changelog(tmp_path / "nothing-here.json")


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ([], "must be a JSON object"),
        ({"schema_version": "9.9"}, "is not the supported"),
        (
            {
                "schema_version": CHANGELOG_SCHEMA_VERSION,
                "unrecorded_before": {"revision": "0" * 64, "statement": "x"},
                "events": {},
            },
            "must be a list",
        ),
        (
            {
                "schema_version": CHANGELOG_SCHEMA_VERSION,
                "unrecorded_before": {"revision": "0" * 64, "statement": "x"},
                "events": ["not an object"],
            },
            "must be an object",
        ),
        (
            {
                "schema_version": CHANGELOG_SCHEMA_VERSION,
                "unrecorded_before": {"revision": "0" * 64, "statement": "x"},
                "events": [{"kind": "added"}],
            },
            "is missing",
        ),
    ],
)
def test_a_malformed_log_is_refused(tmp_path: Path, document: Any, expected: str) -> None:
    target = tmp_path / "log.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RegistryChangelogError, match=expected):
        load_changelog(target)


@pytest.mark.parametrize("field", ["kind", "subject"])
def test_a_log_event_with_an_unknown_closed_value_is_refused(tmp_path: Path, field: str) -> None:
    event = {
        "kind": "added",
        "subject": "source",
        "subject_id": "tx-dps-name-change",
        "jurisdiction": "TX",
        "document_class": "drivers_license",
        "from_revision": "0" * 64,
        "to_revision": "1" * 64,
    }
    event[field] = "something-else"
    target = tmp_path / "log.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": CHANGELOG_SCHEMA_VERSION,
                "unrecorded_before": {"revision": "0" * 64, "statement": "x"},
                "events": [event],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryChangelogError, match="is not"):
        load_changelog(target)


def test_a_log_naming_a_gap_the_registry_does_not_have_fails_reconciliation(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, "live", [_source()], [])
    document = {
        "schema_version": CHANGELOG_SCHEMA_VERSION,
        "unrecorded_before": {"revision": "0" * 64, "statement": "x"},
        "events": [
            {
                "kind": "gap_opened",
                "subject": "gap",
                "subject_id": gap_key("VT", "drivers_license"),
                "jurisdiction": "VT",
                "document_class": "drivers_license",
                "from_revision": "0" * 64,
                "to_revision": "1" * 64,
                "reason": "robots-disallowed",
            }
        ],
    }

    violations = reconcile(document, registry)

    assert len(violations) == 1
    assert "no `gap_closed` event accounts for" in violations[0]


def test_a_gap_whose_reported_reason_disagrees_with_the_registry_fails(tmp_path: Path) -> None:
    registry = _registry(tmp_path, "live", [_source()], [_gap(reason="blocked-403")])
    document = {
        "schema_version": CHANGELOG_SCHEMA_VERSION,
        "unrecorded_before": {"revision": "0" * 64, "statement": "x"},
        "events": [
            {
                "kind": "gap_opened",
                "subject": "gap",
                "subject_id": gap_key("VT", "drivers_license"),
                "jurisdiction": "VT",
                "document_class": "drivers_license",
                "from_revision": "0" * 64,
                "to_revision": "1" * 64,
                "reason": "robots-disallowed",
            }
        ],
    }

    violations = reconcile(document, registry)

    assert len(violations) == 1
    assert "blocked-403" in violations[0]


def test_a_removed_source_may_still_be_named_by_the_log(tmp_path: Path) -> None:
    """The point of a changelog: an entry that is gone stays legible, without failing the gate."""
    registry = _registry(tmp_path, "live", [_source()], [])
    document = {
        "schema_version": CHANGELOG_SCHEMA_VERSION,
        "unrecorded_before": {"revision": "0" * 64, "statement": "x"},
        "events": [
            {
                "kind": "removed",
                "subject": "source",
                "subject_id": "nv-dmv-old-entry",
                "jurisdiction": "NV",
                "document_class": "drivers_license",
                "from_revision": "0" * 64,
                "to_revision": "1" * 64,
                "from": "https://dmv.nv.gov/old",
            }
        ],
    }

    assert reconcile(document, registry) == []
