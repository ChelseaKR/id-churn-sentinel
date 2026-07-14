"""The published JSON schema is a promise to an integrator — so it is tested, not asserted.

`docs/schema/changes-v2.schema.json` exists so that A4TE, Trans Lifeline, Namesake or a
legal-aid clinic can build against `changes.json` **without reading our source code**. That
is the entire point of publishing a schema, and it creates a failure mode that a
hand-maintained schema document always eventually has: the code moves, the schema does not,
and the document an integrator trusted is now *lying to them* with our name on it. A schema
that has drifted from its implementation is worse than no schema at all, because no schema
at least forces them to look at the real bytes.

So two things are checked here, and they are different:

1. **The schema and the code agree about the vocabulary.** The enums in the schema are
   derived-checked against the actual `StrEnum`s, and the property list against what
   `ChangeRecord.to_dict()` actually emits. Add a field to the record and forget the schema:
   this goes red.
2. **Real published output validates against it.** Not a hand-written example — the actual
   bytes `publish()` writes.

The validator below is a deliberately small subset of JSON Schema 2020-12, hand-written to
keep the runtime dependency count at zero (`pyproject.toml`: an unattended watcher should
not carry a dependency tree that rots faster than the law it watches). It covers exactly the
keywords the schema uses, and it fails loudly on a keyword it does not know rather than
silently passing something it did not check — a validator that quietly ignores a constraint
is how a schema test goes green while enforcing nothing.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.publish import FEED_SCHEMA_VERSION, publish
from id_churn_sentinel.core.registry import (
    DOCUMENT_CLASSES,
    JURISDICTIONS,
    VERIFIED,
    Registry,
    Verification,
)
from id_churn_sentinel.core.site import feed_slug

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "schema" / "changes-v2.schema.json"
STATUS_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "schema" / "status-v1.schema.json"
)
CONSUMERS_PATH = Path(__file__).resolve().parents[1] / "docs" / "CONSUMERS.md"
V1_VERIFICATION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "source-verification-v1.0.schema.json"
)

_KNOWN_KEYWORDS = {
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "title",
    "description",
    "type",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "pattern",
    "minLength",
    "maxLength",
    "format",
    "maximum",
    "minimum",
    "oneOf",
    "allOf",
    "if",
    "then",
    "else",
    "uniqueItems",
}

_RFC3339_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RFC3339_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")


@pytest.fixture
def schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return loaded


def _validate_object(
    instance: dict[str, Any], schema: dict[str, Any], root: dict[str, Any], path: str
) -> list[str]:
    errors = [
        f"{path}: missing required property {key!r}"
        for key in schema.get("required", [])
        if key not in instance
    ]
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        errors.extend(
            f"{path}: property {key!r} is not permitted by the schema"
            for key in instance
            if key not in properties
        )
    for key, value in instance.items():
        if key in properties:
            errors.extend(_validate(value, properties[key], root, f"{path}.{key}"))
    return errors


def _validate_scalar(instance: object, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: {instance!r} is not the required const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of the permitted values")
    if isinstance(instance, str):
        errors.extend(_validate_string(instance, schema, path))
    if isinstance(instance, int | float) and not isinstance(instance, bool):
        errors.extend(_validate_number(instance, schema, path))
    return errors


def _validate_string(instance: str, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    pattern = schema.get("pattern")
    if pattern is not None and not re.search(pattern, instance):
        errors.append(f"{path}: {instance!r} does not match {pattern!r}")
    minimum = schema.get("minLength")
    if minimum is not None and len(instance) < minimum:
        errors.append(f"{path}: {instance!r} is shorter than minLength")
    maximum = schema.get("maxLength")
    if maximum is not None and len(instance) > maximum:
        errors.append(f"{path}: {instance!r} is longer than maxLength")
    format_name = schema.get("format")
    if format_name is not None:
        format_error = _format_error(instance, str(format_name))
        if format_error:
            errors.append(f"{path}: {instance!r} {format_error}")
    return errors


def _validate_number(instance: int | float, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minimum")
    if minimum is not None and instance < minimum:
        errors.append(f"{path}: {instance!r} is below minimum {minimum!r}")
    maximum = schema.get("maximum")
    if maximum is not None and instance > maximum:
        errors.append(f"{path}: {instance!r} is above maximum {maximum!r}")
    return errors


def _date_format_error(instance: str) -> str:
    if not _RFC3339_DATE.fullmatch(instance):
        return "is not an RFC 3339 full-date"
    date.fromisoformat(instance)
    return ""


def _date_time_format_error(instance: str) -> str:
    if not _RFC3339_DATE_TIME.fullmatch(instance):
        return "is not an RFC 3339 date-time with an explicit offset"
    normalized = f"{instance[:-1]}+00:00" if instance[-1].lower() == "z" else instance
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return "is not an RFC 3339 date-time with an explicit offset"
    return ""


def _uri_format_error(instance: str) -> str:
    if any(character.isspace() or ord(character) < 0x20 for character in instance):
        return "is not an absolute URI"
    parsed_uri = urlsplit(instance)
    if not _URI_SCHEME.fullmatch(parsed_uri.scheme):
        return "is not an absolute URI"
    if parsed_uri.scheme.lower() in {"http", "https"} and not parsed_uri.hostname:
        return "is not an absolute HTTP(S) URI"
    _ = parsed_uri.port  # force validation of a malformed explicit port
    return ""


def _format_error(instance: str, format_name: str) -> str:
    """Return an honest validation error for every format used by the public schemas."""

    validators = {
        "date": _date_format_error,
        "date-time": _date_time_format_error,
        "uri": _uri_format_error,
    }
    validator = validators.get(format_name)
    if validator is None:
        return f"uses unsupported schema format {format_name!r}"
    try:
        return validator(instance)
    except ValueError:
        return f"is not a valid {format_name}"


def _validate_one_of(
    instance: object, schema: dict[str, Any], root: dict[str, Any], path: str
) -> list[str]:
    outcomes = [_validate(instance, option, root, path) for option in schema["oneOf"]]
    matches = sum(not errors for errors in outcomes)
    if matches == 1:
        return []
    return [f"{path}: expected exactly one oneOf branch to match; got {matches}"]


def _validate_array(
    instance: object, schema: dict[str, Any], root: dict[str, Any], path: str
) -> list[str]:
    if not isinstance(instance, list):
        return [f"{path}: expected array, got {type(instance).__name__}"]
    if schema.get("uniqueItems") is True:
        canonical = [json.dumps(item, sort_keys=True) for item in instance]
        if len(canonical) != len(set(canonical)):
            return [f"{path}: array items are not unique"]
    item_schema = schema.get("items")
    if item_schema is None:
        return []
    errors: list[str] = []
    for index, item in enumerate(instance):
        errors.extend(_validate(item, item_schema, root, f"{path}[{index}]"))
    return errors


def _validate_conditionals(
    instance: object,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []
    for branch in schema.get("allOf", []):
        errors.extend(_validate(instance, branch, root, path))
    if "if" in schema:
        condition_matches = not _validate(instance, schema["if"], root, path)
        selected = schema.get("then") if condition_matches else schema.get("else")
        if selected is not None:
            errors.extend(_validate(instance, selected, root, path))
    return errors


def _validate(
    instance: object, schema: dict[str, Any], root: dict[str, Any], path: str
) -> list[str]:
    """A minimal JSON Schema 2020-12 validator. Returns a list of violations."""
    unknown = set(schema) - _KNOWN_KEYWORDS
    if unknown:
        # Refusing to pass silently on a keyword we do not implement. A validator that
        # ignores what it does not understand reports success it did not earn.
        return [f"{path}: schema uses keyword(s) this validator does not implement: {unknown}"]

    conditional_errors = _validate_conditionals(instance, schema, root, path)

    if "oneOf" in schema:
        return conditional_errors + _validate_one_of(instance, schema, root, path)

    if "$ref" in schema:
        key = str(schema["$ref"]).removeprefix("#/$defs/")
        return conditional_errors + _validate(instance, root["$defs"][key], root, path)

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            return [f"{path}: expected object, got {type(instance).__name__}"]
        return conditional_errors + _validate_object(instance, schema, root, path)

    if expected == "array":
        return conditional_errors + _validate_array(instance, schema, root, path)

    mistyped = _wrong_primitive_type(instance, expected)
    if mistyped:
        return [*conditional_errors, f"{path}: {mistyped}"]

    return conditional_errors + _validate_scalar(instance, schema, path)


def _wrong_primitive_type(instance: object, expected: object) -> str:
    """`""` when the instance matches the expected primitive type, else the complaint.

    `bool` is an `int` in Python, so the explicit exclusion matters: without it a schema
    saying `"type": "integer"` would happily accept `true`, and a validator that accepts what
    the schema forbids is reporting a success it did not earn.
    """
    got = type(instance).__name__
    if expected == "string" and not isinstance(instance, str):
        return f"expected string, got {got}"
    if expected == "boolean" and not isinstance(instance, bool):
        return f"expected boolean, got {got}"
    if expected == "integer" and (isinstance(instance, bool) or not isinstance(instance, int)):
        return f"expected integer, got {got}"
    if expected == "number" and (
        isinstance(instance, bool) or not isinstance(instance, int | float)
    ):
        return f"expected number, got {got}"
    if expected == "null" and instance is not None:
        return f"expected null, got {got}"
    return ""


def test_the_validator_actually_rejects_something() -> None:
    """Guard the guard. A validator that returns `[]` no matter what would make every other
    test in this file pass while checking nothing — which is the exact failure mode this
    repo caught once already, when `INSERT OR IGNORE` silently swallowed the CHECK
    constraint the safety gate depended on."""
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    assert _validate({}, schema, schema, "$") != []
    assert _validate({"a": 1}, schema, schema, "$") != []
    assert _validate({"a": "ok"}, schema, schema, "$") == []

    typed = {"type": "object", "properties": {"n": {"type": "integer"}, "b": {"type": "boolean"}}}
    assert _validate({"n": True}, typed, typed, "$") != []  # a bool is not an integer here
    assert _validate({"b": 1}, typed, typed, "$") != []
    assert _validate({"n": 3, "b": False}, typed, typed, "$") == []

    date_time = {"type": "string", "format": "date-time"}
    uri = {"type": "string", "format": "uri"}
    assert _validate("2026-07-14T12:00:00Z", date_time, date_time, "$") == []
    assert _validate("definitely-not-a-date", date_time, date_time, "$") != []
    assert _validate("https://example.gov/path", uri, uri, "$") == []
    assert _validate("not a uri", uri, uri, "$") != []


def test_every_consumer_json_example_is_complete_and_schema_valid(
    schema: dict[str, Any],
) -> None:
    """Documentation examples are copied into integrations, so they are contract fixtures."""

    blocks = re.findall(
        r"```json\n(.*?)\n```",
        CONSUMERS_PATH.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )

    assert len(blocks) == 2
    for index, block in enumerate(blocks):
        payload = json.loads(block)
        assert _validate(payload, schema, schema, f"consumer_example[{index}]") == []


def test_the_schemas_enums_match_the_code(schema: dict[str, Any]) -> None:
    """The vocabulary is closed in the code; the schema must close it the same way."""
    change = schema["$defs"]["change"]["properties"]

    assert set(change["jurisdiction"]["enum"]) == set(JURISDICTIONS)
    assert set(change["document_class"]["enum"]) == set(DOCUMENT_CLASSES)
    assert set(change["kind"]["enum"]) == {str(k) for k in ChangeKind}

    # `significance` is NARROWER in the published schema than in the code, and deliberately
    # so: `unclassified` is a real internal state that can never be published, because an
    # unclassified record is unpublishable by construction. The schema documents the
    # CONTRACT, not the internals.
    assert set(change["significance"]["enum"]) == {
        str(Significance.EDITORIAL),
        str(Significance.SUBSTANTIVE),
    }
    assert str(Significance.UNCLASSIFIED) not in change["significance"]["enum"]
    assert change["review_status"]["const"] == str(ReviewStatus.CONFIRMED)


def test_the_schema_describes_every_field_the_code_emits(
    tmp_path: Path,
    schema: dict[str, Any],
    confirmed_change: ChangeRecord,
    registry: Registry,
) -> None:
    """Add a field to a published change and forget the schema: this goes red. That is the
    whole job — an integrator's parser is written against the schema, not against us.

    Compared against the **published item**, not against `ChangeRecord.to_dict()`. A published
    item is the record plus what the *publisher* knows and the record does not: the
    verification status of the source it cites. Comparing against `to_dict()` alone would have
    let `source_verification` ship undocumented — the field that tells an integrator nobody has
    confirmed the URL in the item they are about to act on.
    """
    publish([confirmed_change], tmp_path, registry=registry)
    item = json.loads((tmp_path / "changes.json").read_text())["changes"][0]

    described = set(schema["$defs"]["change"]["properties"])
    emitted = set(item)

    assert emitted - described == set(), "the code emits fields the schema does not describe"
    assert described - emitted == set(), "the schema describes fields the code never emits"
    assert set(schema["$defs"]["change"]["required"]) == emitted
    verification_schema = schema["$defs"]["verification"]
    assert set(item["source_verification"]) == set(verification_schema["properties"])
    assert set(verification_schema["required"]) == set(item["source_verification"])


def test_internal_eligibility_metadata_preserves_the_closed_v1_feed_contract() -> None:
    verification = Verification(
        status=VERIFIED,
        verifier="Source Reviewer",
        at="2026-07-12",
        note="reviewed",
        evidence="evidence/verification/source.json",
        expires_at="2027-07-12",
    )
    payload = verification.to_dict()
    prior_schema = json.loads(V1_VERIFICATION_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert _validate(payload, prior_schema, prior_schema, "$") == []
    assert set(payload) == {"status", "verifier", "verified_at", "note", "statement"}


def test_the_schema_describes_every_field_of_a_published_source(
    tmp_path: Path, schema: dict[str, Any], registry: Registry
) -> None:
    """The `sources` array ships inside every change feed, and every entry in it carries a
    verification status. The schema has to describe that array exactly, for the same reason it
    describes the change array: an integrator builds against the schema."""
    publish([], tmp_path, registry=registry)
    source = json.loads((tmp_path / "changes.json").read_text())["sources"][0]

    described = set(schema["$defs"]["source"]["properties"])

    assert set(source) == described
    assert set(schema["$defs"]["source"]["required"]) == set(source)
    assert source["verification_status"] == "verified"


def test_the_schema_version_matches_the_publisher(schema: dict[str, Any]) -> None:
    assert re.match(schema["properties"]["schema_version"]["pattern"], FEED_SCHEMA_VERSION)


def test_real_published_output_validates(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    """Not a hand-written example — the actual bytes `publish()` writes."""
    publish([confirmed_change], tmp_path, registry=registry)
    document = json.loads((tmp_path / "changes.json").read_text())

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert _validate(document, schema, schema, "$") == []
    assert document["changes"][0]["review_status"] == "confirmed"


def test_an_empty_feed_validates_too(tmp_path: Path, registry: Registry) -> None:
    """The current, real state of this feed. If the empty document did not validate, every
    consumer's first poll would fail — and an integrator whose first poll errors concludes
    the feed is broken and never comes back."""
    publish([], tmp_path, registry=registry)
    document = json.loads((tmp_path / "changes.json").read_text())

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert _validate(document, schema, schema, "$") == []
    assert document["changes"] == []


def test_real_public_status_validates_against_its_closed_schema(
    tmp_path: Path, registry: Registry
) -> None:
    publish([], tmp_path, registry=registry, now=datetime(2026, 7, 13, tzinfo=UTC))
    document = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    status_schema = json.loads(STATUS_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert _validate(document, status_schema, status_schema, "$") == []
    assert document["state"] == "stale"
    assert document["last_attempted_run"] is None


def test_a_per_jurisdiction_document_validates_and_says_what_it_is_scoped_to(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    """A scoped document that does not say what it is scoped to is a trap: an empty
    `changes-us-tx.json` must not be mistakable for a statement about the whole country."""
    publish([confirmed_change], tmp_path, registry=registry)
    slug = feed_slug(confirmed_change.jurisdiction)
    document = json.loads((tmp_path / f"changes-{slug}.json").read_text())

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert _validate(document, schema, schema, "$") == []
    assert document["jurisdiction"] == confirmed_change.jurisdiction


def test_an_unreviewed_record_would_not_validate(
    tmp_path: Path, observed_change: ChangeRecord
) -> None:
    """The schema is not merely descriptive — it *encodes* the safety property. If a bug ever
    published an unreviewed record, a consumer validating against our own published schema
    would reject it, which is one more independent place the promise is kept."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    smuggled = {
        "schema_version": FEED_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "feed_url": "https://example.gov/",
        "disclaimer": "…",
        "changes": [observed_change.to_dict()],
    }

    errors = _validate(smuggled, schema, schema, "$")

    assert errors, "the schema must refuse an unreviewed record"
    assert any("review_status" in error for error in errors)
    assert any("significance" in error for error in errors)


def test_v2_schema_rejects_incoherent_review_lifecycle_and_private_notes(
    tmp_path: Path,
    confirmed_change: ChangeRecord,
    registry: Registry,
    schema: dict[str, Any],
) -> None:
    publish([confirmed_change], tmp_path, registry=registry)
    item = json.loads((tmp_path / "changes.json").read_text())["changes"][0]
    change_schema = schema["$defs"]["change"]

    missing_second = copy.deepcopy(item)
    missing_second["independent_review_status"] = None
    missing_second["independent_reviewer"] = None
    missing_second["independent_reviewed_at"] = None

    corrected_without_successor = copy.deepcopy(item)
    corrected_without_successor.update(
        publication_status="corrected",
        superseded_by=None,
        lifecycle_reason="review_error",
        lifecycle_actor="Correction Reviewer",
        lifecycle_at=datetime.now(UTC).isoformat(),
    )

    active_with_lifecycle = copy.deepcopy(item)
    active_with_lifecycle.update(
        publication_status="active",
        superseded_by="c" * 16,
        lifecycle_reason="review_error",
        lifecycle_actor="Correction Reviewer",
        lifecycle_at=datetime.now(UTC).isoformat(),
    )

    editorial_with_second = copy.deepcopy(item)
    editorial_with_second["significance"] = "editorial"

    removal_with_invented_content = copy.deepcopy(item)
    removal_with_invented_content["kind"] = "possibly_removed"

    content_drift_without_content = copy.deepcopy(item)
    content_drift_without_content["new_hash"] = ""

    for malformed in (
        missing_second,
        corrected_without_successor,
        active_with_lifecycle,
        editorial_with_second,
        removal_with_invented_content,
        content_drift_without_content,
    ):
        assert _validate(malformed, change_schema, schema, "$.change")

    note_leak = copy.deepcopy(item)
    note_leak["source_verification"]["note"] = "private registry rationale"
    errors = _validate(note_leak, change_schema, schema, "$.change")
    assert any("source_verification.note" in error for error in errors)
