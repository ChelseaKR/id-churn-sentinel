"""`sentinel stale` — a consumer's own pages, against the published feed.

The property under test throughout: this command cannot report a consumer's page as fine on the
strength of something it never watched. A citation outside the registry is `unwatched`, a
citation that matches only a host is `host_only`, and neither is ever silently folded into "no
changes since your review date".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.registry import Registry, load_registry
from id_churn_sentinel.core.staleness import (
    MANIFEST_SCHEMA_VERSION,
    MATCH_KINDS,
    ManifestError,
    load_changes_document,
    load_manifest,
    normalize_url,
    render_text,
    staleness_report,
)
from tests.test_schema import _validate

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "schema" / "consumer-manifest-v1.schema.json"
)

WATCHED = "https://dps.texas.gov/section/driver-license/change-name"
SAME_HOST = "https://dps.texas.gov/some/other/guidance"
ELSEWHERE = "https://example.invalid/a-page-nobody-watches"


def _registry(tmp_path: Path) -> Registry:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "registry_version": "1.0",
                "sources": [
                    {
                        "id": "tx-dps-name-change",
                        "jurisdiction": "TX",
                        "document_class": "drivers_license",
                        "url": WATCHED,
                        "authority": "Texas Department of Public Safety",
                        "verified": False,
                        "notes": "fixture",
                    }
                ],
                "gaps": [],
            }
        ),
        encoding="utf-8",
    )
    return load_registry(path)


def _manifest(tmp_path: Path, pages: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": MANIFEST_SCHEMA_VERSION, "pages": pages, **extra}),
        encoding="utf-8",
    )
    return path


def _change(**overrides: Any) -> dict[str, Any]:
    change: dict[str, Any] = {
        "id": "chg-0001",
        "source_id": "tx-dps-name-change",
        "jurisdiction": "TX",
        "document_class": "drivers_license",
        "url": WATCHED,
        "observed_at": "2026-08-30T00:00:00+00:00",
        "significance": "substantive",
        "review_status": "confirmed",
        "reviewer": "A Named Human",
        "reviewed_at": "2026-08-31",
        "independent_review_status": "confirmed",
        "independent_reviewer": "A Second Named Human",
        "publication_status": "active",
    }
    change.update(overrides)
    return change


def _changes(tmp_path: Path, changes: list[dict[str, Any]]) -> Path:
    path = tmp_path / "changes.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "generated_at": "2026-09-01T00:00:00+00:00",
                "feed_url": "https://example.test/",
                "changes": changes,
                "sources": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _report(tmp_path: Path, pages: list[dict[str, Any]], changes: list[dict[str, Any]]) -> Any:
    return staleness_report(
        load_manifest(_manifest(tmp_path, pages)),
        load_changes_document(_changes(tmp_path, changes)),
        _registry(tmp_path),
    )


# ---- the three "done when" criteria ----------------------------------------------------------


def test_a_manifest_yields_the_expected_stale_rows_and_an_unwatched_row(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [
            {
                "id": "tx-name-change",
                "title": "Changing your name on a Texas licence",
                "last_reviewed": "2026-06-01",
                "cites": [WATCHED, ELSEWHERE],
            }
        ],
        [_change()],
    )

    (page,) = report["pages"]
    matches = {c["url"]: c["match"] for c in page["citations"]}
    assert matches == {WATCHED: "matched", ELSEWHERE: "unwatched"}
    (row,) = page["stale"]
    assert row["change_id"] == "chg-0001"
    assert row["significance"] == "substantive"
    assert row["reviewer"] == "A Named Human"
    assert row["independent_reviewer"] == "A Second Named Human"
    assert row["verification_status"] == "unverified"
    assert report["summary"]["citations_not_watched"] == 1


def test_a_page_reviewed_after_every_change_reports_nothing(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-09-01", "cites": [WATCHED]}],
        [_change()],
    )

    (page,) = report["pages"]
    assert page["stale"] == []
    assert page["citations"][0]["match"] == "matched"
    assert "no confirmed change" in render_text(report)


@pytest.mark.parametrize(
    "overrides",
    [
        {"review_status": "unreviewed"},
        {"review_status": "dismissed"},
        {"publication_status": "withdrawn"},
        {"significance": "unclassified"},
        {"significance": "substantive", "independent_review_status": "returned"},
        {"significance": "substantive", "independent_review_status": None},
    ],
    ids=[
        "unreviewed",
        "dismissed",
        "withdrawn",
        "unclassified",
        "independent-review-returned",
        "no-independent-review",
    ],
)
def test_a_change_a_human_did_not_publish_never_reaches_the_output(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    """The committed-feed safety property, re-asserted at the consumer's edge.

    `docs/changes.json` can never carry one of these — `publish()` refuses. But `--changes`
    takes any conforming file, and an unreviewed hash change surfaced to a clinic as "your page
    is out of date" is exactly the claim this project refuses to make.
    """
    report = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-06-01", "cites": [WATCHED]}],
        [_change(**overrides)],
    )

    (page,) = report["pages"]
    assert page["stale"] == []
    assert report["summary"]["changes_considered"] == 0
    assert report["summary"]["changes_in_input"] == 1, (
        "the report hides that it discarded an input record; a reader cannot tell a filtered "
        "feed from an empty one"
    )


# ---- absence is reported as absence ------------------------------------------------------------


def test_a_same_host_citation_is_host_only_and_never_matched(tmp_path: Path) -> None:
    """The registry watches one page on a host. It does not watch the host."""
    report = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-06-01", "cites": [SAME_HOST]}],
        [_change()],
    )

    (page,) = report["pages"]
    (citation,) = page["citations"]
    assert citation["match"] == "host_only"
    assert citation["same_host_sources"] == ["tx-dps-name-change"]
    assert citation["source_id"] == ""
    assert page["stale"] == [], "a host-only citation inherited another page's change"
    assert "HOST ONLY" in render_text(report)


def test_a_citation_differing_only_by_a_trailing_slash_is_not_claimed_as_matched(
    tmp_path: Path,
) -> None:
    """A normalizer that guessed here would report an unwatched page as watched."""
    report = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-06-01", "cites": [WATCHED + "/"]}],
        [_change()],
    )

    (citation,) = report["pages"][0]["citations"]
    assert citation["match"] == "host_only"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://DPS.Texas.GOV/x", "https://dps.texas.gov/x"),
        ("HTTPS://dps.texas.gov/x", "https://dps.texas.gov/x"),
        ("https://dps.texas.gov:443/x", "https://dps.texas.gov/x"),
        ("https://dps.texas.gov/x#anchor", "https://dps.texas.gov/x"),
    ],
)
def test_url_identity_covers_only_what_the_rfc_already_grants(a: str, b: str) -> None:
    assert normalize_url(a) == normalize_url(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://dps.texas.gov/x/", "https://dps.texas.gov/x"),
        ("https://dps.texas.gov/x?lang=es", "https://dps.texas.gov/x"),
        ("https://dps.texas.gov:8443/x", "https://dps.texas.gov/x"),
    ],
)
def test_url_identity_does_not_guess(a: str, b: str) -> None:
    assert normalize_url(a) != normalize_url(b)


def test_a_change_whose_date_cannot_be_read_is_kept_rather_than_dropped(tmp_path: Path) -> None:
    """The one place this module rounds, and it rounds toward telling the consumer.

    A change we cannot date is a change we cannot rule out. Dropping it would remove a real
    item from a staleness report on the strength of a malformed field.
    """
    report = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-06-01", "cites": [WATCHED]}],
        [_change(observed_at="")],
    )

    assert len(report["pages"][0]["stale"]) == 1


def test_the_report_says_which_date_it_compared_and_carries_both(tmp_path: Path) -> None:
    """A change observed BEFORE the review date but confirmed after is not stale for this page.

    The consumer reviewed their page after the source moved. Comparing against `reviewed_at`
    instead would report a page that is fine as one that is not — a different, equally
    plausible number under the same label.
    """
    report = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-06-01", "cites": [WATCHED]}],
        [_change(observed_at="2026-05-01T00:00:00+00:00", reviewed_at="2026-07-01")],
    )

    assert report["compared_on"] == "observed_at"
    assert report["pages"][0]["stale"] == []

    later = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-06-01", "cites": [WATCHED]}],
        [_change()],
    )
    (row,) = later["pages"][0]["stale"]
    assert row["observed_at"].startswith("2026-08-30")
    assert row["reviewed_at"] == "2026-08-31"


def test_the_match_vocabulary_is_closed(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [{"id": "tx", "last_reviewed": "2026-06-01", "cites": [WATCHED, SAME_HOST, ELSEWHERE]}],
        [_change()],
    )
    kinds = {c["match"] for c in report["pages"][0]["citations"]}
    assert kinds == MATCH_KINDS


# ---- the manifest is validated, never partially read --------------------------------------------


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"schema_version": "9.9", "pages": []}, "is not the supported"),
        ({"schema_version": "1.0"}, "non-empty list"),
        ({"schema_version": "1.0", "pages": []}, "non-empty list"),
        ({"schema_version": "1.0", "pages": ["x"], "extra": 1}, "unknown field"),
        (
            {"schema_version": "1.0", "pages": [{"id": "a", "last_reviewed": "2026-01-01"}]},
            "is missing",
        ),
        (
            {
                "schema_version": "1.0",
                "pages": [
                    {"id": "a", "last_reviewed": "2026-01-01", "cites": ["https://x/"], "z": 1}
                ],
            },
            "unknown field",
        ),
        (
            {
                "schema_version": "1.0",
                "pages": [{"id": "a", "last_reviewed": "not-a-date", "cites": ["https://x/"]}],
            },
            "YYYY-MM-DD",
        ),
        (
            {
                "schema_version": "1.0",
                "pages": [{"id": "", "last_reviewed": "2026-01-01", "cites": ["https://x/"]}],
            },
            "is empty",
        ),
    ],
)
def test_a_malformed_manifest_is_refused(tmp_path: Path, document: Any, expected: str) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestError, match=expected):
        load_manifest(path)


def test_a_plaintext_citation_is_refused(tmp_path: Path) -> None:
    """Same rule the registry applies to its own sources, for the same reason.

    A plaintext fetch of a government page is observable and modifiable by anyone on the path,
    and the people this feed exists for are the ones least able to afford that.
    """
    path = _manifest(
        tmp_path,
        [{"id": "a", "last_reviewed": "2026-01-01", "cites": ["http://dps.texas.gov/x"]}],
    )
    with pytest.raises(ManifestError, match="non-https URL"):
        load_manifest(path)


def test_a_duplicate_page_id_is_refused(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        [
            {"id": "a", "last_reviewed": "2026-01-01", "cites": [WATCHED]},
            {"id": "a", "last_reviewed": "2026-02-01", "cites": [WATCHED]},
        ],
    )
    with pytest.raises(ManifestError, match="twice"):
        load_manifest(path)


def test_a_missing_manifest_or_feed_is_named(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="manifest not found"):
        load_manifest(tmp_path / "nope.json")
    with pytest.raises(ManifestError, match="changes document not found"):
        load_changes_document(tmp_path / "nope.json")


def test_a_file_that_is_not_a_changes_document_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    with pytest.raises(ManifestError, match="not a changes document"):
        load_changes_document(path)


# ---- the published schema -----------------------------------------------------------------------


def _schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return loaded


def test_the_documented_example_manifest_validates_against_the_schema() -> None:
    """The example in `docs/CONSUMERS.md` is the thing consumers will copy; it is checked."""
    consumers = (Path(__file__).resolve().parents[1] / "docs" / "CONSUMERS.md").read_text(
        encoding="utf-8"
    )
    marker = '"schema_version": "1.0",\n  "site":'
    assert marker in consumers, "the documented manifest example has moved or been removed"
    start = consumers.index("```json\n{\n  " + marker.split(",")[0])
    body = consumers[start + len("```json\n") :]
    example = json.loads(body[: body.index("\n```")])
    schema = _schema()
    assert _validate(example, schema, schema, "$") == []


def test_the_schema_rejects_a_plaintext_citation() -> None:
    schema = _schema()
    document = {
        "schema_version": "1.0",
        "pages": [{"id": "a", "last_reviewed": "2026-01-01", "cites": ["http://dps.texas.gov/x"]}],
    }
    assert _validate(document, schema, schema, "$") != []


def test_the_validator_this_file_relies_on_still_rejects_something() -> None:
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    assert _validate({}, schema, schema, "$") != []


# ---- the command -----------------------------------------------------------------------------


def test_the_command_runs_from_a_clean_clone_against_the_committed_feed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `--changes`, no network: the committed `docs/changes.json` is the default."""
    real_url = load_registry().sources[0].url
    path = _manifest(tmp_path, [{"id": "p", "last_reviewed": "2026-01-01", "cites": [real_url]}])

    assert main(["stale", "--manifest", str(path)]) == 0

    out = capsys.readouterr().out
    assert "watched" in out
    assert "consumer pages:" in out


def test_the_command_emits_json_and_exits_zero_with_stale_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A page citing a changed source is a page somebody should look at, not a broken tool."""
    real_url = load_registry().sources[0].url
    real_id = load_registry().sources[0].id
    path = _manifest(tmp_path, [{"id": "p", "last_reviewed": "2026-01-01", "cites": [real_url]}])
    changes = _changes(tmp_path, [_change(source_id=real_id, url=real_url)])

    assert main(["stale", "--manifest", str(path), "--changes", str(changes), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["pages_with_stale_citations"] == 1
    assert payload["pages"][0]["stale"][0]["change_id"] == "chg-0001"


def test_the_command_reports_a_malformed_manifest_rather_than_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "m.json"
    path.write_text("{}", encoding="utf-8")
    assert main(["stale", "--manifest", str(path)]) == 1
    assert "error:" in capsys.readouterr().err


def test_the_schema_rejects_a_manifest_with_no_pages_and_a_page_citing_nothing() -> None:
    """`minItems` is enforced, not merely written down.

    `tests/test_schema.py`'s validator refuses a keyword it does not implement rather than
    passing it silently, so this constraint had to be implemented to be stated at all — and a
    keyword that is implemented but never exercised is a constraint nobody has checked.
    """
    schema = _schema()
    assert _validate({"schema_version": "1.0", "pages": []}, schema, schema, "$") != []
    page = {"id": "a", "last_reviewed": "2026-01-01", "cites": []}
    assert _validate({"schema_version": "1.0", "pages": [page]}, schema, schema, "$") != []
