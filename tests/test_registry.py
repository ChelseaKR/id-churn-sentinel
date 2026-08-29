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
    dump_registry_text,
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


def test_no_committed_entry_claims_a_verification_nobody_signed() -> None:
    """The seed is SEEDED, and this is the invariant that survives the burn-down.

    Not "nothing is verified" — the whole point of `sentinel verify` is that entries *will*
    become verified, one named human at a time, and a test that went red on real progress
    would be a test that discourages the most valuable work in the repo. What may never
    happen is an entry claiming a human confirmed it with **no human named and no date**.
    `load_registry` refuses to load one at all; this asserts it on the committed file.

    (The standing count — `0 of 156 sources are human-verified` — is checked in
    `test_verify.py` and gated in every doc by `sentinel coverage --check-docs`.)
    """
    registry = load_registry(default_registry_path())

    for source in registry.sources:
        if source.verified:
            assert source.verification.verifier.strip(), (
                f"{source.id} claims verified: true with nobody named. A verification with no "
                f"human attached is indistinguishable from a machine's — which is the one thing "
                f"this flag exists to not be."
            )
            assert source.verification.at, f"{source.id} is verified on no date"


def test_committed_registry_is_written_in_the_canonical_style() -> None:
    """The committed file must be byte-identical to what `dump_registry_text` produces.

    This is a formatting test that earns its place, and it is here because the regression
    it catches has already happened twice. `dump_registry_text` re-collapses each
    `checked`/`verification`/`fetch_policy` block onto one line; a plain
    `json.dumps(indent=2)` does not, and explodes the file from ~1750 lines to ~2400.
    Both times, a two-field edit arrived as a ~960-line diff.

    That is not a cosmetic complaint. `sentinel verify` REWRITES this file once per source,
    up to 156 times, and the whole value of that audit trail is that each rewrite is a
    one-line diff naming a human. A writer that reflows the file buries the verifier's name
    under 800 lines of whitespace, and a diff nobody can read is a diff nobody reviews —
    which is exactly the review this repo refuses to do without.
    """
    path = default_registry_path()
    committed = path.read_text(encoding="utf-8")
    expected = dump_registry_text(json.loads(committed))

    assert committed == expected, (
        "sources/registry.json is not in the committed canonical style. It was almost "
        "certainly written with `json.dumps(indent=2)` instead of `dump_registry_text`. "
        "Rewrite it through `dump_registry_text` — do not reformat by hand."
    )


def test_no_committed_entry_renders_a_failed_fetch_as_a_reachable_one() -> None:
    """A source we could not fetch may never carry `reachable: true`.

    This is the repo's own issue #10 expressed as an invariant over the committed data.
    `Source.reachable` defaults to True when a `checked` block is absent — deliberately,
    because an unchecked entry is not evidence of unreachability. But once a block IS
    present, it records what a socket actually saw, and a block that pairs an error status
    with `reachable: true` is the failure mode this whole project is organised against:
    absence rendered as a value, a blocked fetch published as a healthy one.

    A missing `status` (None) is a fetch that never got an HTTP response at all — a TLS
    failure, a timeout, a refused connection. That is the *least* reachable a source can
    be, and it must never be recorded as reachable either.
    """
    registry = load_registry(default_registry_path())

    for source in registry.sources:
        if not source.checked:
            continue
        status = source.checked.get("status")
        claims_reachable = bool(source.checked.get("reachable", True))

        if status is None:
            assert not claims_reachable, (
                f"{source.id} records no HTTP status at all — nothing answered — yet claims "
                f"reachable: true. A fetch that never got a response is not a reachable source."
            )
        elif int(status) >= 400:
            assert not claims_reachable, (
                f"{source.id} records HTTP {status} yet claims reachable: true. A blocked or "
                f"missing page counted as reachable is counted as unchanged, which is a wrong "
                f"'no change' — the exact harm issue #10 was filed about."
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
