"""The published JSON schema is a promise to an integrator — so it is tested, not asserted.

`docs/schema/changes-v1.schema.json` exists so that A4TE, Trans Lifeline, Namesake or a
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

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.publish import FEED_SCHEMA_VERSION, publish
from id_churn_sentinel.core.registry import DOCUMENT_CLASSES, JURISDICTIONS, Registry
from id_churn_sentinel.core.site import feed_slug

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "schema" / "changes-v1.schema.json"
CONSUMERS_PATH = Path(__file__).resolve().parents[1] / "docs" / "CONSUMERS.md"

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
    "format",
}


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
        pattern = schema.get("pattern")
        if pattern is not None and not re.search(pattern, instance):
            errors.append(f"{path}: {instance!r} does not match {pattern!r}")
        minimum = schema.get("minLength")
        if minimum is not None and len(instance) < minimum:
            errors.append(f"{path}: {instance!r} is shorter than minLength")
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

    if "$ref" in schema:
        key = str(schema["$ref"]).removeprefix("#/$defs/")
        return _validate(instance, root["$defs"][key], root, path)

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            return [f"{path}: expected object, got {type(instance).__name__}"]
        return _validate_object(instance, schema, root, path)

    if expected == "array":
        if not isinstance(instance, list):
            return [f"{path}: expected array, got {type(instance).__name__}"]
        item_schema = schema.get("items")
        if item_schema is None:
            return []
        errors: list[str] = []
        for index, item in enumerate(instance):
            errors.extend(_validate(item, item_schema, root, f"{path}[{index}]"))
        return errors

    mistyped = _wrong_primitive_type(instance, expected)
    if mistyped:
        return [f"{path}: {mistyped}"]

    return _validate_scalar(instance, schema, path)


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
    assert set(item["source_verification"]) <= set(verification_schema["properties"])
    assert set(verification_schema["required"]) <= set(item["source_verification"])


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
    assert source["verification_status"] == "unverified"


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
