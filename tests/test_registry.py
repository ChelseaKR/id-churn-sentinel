"""Tests for :mod:`id_churn_sentinel.core.registry`, including the committed seed registry.

`test_committed_registry_loads` is the merge gate `make sources-validate` runs. It is the
reason a typo'd jurisdiction or a duplicated watch target cannot land.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.core.registry import (
    DOCUMENT_CLASSES,
    JURISDICTIONS,
    default_registry_path,
    load_registry,
)
from id_churn_sentinel.errors import RegistryError

VALID_ENTRY: dict[str, Any] = {
    "id": "tx-dps-change-dl-id",
    "jurisdiction": "TX",
    "document_class": "drivers_license",
    "url": "https://www.dps.texas.gov/section/driver-license",
    "authority": "Texas Department of Public Safety",
    "verified": False,
    "notes": "seed",
}


def write_registry(path: Path, *entries: dict[str, Any], version: str = "1.0") -> Path:
    target = path / "registry.json"
    target.write_text(
        json.dumps({"registry_version": version, "sources": list(entries)}), encoding="utf-8"
    )
    return target


# -- the committed seed ----------------------------------------------------------


def test_committed_registry_loads() -> None:
    """The gate. Every seeded entry is a well-formed https official URL with a known
    jurisdiction and document class, a named authority, a unique id, and no duplicate
    watch target."""
    registry = load_registry(default_registry_path())
    assert len(registry) > 0
    for source in registry.sources:
        assert source.url.startswith("https://")
        assert source.jurisdiction in JURISDICTIONS
        assert source.document_class in DOCUMENT_CLASSES
        assert source.authority.strip()


def test_committed_registry_ships_entirely_unverified() -> None:
    """The seed is SEEDED. Nothing in this codebase flips `verified`, and no entry may
    ship claiming a human confirmed it when none has. If someone verifies entries for
    real, this test is what they must consciously update — which is the point."""
    registry = load_registry(default_registry_path())
    assert len(registry.unverified) == len(registry), (
        "an entry claims verified: true — a human must have actually opened the URL and "
        "confirmed it, and this assertion must be narrowed deliberately, not by accident"
    )


def test_committed_registry_covers_the_federal_bucket() -> None:
    registry = load_registry(default_registry_path())
    federal = registry.for_jurisdiction("US")
    assert {s.document_class for s in federal} >= {
        "passport",
        "social_security",
        "selective_service",
    }


# -- vocabulary ------------------------------------------------------------------


def test_jurisdictions_is_50_states_plus_dc_plus_federal() -> None:
    assert len(JURISDICTIONS) == 52
    assert "DC" in JURISDICTIONS
    assert "US" in JURISDICTIONS
    assert "PR" not in JURISDICTIONS  # territories are out of scope until a human adds them


def test_document_classes_are_the_six_documents_a_transition_touches() -> None:
    assert {
        "birth_certificate",
        "drivers_license",
        "court_order_name_change",
        "passport",
        "social_security",
        "selective_service",
    } == DOCUMENT_CLASSES


# -- validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"url": "http://example.gov/x"}, "must be https"),
        ({"url": "https://ex.gov/x#anchor"}, "#fragment"),
        ({"url": "https://user:pw@ex.gov/x"}, "credentials"),
        ({"url": "not-a-url"}, "must be https"),
        ({"jurisdiction": "XX"}, "not a known jurisdiction"),
        ({"jurisdiction": "tx"}, "not a known jurisdiction"),  # case matters; no silent coercion
        ({"document_class": "library_card"}, "not one of"),
        ({"id": "TX_DPS"}, "lowercase-hyphen slug"),
        ({"authority": "   "}, "issuing authority"),
        ({"verified": "no"}, "must be a boolean"),
        ({"notes": 7}, "notes must be a string"),
    ],
)
def test_invalid_entry_is_rejected(tmp_path: Path, mutation: dict[str, Any], expected: str) -> None:
    """There is no 'skip the bad entry and carry on' path. A skipped entry is an unwatched
    source, and an unwatched source is the silent failure this whole tool exists to prevent."""
    path = write_registry(tmp_path, {**VALID_ENTRY, **mutation})
    with pytest.raises(RegistryError, match=expected):
        load_registry(path)


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    entry = {k: v for k, v in VALID_ENTRY.items() if k != "authority"}
    path = write_registry(tmp_path, entry)
    with pytest.raises(RegistryError, match="missing required field"):
        load_registry(path)


def test_verified_defaults_to_false_when_absent(tmp_path: Path) -> None:
    """Absence must never read as 'verified'. The default has to fail safe."""
    entry = {k: v for k, v in VALID_ENTRY.items() if k != "verified"}
    registry = load_registry(write_registry(tmp_path, entry))
    assert registry.sources[0].verified is False


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    """Ids key the snapshot store: a collision would overwrite one source's history."""
    other = {**VALID_ENTRY, "url": "https://www.dps.texas.gov/other"}
    path = write_registry(tmp_path, VALID_ENTRY, other)
    with pytest.raises(RegistryError, match="duplicate source id"):
        load_registry(path)


def test_duplicate_watch_target_is_rejected(tmp_path: Path) -> None:
    """The same page under two ids doubles every change record a reviewer sees, which is
    how a reviewer learns to ignore the feed."""
    twin = {**VALID_ENTRY, "id": "tx-dps-again"}
    path = write_registry(tmp_path, VALID_ENTRY, twin)
    with pytest.raises(RegistryError, match="duplicate watch target"):
        load_registry(path)


def test_unknown_version_is_rejected(tmp_path: Path) -> None:
    path = write_registry(tmp_path, VALID_ENTRY, version="0.9")
    with pytest.raises(RegistryError, match="not the supported"):
        load_registry(path)


def test_empty_registry_is_rejected(tmp_path: Path) -> None:
    path = write_registry(tmp_path)
    with pytest.raises(RegistryError, match="non-empty list"):
        load_registry(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "nope.json")


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RegistryError, match="not valid JSON"):
        load_registry(path)


def test_non_object_registry_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RegistryError, match="must be a JSON object"):
        load_registry(path)


def test_non_object_entry_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text('{"registry_version": "1.0", "sources": ["oops"]}', encoding="utf-8")
    with pytest.raises(RegistryError, match=r"sources\[0\] must be an object"):
        load_registry(path)


# -- queries ---------------------------------------------------------------------


def test_for_unknown_jurisdiction_raises_rather_than_returning_nothing() -> None:
    """`--jurisdiction XX` silently watching nothing is precisely the failure this tool
    exists to prevent, so a typo must be loud."""
    registry = load_registry(default_registry_path())
    with pytest.raises(RegistryError, match="unknown jurisdiction"):
        registry.for_jurisdiction("XX")


def test_for_jurisdiction_is_case_insensitive_at_the_query_boundary() -> None:
    registry = load_registry(default_registry_path())
    assert registry.for_jurisdiction("tx") == registry.for_jurisdiction("TX")


def test_by_id_round_trips_and_raises_on_unknown() -> None:
    registry = load_registry(default_registry_path())
    assert registry.by_id("us-passport-sex-markers").jurisdiction == "US"
    with pytest.raises(RegistryError, match="unknown source id"):
        registry.by_id("nope")


def test_source_host_is_exposed_for_politeness() -> None:
    registry = load_registry(default_registry_path())
    assert registry.by_id("us-passport-sex-markers").host == "travel.state.gov"


def test_registry_is_iterable_and_sized() -> None:
    registry = load_registry(default_registry_path())
    assert len(list(registry)) == len(registry)
