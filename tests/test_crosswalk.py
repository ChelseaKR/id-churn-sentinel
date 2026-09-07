"""`sentinel crosswalk` — which of an outside list of URLs this registry already covers.

The property under test throughout is that **`host_only` is never reported as coverage**.
Every other assertion supports it: the vocabulary is closed so a consumer can branch
exhaustively, precedence between a gap and a same-host source is fixed and tested, and the
output has no clock so two runs over one input are byte-identical.

The three criteria this feature was specified against are the first three tests, named as
such, so a reader can find them without reading the issue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.crosswalk import (
    CROSSWALK_SCHEMA_VERSION,
    MATCH_GAP,
    MATCH_HOST_ONLY,
    MATCH_KINDS,
    MATCH_SOURCE,
    MATCH_UNMATCHED,
    CrosswalkError,
    crosswalk_document,
    crosswalk_report,
    dumps_crosswalk,
    load_urls,
    render_text,
)
from id_churn_sentinel.core.registry import Registry, load_registry
from tests.test_schema import _validate

_SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "schema" / "crosswalk-v1.schema.json"

_REGISTRY_URL = "https://www.dps.texas.gov/section/driver-license/change-name"
_SAME_HOST_URL = "https://www.dps.texas.gov/section/driver-license/renew"
_GAP_HOST_URL = "https://dmv.vermont.gov/licenses/gender-designation"
_UNKNOWN_URL = "https://example.invalid/some-page"


def _source(
    source_id: str = "tx-dps-name-change",
    *,
    url: str = _REGISTRY_URL,
    jurisdiction: str = "TX",
    document_class: str = "drivers_license",
) -> dict[str, Any]:
    return {
        "id": source_id,
        "jurisdiction": jurisdiction,
        "document_class": document_class,
        "url": url,
        "authority": "Texas Department of Public Safety",
        "verified": False,
        "notes": "fixture",
    }


def _gap(
    jurisdiction: str = "VT",
    document_class: str = "drivers_license",
    reason: str = "robots-disallowed",
    hosts: tuple[str, ...] = ("dmv.vermont.gov",),
) -> dict[str, Any]:
    return {
        "jurisdiction": jurisdiction,
        "document_class": document_class,
        "reason": reason,
        "hosts": list(hosts),
        "checked": "2026-05-01",
        "detail": "fixture gap",
    }


def _registry(
    tmp_path: Path,
    sources: list[Any] | None = None,
    gaps: list[Any] | None = None,
    *,
    name: str = "registry",
) -> Registry:
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "registry_version": "1.0",
                "sources": sources if sources is not None else [_source()],
                "gaps": gaps if gaps is not None else [_gap()],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return load_registry(path)


def _write_urls(tmp_path: Path, urls: list[str], *, name: str = "urls.txt") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return path


# ---- the three criteria this feature was specified against --------------------------------


def test_one_registry_one_same_host_one_gap_host_and_one_unknown_yield_four_kinds(
    tmp_path: Path,
) -> None:
    """The whole vocabulary, exercised on one list, in one assertion."""
    report = crosswalk_report(
        _registry(tmp_path),
        [_REGISTRY_URL, _SAME_HOST_URL, _GAP_HOST_URL, _UNKNOWN_URL],
    )
    by_url = {row.url: row for row in report.rows}
    assert by_url[_REGISTRY_URL].match == MATCH_SOURCE
    assert by_url[_REGISTRY_URL].source_id == "tx-dps-name-change"
    assert by_url[_SAME_HOST_URL].match == MATCH_HOST_ONLY
    assert by_url[_GAP_HOST_URL].match == MATCH_GAP
    assert by_url[_GAP_HOST_URL].gap_reason == "robots-disallowed"
    assert by_url[_UNKNOWN_URL].match == MATCH_UNMATCHED
    assert report.summary == {
        MATCH_SOURCE: 1,
        MATCH_GAP: 1,
        MATCH_HOST_ONLY: 1,
        MATCH_UNMATCHED: 1,
        "total": 4,
    }


def test_the_committed_registrys_own_urls_all_match_exactly() -> None:
    """100 percent exact matches, or the normalizer disagrees with the registry it reads.

    This is the criterion that would catch a normalizer change: any transform that alters a
    registry URL's identity shows up here as a `host_only` row against the registry's own
    entry, which is the same failure a consumer would hit silently.
    """
    registry = load_registry()
    report = crosswalk_report(registry, [source.url for source in registry.sources])
    assert report.summary[MATCH_SOURCE] == len(registry.sources)
    assert report.summary[MATCH_HOST_ONLY] == 0
    assert report.summary[MATCH_UNMATCHED] == 0
    assert report.summary[MATCH_GAP] == 0


def test_output_is_byte_identical_across_two_runs_and_across_input_ordering(
    tmp_path: Path,
) -> None:
    """No clock, and no dependence on the order a consumer listed their citations in.

    The reversal is the half that matters: a report keyed off input order would be stable on
    repeat and still differ between two consumers holding the same set of URLs.
    """
    registry = _registry(tmp_path)
    urls = [_REGISTRY_URL, _SAME_HOST_URL, _GAP_HOST_URL, _UNKNOWN_URL]
    first = dumps_crosswalk(crosswalk_document(crosswalk_report(registry, urls)))
    again = dumps_crosswalk(crosswalk_document(crosswalk_report(registry, urls)))
    reversed_order = dumps_crosswalk(
        crosswalk_document(crosswalk_report(registry, list(reversed(urls))))
    )
    assert first == again
    assert first == reversed_order
    assert "generated_at" not in first


# ---- the property the whole module exists to hold -----------------------------------------


def test_a_same_host_page_is_never_reported_as_a_watched_source(tmp_path: Path) -> None:
    """The one collapse that would make this command actively harmful.

    A consumer reading `host_only` as coverage would treat this registry's silence about
    their page as evidence about their page. The row names the neighbouring source so the
    consumer can see WHY the host is known, and the match kind says plainly that it is not
    the same page.
    """
    report = crosswalk_report(_registry(tmp_path), [_SAME_HOST_URL])
    (row,) = report.rows
    assert row.match == MATCH_HOST_ONLY
    assert row.match != MATCH_SOURCE
    assert row.source_id == "tx-dps-name-change"
    assert "NOT coverage" in render_text(report)


def test_a_trailing_slash_is_host_only_rather_than_a_guessed_match(tmp_path: Path) -> None:
    """The normalizer's conservatism, asserted rather than assumed.

    `/change-name` and `/change-name/` are the same page on most servers and different pages
    on some. Reporting the second as `source` would be a guess presented as a fact.
    """
    report = crosswalk_report(_registry(tmp_path), [_REGISTRY_URL + "/"])
    assert report.rows[0].match == MATCH_HOST_ONLY


@pytest.mark.parametrize(
    "spelling",
    [
        "HTTPS://WWW.DPS.TEXAS.GOV/section/driver-license/change-name",
        "https://www.dps.texas.gov:443/section/driver-license/change-name",
        "https://www.dps.texas.gov/section/driver-license/change-name#anchor",
    ],
)
def test_identities_the_rfc_already_grants_do_match(tmp_path: Path, spelling: str) -> None:
    """Case, a default port and an empty fragment are the same URL, not a near miss."""
    report = crosswalk_report(_registry(tmp_path), [spelling])
    assert report.rows[0].match == MATCH_SOURCE


def test_a_gap_outranks_a_same_host_source_on_the_same_host(tmp_path: Path) -> None:
    """When both are true, the reviewed decision is the more informative answer.

    A same-host source is an accident of hosting. A gap is a dated statement that this
    registry looked at the host and decided not to watch it, and it carries the reason.
    """
    registry = _registry(
        tmp_path,
        sources=[_source(url="https://dmv.vermont.gov/something-else", jurisdiction="VT")],
        gaps=[_gap()],
    )
    report = crosswalk_report(registry, ["https://dmv.vermont.gov/licenses/gender-designation"])
    (row,) = report.rows
    assert row.match == MATCH_GAP
    assert row.gap_reason == "robots-disallowed"


def test_an_unmatched_row_carries_no_empty_source_fields(tmp_path: Path) -> None:
    """A `null` source_id invites a reader to treat the field as present-but-empty."""
    document = crosswalk_document(crosswalk_report(_registry(tmp_path), [_UNKNOWN_URL]))
    (row,) = document["rows"]
    assert set(row) == {"url", "normalized_url", "host", "match"}


def test_every_match_kind_appears_in_the_summary_even_at_zero(tmp_path: Path) -> None:
    """Otherwise "no unmatched URLs" and "no notion of unmatched" look identical."""
    summary = crosswalk_report(_registry(tmp_path), [_REGISTRY_URL]).summary
    assert set(summary) == {*MATCH_KINDS, "total"}
    assert summary[MATCH_UNMATCHED] == 0


# ---- the filter ----------------------------------------------------------------------------


def test_the_jurisdiction_filter_narrows_the_registry_side_and_says_so(tmp_path: Path) -> None:
    """A URL watched under ANOTHER jurisdiction is `unmatched` under the filter.

    That is the honest answer to the narrowed question, and the filter travels on the
    document so a reader cannot mistake a filtered report for a whole-registry one.
    """
    registry = _registry(tmp_path)
    document = crosswalk_document(crosswalk_report(registry, [_REGISTRY_URL], jurisdiction="VT"))
    assert document["jurisdiction"] == "VT"
    assert document["rows"][0]["match"] == MATCH_UNMATCHED
    assert crosswalk_document(crosswalk_report(registry, [_REGISTRY_URL]))["jurisdiction"] is None


def test_an_unknown_jurisdiction_is_an_error_not_an_empty_registry_side(tmp_path: Path) -> None:
    """`--jurisdiction XY` matching nothing would report a clean sheet from a typo."""
    with pytest.raises(CrosswalkError, match="unknown jurisdiction"):
        crosswalk_report(_registry(tmp_path), [_REGISTRY_URL], jurisdiction="XY")


# ---- the input -------------------------------------------------------------------------------


def test_the_three_accepted_input_shapes_all_yield_the_same_urls(tmp_path: Path) -> None:
    """A consumer brings whichever of the three they already have."""
    plain = _write_urls(tmp_path, [_REGISTRY_URL, _UNKNOWN_URL], name="plain.txt")
    array = tmp_path / "array.json"
    array.write_text(json.dumps([_REGISTRY_URL, {"url": _UNKNOWN_URL}]), encoding="utf-8")
    keyed = tmp_path / "baselines.json"
    keyed.write_text(
        json.dumps({_REGISTRY_URL: {"sha256": "x"}, _UNKNOWN_URL: {"sha256": "y"}}),
        encoding="utf-8",
    )
    assert load_urls(plain) == load_urls(array) == load_urls(keyed)


def test_comments_and_blank_lines_are_ignored_in_a_plain_list(tmp_path: Path) -> None:
    path = tmp_path / "urls.txt"
    path.write_text(f"# a heading\n\n{_REGISTRY_URL}\n\n", encoding="utf-8")
    assert load_urls(path) == (_REGISTRY_URL,)


def test_a_repeated_url_yields_one_row_not_two(tmp_path: Path) -> None:
    path = _write_urls(tmp_path, [_REGISTRY_URL, _REGISTRY_URL])
    assert load_urls(path) == (_REGISTRY_URL,)


@pytest.mark.parametrize(
    "entry",
    ["www.dps.texas.gov/section", "/section/driver-license", "ftp://dps.texas.gov/x", "not a url"],
)
def test_an_unparseable_entry_is_refused_rather_than_reported_as_unmatched(
    tmp_path: Path, entry: str
) -> None:
    """`unmatched` is a statement about the registry, not about the input.

    Letting a typo become an `unmatched` row would tell a consumer this registry has not
    considered a URL that this tool never managed to read.
    """
    path = _write_urls(tmp_path, [entry])
    with pytest.raises(CrosswalkError, match="not an absolute http"):
        load_urls(path)


def test_an_empty_list_is_refused_because_a_clean_sheet_reads_like_a_clean_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("# nothing but a comment\n", encoding="utf-8")
    with pytest.raises(CrosswalkError, match="is empty"):
        load_urls(path)


def test_a_missing_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(CrosswalkError, match="URL list not found"):
        load_urls(tmp_path / "absent.txt")


def test_malformed_json_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CrosswalkError, match="not valid JSON"):
        load_urls(path)


def test_a_json_array_entry_that_is_neither_a_string_nor_a_url_object_is_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "array.json"
    path.write_text(json.dumps([_REGISTRY_URL, 42]), encoding="utf-8")
    with pytest.raises(CrosswalkError, match="neither a URL string"):
        load_urls(path)


def test_a_json_scalar_reaches_the_line_parser_and_is_refused_there(tmp_path: Path) -> None:
    """A bare JSON string is not one of the three shapes, and does not silently become one.

    It is refused as an unparseable entry rather than by a separate "expected an object or
    an array" branch, because such a branch would be unreachable: only text beginning `{` or
    `[` reaches the JSON parser at all.
    """
    path = tmp_path / "scalar.json"
    path.write_text('"just-a-string"', encoding="utf-8")
    with pytest.raises(CrosswalkError, match="not an absolute http"):
        load_urls(path)


def test_a_list_larger_than_the_cap_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "huge.txt"
    path.write_text("https://example.gov/x\n" * 400_000, encoding="utf-8")
    with pytest.raises(CrosswalkError, match="larger than"):
        load_urls(path)


def test_a_list_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "latin.txt"
    path.write_bytes(b"https://example.gov/\xff\n")
    with pytest.raises(CrosswalkError, match="not valid UTF-8"):
        load_urls(path)


# ---- the published shape -----------------------------------------------------------------


def test_the_document_validates_against_its_published_schema(tmp_path: Path) -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    document = crosswalk_document(
        crosswalk_report(
            _registry(tmp_path),
            [_REGISTRY_URL, _SAME_HOST_URL, _GAP_HOST_URL, _UNKNOWN_URL],
        )
    )
    assert _validate(document, schema, schema, "$") == []


def test_the_schema_enum_matches_the_code() -> None:
    """A schema that has drifted from its implementation lies with our name on it."""
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    published = schema["properties"]["rows"]["items"]["properties"]["match"]["enum"]
    assert tuple(published) == MATCH_KINDS
    assert schema["properties"]["schema_version"]["enum"] == [CROSSWALK_SCHEMA_VERSION]


def test_the_validator_this_file_relies_on_still_rejects_something(tmp_path: Path) -> None:
    """Otherwise the schema test above could be green against a validator that passes all."""
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    document = crosswalk_document(crosswalk_report(_registry(tmp_path), [_UNKNOWN_URL]))
    document["rows"][0]["match"] = "definitely-not-a-kind"
    assert _validate(document, schema, schema, "$") != []


# ---- the reverse view, and the CLI ----------------------------------------------------------


def test_sources_json_publishes_the_identity_a_consumer_must_join_on() -> None:
    """The reverse of this command: the join without needing this tool installed.

    Asserted against `crosswalk`'s own matcher rather than against a re-implementation, so
    the published field is the one a `source` match is actually decided on.
    """
    from datetime import UTC, datetime

    from id_churn_sentinel.core.publish import sources_json

    registry = load_registry()
    document = json.loads(sources_json(registry, generated_at=datetime(2026, 1, 1, tzinfo=UTC)))
    published = {entry["source_id"]: entry for entry in document["sources"]}
    report = crosswalk_report(registry, [source.url for source in registry.sources])
    for row in report.rows:
        assert row.source_id is not None
        assert published[row.source_id]["normalized_url"] == row.normalized_url
        assert published[row.source_id]["host"] == row.host


def test_the_cli_writes_a_file_and_prints_a_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    urls = _write_urls(tmp_path, [_REGISTRY_URL, _UNKNOWN_URL])
    output = tmp_path / "crosswalk.json"
    assert main(["crosswalk", "--urls", str(urls), "--output", str(output)]) == 0
    assert "wrote" in capsys.readouterr().out
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == CROSSWALK_SCHEMA_VERSION
    assert document["summary"]["total"] == 2


def test_the_cli_prints_json_and_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    urls = _write_urls(tmp_path, [_REGISTRY_URL])
    assert main(["crosswalk", "--urls", str(urls), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["total"] == 1
    assert main(["crosswalk", "--urls", str(urls)]) == 0
    assert "crosswalk against the committed registry" in capsys.readouterr().out


def test_the_cli_reports_a_bad_list_as_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    urls = _write_urls(tmp_path, ["not-a-url"])
    assert main(["crosswalk", "--urls", str(urls)]) == 1
    assert "not an absolute http" in capsys.readouterr().err
