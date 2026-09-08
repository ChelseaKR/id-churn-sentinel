"""The per-jurisdiction watch receipt (issue #76).

The point of the file under test is that an empty `feed-us-tx.xml` is compatible with four
different weeks and says which one happened. So the tests here are mostly about the
*absences*: that a source a run never saw does not read as a source a run read and found
unchanged, and that a jurisdiction the latest run skipped says so instead of quietly
reporting an older run as if it were this week's.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.core.changes import ChangeRecord
from id_churn_sentinel.core.jurisdiction_status import (
    COVERAGE_COVERED,
    COVERAGE_NEVER,
    COVERAGE_NOT_IN_RUN,
    COVERAGE_STORE_UNAVAILABLE,
    OUTCOMES,
    build_jurisdiction_status,
    jurisdiction_status_json,
    no_run_jurisdiction_status,
    store_unavailable_jurisdiction_status,
)
from id_churn_sentinel.core.normalize import EXTRACTOR_VERSION, NORMALIZER_VERSION
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import Registry, Source
from id_churn_sentinel.core.store import (
    RUN_COMPLETE,
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_QUIET,
    AttemptEvidence,
    RunSourceInput,
    SnapshotStore,
)

from .conftest import eligible_source

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 7, 13)

#: The schema this document is published against, pinned as a LITERAL. Reading it back from
#: `JURISDICTION_STATUS_SCHEMA_VERSION` would make the assertion move with the constant and
#: assert nothing about the contract a consumer pinned to.
SCHEMA_VERSION = "1.0"

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "schema" / "jurisdiction-status-v1.schema.json"
)


def _inputs(
    sources: tuple[Source, ...], *, eligible: frozenset[str] | None = None
) -> tuple[RunSourceInput, ...]:
    return tuple(
        RunSourceInput(
            source_id=source.id,
            jurisdiction=source.jurisdiction,
            document_class=source.document_class,
            url=source.url,
            authority=source.authority,
            eligible=eligible is None or source.id in eligible,
            eligibility_reasons=()
            if eligible is None or source.id in eligible
            else ("unverified",),
        )
        for source in sources
    )


def _ok_evidence(url: str) -> AttemptEvidence:
    return AttemptEvidence(
        final_url=url,
        redirect_chain=(),
        raw_sha256="a" * 64,
        normalized_sha256="b" * 64,
        bytes_received=8,
        byte_limit=8 * 1024 * 1024,
        truncated=False,
        extraction_outcome="text-normalized",
        error_class="",
    )


def _failed_evidence(url: str) -> AttemptEvidence:
    return AttemptEvidence(
        final_url=url,
        redirect_chain=(),
        raw_sha256="",
        normalized_sha256="",
        bytes_received=0,
        byte_limit=None,
        truncated=False,
        extraction_outcome="",
        error_class="http-error",
    )


def _attempt(
    store: SnapshotStore,
    run_id: str,
    source: Source,
    *,
    ok: bool,
    measured: bool = True,
    at: datetime = NOW,
) -> None:
    store.begin_fetch_attempt(run_id, source_id=source.id, url=source.url)
    store.finish_fetch_attempt(
        run_id,
        source_id=source.id,
        ok=ok,
        http_status=200 if ok else 503,
        content_type="text/html",
        normalizer_version=NORMALIZER_VERSION if ok else "",
        extractor_version=EXTRACTOR_VERSION if ok else "",
        error="" if ok else "synthetic outage",
        evidence=_ok_evidence(source.url) if ok else _failed_evidence(source.url),
        measured=measured,
        completed_at=at,
    )


def _start(
    store: SnapshotStore,
    registry: Registry,
    *,
    jurisdiction: str | None,
    started_at: datetime = NOW,
    eligible: frozenset[str] | None = None,
    sources: tuple[Source, ...] | None = None,
) -> str:
    return store.start_watch_run(
        as_of=AS_OF,
        registry_version="1.0",
        registry_revision="a" * 64,
        jurisdiction=jurisdiction,
        sources=_inputs(sources if sources is not None else registry.sources, eligible=eligible),
        started_at=started_at,
    )


@pytest.fixture
def texas(source: Source) -> Source:
    return eligible_source(source)


@pytest.fixture
def arizona(arizona_source: Source) -> Source:
    return eligible_source(arizona_source)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SnapshotStore]:
    with SnapshotStore(tmp_path / "sentinel.db") as opened:
        yield opened


def _payload(status_json_text: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(status_json_text)
    return loaded


def _outcomes(payload: dict[str, Any]) -> dict[str, str]:
    return {entry["source_id"]: entry["outcome"] for entry in payload["sources"]}


# ---- the three "done when" criteria from the issue ------------------------------------


def test_a_run_covering_two_jurisdictions_leaves_the_third_saying_not_in_run(
    store: SnapshotStore, registry: Registry, texas: Source, arizona: Source
) -> None:
    """The issue's first criterion, and the one the whole file exists for.

    An aggregate run covers everything. A later TX-scoped run does not cover AZ — so AZ's
    receipt must report the aggregate run it WAS covered in, and say that the newest run
    skipped it, rather than showing the older run's outcomes as though they were this
    week's.
    """
    aggregate = _start(store, registry, jurisdiction=None)
    for source in registry.sources:
        _attempt(store, aggregate, source, ok=True)
    store.finish_watch_run(aggregate, state=RUN_QUIET, observation_count=0, completed_at=NOW)

    later = NOW + timedelta(days=7)
    scoped = _start(store, registry, jurisdiction="TX", started_at=later, sources=(texas,))
    _attempt(store, scoped, texas, ok=True, at=later)
    store.finish_watch_run(scoped, state=RUN_QUIET, observation_count=0, completed_at=later)

    tx = build_jurisdiction_status(store, "TX", registry=registry)
    az = build_jurisdiction_status(store, "AZ", registry=registry)

    assert tx.coverage == COVERAGE_COVERED
    assert tx.run is not None and tx.run.run_id == scoped
    assert _outcomes(_payload(jurisdiction_status_json(tx, generated_at=NOW))) == {
        texas.id: "observed_unchanged"
    }

    assert az.coverage == COVERAGE_NOT_IN_RUN
    assert az.run is not None and az.run.run_id == aggregate
    assert az.latest_run is not None and az.latest_run.run_id == scoped

    document = _payload(jurisdiction_status_json(az, generated_at=NOW))
    assert document["run"]["run_id"] == aggregate
    assert document["latest_run"]["run_id"] == scoped
    assert "not in the most recent run" in document["statement"]


def test_a_failed_fetch_reads_unreachable_and_never_observed_unchanged(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """The issue's second criterion — #51's class, at jurisdiction level.

    A source with nothing to compare against and a fetch that failed produced identical
    drift counts to one that was read and matched. Here it must say which happened.
    """
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    _attempt(store, run_id, texas, ok=False)
    store.finish_watch_run(run_id, state=RUN_PARTIAL, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    assert _outcomes(document)[texas.id] == "unreachable"
    assert document["counts"]["observed_unchanged"] == 0
    row = next(entry for entry in document["sources"] if entry["source_id"] == texas.id)
    assert "not evidence that it did not change" in row["outcome_statement"]


def test_publishing_with_no_run_at_all_says_not_attempted_and_a_null_run(
    tmp_path: Path, registry: Registry
) -> None:
    """The issue's third criterion. Files exist; every source reads `not_attempted`.

    The coverage word is `store_unavailable`, not `never_covered`: `publish()` was handed no
    receipts, so it has not read a store and cannot say what one holds. See
    `test_no_store_and_no_run_are_different_words`.
    """
    result = publish([], tmp_path, registry=registry, now=NOW)

    assert len(result.jurisdiction_status_paths) == len(registry.jurisdictions)
    for path in result.jurisdiction_status_paths:
        document = _payload(path.read_text(encoding="utf-8"))
        assert document["run"] is None
        assert document["coverage"] == COVERAGE_STORE_UNAVAILABLE
        assert set(_outcomes(document).values()) == {"not_attempted"}
        assert "not evidence of no change" in document["statement"]


def test_the_statement_counts_readings_and_never_claims_a_comparison(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """Issue #99, the half that needs no vocabulary decision.

    The numerator has always been a count of `observed_unchanged` + `observed_changed`, and
    the sentence built from it used to read *"compared N of M ... against the committed
    baseline"*. `observed_unchanged` is also what a **first sighting** persists as, and what
    a source whose registry entry has been re-pointed persists as, and what an
    unrenormalizable committed hash persists as -- `detect.py` refuses the comparison in the
    last two cases *in terms*. So on the ordinary first run, over an empty store, the
    sentence claimed 156 comparisons against baselines that did not exist.

    Both halves are asserted. The presence assertion is not decoration: "the word `compared`
    is absent" is satisfied by a fixture that produces no sentence at all, and the fixture
    here is one attempt in one jurisdiction, which is exactly where a mistake would hide.
    """
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    _attempt(store, run_id, texas, ok=True)
    store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    statement = document["statement"]

    # No snapshot was ever recorded for this source, so nothing was held against anything.
    assert _outcomes(document)[texas.id] == "observed_unchanged"
    assert "read 1 of 1 registered source(s)" in statement, statement
    assert run_id in statement
    assert "compared" not in statement, statement
    assert "against the committed baseline" not in statement, statement


def test_every_reading_outcome_word_is_one_the_receipt_publishes(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """The statement's numerator is drawn from the published vocabulary, not beside it.

    `_READING_OUTCOMES` decides the number every receipt prints. A word in it that `OUTCOMES`
    does not declare would be counted and never emitted, so the numerator would describe a
    set no reader can see -- and renaming an outcome would silently drop it from the count
    with the sentence still reading as a total.
    """
    from id_churn_sentinel.core.jurisdiction_status import _READING_OUTCOMES

    assert _READING_OUTCOMES, "the reading vocabulary is empty; the numerator is always 0"
    assert _READING_OUTCOMES <= set(OUTCOMES), sorted(_READING_OUTCOMES - set(OUTCOMES))


def test_no_store_and_no_run_are_different_words(
    tmp_path: Path, store: SnapshotStore, registry: Registry
) -> None:
    """ "I did not look" and "I looked and found nothing" are not the same claim.

    Both produce a receipt with a null run and every source `not_attempted`, which is why it
    would be so easy to give them one word — and giving them one word would put a statement
    about the store's contents into a document written by something that never opened it.
    That is this repository's whole defect, committed inside the file written to remove it.
    """
    publish([], tmp_path, registry=registry, now=NOW)
    from_store = build_jurisdiction_status(store, "TX", registry=registry)

    without_store = _payload((tmp_path / "status-us-tx.json").read_text(encoding="utf-8"))
    with_store = _payload(jurisdiction_status_json(from_store, generated_at=NOW))

    assert without_store["coverage"] == COVERAGE_STORE_UNAVAILABLE
    assert with_store["coverage"] == COVERAGE_NEVER
    assert without_store["run"] is None and with_store["run"] is None
    assert set(_outcomes(without_store).values()) == set(_outcomes(with_store).values())
    assert "without access to the evidence store" in without_store["statement"]
    assert "No watch run has ever covered" in with_store["statement"]


# ---- the absences, one at a time -------------------------------------------------------


def test_a_source_the_run_never_saw_is_not_in_run_rather_than_not_eligible(
    store: SnapshotStore, registry: Registry, texas: Source, arizona: Source
) -> None:
    """A source added to the registry after a run is NOT a source the run judged ineligible.

    Both are absent from `WatchRun.eligible_source_ids`, which is why the receipt reads the
    run's rows instead of its id sets. Collapsing them would publish a decision nobody made.
    """
    run_id = _start(store, registry, jurisdiction=None, sources=(arizona,))
    _attempt(store, run_id, arizona, ok=True)
    store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    row = next(entry for entry in document["sources"] if entry["source_id"] == texas.id)
    assert row["outcome"] == "not_in_run"
    assert row["eligible_in_run"] is None
    assert row["eligibility_reasons"] == []


def test_an_ineligible_source_carries_the_runs_own_reasons(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """The run's judgement and the run's reasons, not today's re-evaluation of the source."""
    # `failed`, not `quiet`: the store refuses a terminal success state for a run with no
    # eligible source, which is the shape this repository's real weekly run has been in for
    # four weeks (0 of 156 attempt-eligible, #56).
    run_id = _start(store, registry, jurisdiction=None, eligible=frozenset(), sources=(texas,))
    store.finish_watch_run(run_id, state=RUN_FAILED, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    row = next(entry for entry in document["sources"] if entry["source_id"] == texas.id)
    assert row["outcome"] == "not_eligible"
    assert row["eligible_in_run"] is False
    assert row["eligibility_reasons"] == ["unverified"]


def test_a_page_with_no_extractable_text_reads_unreadable(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """A retrieval that succeeded and yielded nothing comparable is not an observation."""
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    _attempt(store, run_id, texas, ok=True, measured=False)
    store.finish_watch_run(run_id, state=RUN_PARTIAL, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    assert _outcomes(document)[texas.id] == "unreadable"


def test_a_source_that_moved_reads_observed_changed(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """`observed_changed` comes from the run's own observation binding, not from a date range."""
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    _attempt(store, run_id, texas, ok=True)
    store.record_change(
        ChangeRecord.observed(
            source_id=texas.id,
            jurisdiction=texas.jurisdiction,
            document_class=texas.document_class,
            url=texas.url,
            previous_hash="c" * 64,
            new_hash="d" * 64,
            diff_excerpt="synthetic",
            observed_at=NOW,
        ),
        run_id=run_id,
    )
    store.finish_watch_run(run_id, state=RUN_COMPLETE, observation_count=1, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    assert _outcomes(document)[texas.id] == "observed_changed"
    assert document["counts"]["observed_changed"] == 1
    assert document["counts"]["observed_unchanged"] == 0


def test_an_attempt_with_no_recorded_outcome_is_in_flight_not_unreachable(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """A run that has not answered for a source has not answered that it was unreachable."""
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    store.begin_fetch_attempt(run_id, source_id=texas.id, url=texas.url)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    assert _outcomes(document)[texas.id] == "in_flight"


def test_a_jurisdiction_no_run_has_ever_covered_still_names_the_run_that_happened(
    store: SnapshotStore, registry: Registry, arizona: Source
) -> None:
    """`never_covered` is not "nothing happened"; it is "nothing happened *here*"."""
    run_id = _start(store, registry, jurisdiction="AZ", sources=(arizona,))
    _attempt(store, run_id, arizona, ok=True)
    store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)

    status = build_jurisdiction_status(store, "TX", registry=registry)
    assert status.coverage == COVERAGE_NEVER
    assert status.run is None
    assert status.latest_run is not None and status.latest_run.run_id == run_id

    document = _payload(jurisdiction_status_json(status, generated_at=NOW))
    assert document["run"] is None
    assert document["latest_run"]["run_id"] == run_id
    assert set(_outcomes(document).values()) == {"not_attempted"}


# ---- the document itself ---------------------------------------------------------------


def test_every_outcome_word_carries_a_published_sentence() -> None:
    """A word with no sentence cannot be emitted, and the schema's enum cannot drift from it.

    Two lists could otherwise disagree in silence: the vocabulary the code can produce and
    the enum a consumer validates against.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    declared = schema["$defs"]["source"]["properties"]["outcome"]["enum"]
    assert set(declared) == set(OUTCOMES)
    assert all(sentence.strip() for sentence in OUTCOMES.values())


def test_the_counts_block_names_every_outcome_including_the_zeroes(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """Omitting a zero makes a reader reconstruct the key, and they reconstruct it kindly."""
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    _attempt(store, run_id, texas, ok=True)
    store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    assert set(document["counts"]) == set(OUTCOMES)
    assert document["counts"]["observed_unchanged"] == 1
    assert sum(document["counts"].values()) == len(document["sources"])


def test_the_document_pins_its_schema_version_and_carries_the_vocabulary(
    registry: Registry,
) -> None:
    document = _payload(
        jurisdiction_status_json(no_run_jurisdiction_status("TX", registry), generated_at=NOW)
    )
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["outcome_vocabulary"] == OUTCOMES


def test_no_published_hash_is_attributed_to_a_run() -> None:
    """1.0 publishes no observed hash, deliberately — the store cannot bind one to a run.

    `snapshots` has no `run_id`, so the only hash a run owns is the `new_hash` on a change,
    which exists for sources that moved and for no others. A field that is correct only for
    the minority of rows, and silently the latest fetch for the rest, is the substitution
    this file exists to refuse. Asserted against the schema so adding one has to come with
    a decision about where the hash comes from.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = schema["$defs"]["source"]["properties"]
    assert not [name for name in properties if "hash" in name or "sha" in name]


def test_every_source_row_carries_its_verification_status(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """The labelling discipline, restated here because this is the document most likely to
    be read as an endorsement of the URL."""
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    _attempt(store, run_id, texas, ok=True)
    store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    for entry in document["sources"]:
        assert entry["verification_status"]
        assert "human_verified" in entry
        assert entry["verification_statement"]


def test_publish_writes_one_receipt_per_registry_jurisdiction(
    tmp_path: Path, registry: Registry
) -> None:
    result = publish([], tmp_path, registry=registry, now=NOW)
    names = {path.name for path in result.jurisdiction_status_paths}
    assert names == {"status-us-tx.json", "status-us-az.json", "status-us.json"}
    for path in result.jurisdiction_status_paths:
        assert path.exists()


def test_an_eligible_source_the_run_did_not_reach_reads_not_attempted(
    store: SnapshotStore, registry: Registry, texas: Source, arizona: Source
) -> None:
    """A run that stopped part-way has not judged the sources it never got to.

    `failed`, because the store refuses a success state for a run whose attempted set is
    smaller than its eligible set — which is exactly the shape being tested.
    """
    run_id = _start(store, registry, jurisdiction=None, sources=(texas, arizona))
    _attempt(store, run_id, arizona, ok=True)
    store.finish_watch_run(run_id, state=RUN_FAILED, observation_count=0, completed_at=NOW)

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    row = next(entry for entry in document["sources"] if entry["source_id"] == texas.id)
    assert row["outcome"] == "not_attempted"
    assert row["eligible_in_run"] is True


def test_a_row_with_no_recorded_observation_outcome_is_unknown_not_unchanged(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """`legacy-unknown` is what the migration wrote over rows predating observation outcomes.

    Written straight into `run_sources` because the API cannot produce it any more, which is
    the point: these rows exist in the operator's store and will be read by this code. A
    retrieval with no recorded observation is not a page that had not changed.
    """
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,))
    _attempt(store, run_id, texas, ok=True)
    store.finish_watch_run(run_id, state=RUN_QUIET, observation_count=0, completed_at=NOW)
    store._conn.execute(
        "UPDATE run_sources SET observation_outcome = 'legacy-unknown' WHERE run_id = ?",
        (run_id,),
    )
    store._conn.commit()

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    assert _outcomes(document)[texas.id] == "outcome_unknown"
    assert document["counts"]["observed_unchanged"] == 0


def test_a_naive_generated_at_is_stamped_as_utc_rather_than_published_unqualified(
    registry: Registry,
) -> None:
    """An offsetless timestamp in a published document is a time in nobody's zone."""
    document = _payload(
        jurisdiction_status_json(
            store_unavailable_jurisdiction_status("TX", registry),
            generated_at=datetime(2026, 7, 13, 12, 0),
        )
    )
    assert document["generated_at"] == "2026-07-13T12:00:00+00:00"


def test_a_malformed_eligibility_reason_does_not_withhold_the_eligibility_answer(
    store: SnapshotStore, registry: Registry, texas: Source
) -> None:
    """The reasons are a footnote to the decision; the decision is `eligible`.

    A receipt that refused to render because one row's explanation would not parse would
    withhold the answer over the footnote to it — so the reasons degrade to empty and the
    judgement still publishes.
    """
    run_id = _start(store, registry, jurisdiction=None, sources=(texas,), eligible=frozenset())
    store.finish_watch_run(run_id, state=RUN_FAILED, observation_count=0, completed_at=NOW)
    store._conn.execute(
        "UPDATE run_sources SET eligibility_reasons = 'not json' WHERE run_id = ?", (run_id,)
    )
    store._conn.commit()

    document = _payload(
        jurisdiction_status_json(
            build_jurisdiction_status(store, "TX", registry=registry), generated_at=NOW
        )
    )
    row = next(entry for entry in document["sources"] if entry["source_id"] == texas.id)
    assert row["outcome"] == "not_eligible"
    assert row["eligible_in_run"] is False
    assert row["eligibility_reasons"] == []
