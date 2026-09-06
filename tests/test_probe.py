"""The HEAD-only availability channel: what it measures, and what it must never claim.

Two properties carry the file. **A probe is not a watch** — a probe run may not write a
snapshot, a change or a source-health row, and a source that probed reachable is not a source
that was read. **Censoring is reported, never rounded away** — an episode whose start or end
this channel never saw has no measured length, and its length appears in no distribution.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.probe import (
    PROBE_OUTCOMES,
    ProbeError,
    ProbeResult,
    availability_block,
    outage_episodes,
    probe_report,
    render_report,
    run_probe,
)
from id_churn_sentinel.core.registry import (
    FETCH_POLICY_ALLOW,
    VERIFIED,
    Registry,
    load_registry,
)
from id_churn_sentinel.core.status import STATUS_SCHEMA_VERSION, build_public_status, status_json
from id_churn_sentinel.core.store import SnapshotStore
from tests.test_schema import _validate

ROOT = Path(__file__).resolve().parents[1]
STATUS_SCHEMA_PATH = ROOT / "docs" / "schema" / "status-v1.schema.json"

ELIGIBLE_URL = "https://dps.texas.gov/section/driver-license/change-name"
SECOND_URL = "https://dmv.ca.gov/portal/driver-licenses-identification-cards/"


class StubProber:
    """Answers from a script, and records what it was asked. Never opens a socket."""

    def __init__(self, answers: dict[str, ProbeResult]) -> None:
        self._answers = answers
        self.asked: list[str] = []

    def probe(self, url: str) -> ProbeResult:
        self.asked.append(url)
        return self._answers.get(url, ProbeResult(url=url, outcome="unreachable", error="stub"))


def _entry(source_id: str, url: str, *, eligible: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": source_id,
        "jurisdiction": "TX" if "texas" in url else "CA",
        "document_class": "drivers_license",
        "url": url,
        "authority": "Fixture authority",
        "verified": eligible,
        "notes": "fixture",
    }
    if eligible:
        entry["verification"] = {
            "status": VERIFIED,
            "verifier": "A Named Human",
            "at": "2026-01-01",
            "evidence": "tests/evidence/fixture.json",
            "expires_at": "2099-12-31",
        }
        entry["fetch_policy"] = {
            "outcome": FETCH_POLICY_ALLOW,
            "reviewer": "A Policy Reader",
            "at": "2026-01-01",
            "expires_at": "2099-12-31",
            "evidence": "tests/evidence/robots.json",
            "reason": "fixture authorizes the injected prober",
        }
    return entry


def _registry(tmp_path: Path, entries: list[dict[str, Any]]) -> Registry:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"registry_version": "1.0", "sources": entries, "gaps": []}),
        encoding="utf-8",
    )
    return load_registry(path)


def _rows(source_id: str, outcomes: list[str], *, start_day: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "run_id": f"r{index}",
            "source_id": source_id,
            "url": ELIGIBLE_URL,
            "probed_at": f"2026-09-{start_day + index:02d}T06:11:00+00:00",
            "as_of": f"2026-09-{start_day + index:02d}",
            "outcome": outcome,
            "http_status": 200 if outcome == "reachable" else None,
            "latency_ms": 100,
            "tls_ok": 1,
            "redirect_target": "",
            "detail": "",
        }
        for index, outcome in enumerate(outcomes)
    ]


# ---- the issue's "done when" criteria ---------------------------------------------------


def test_three_failed_probes_then_recovery_is_one_episode_of_length_three() -> None:
    rows = _rows("tx", ["reachable", "unreachable", "unreachable", "unreachable", "reachable"])

    (episode,) = outage_episodes(rows)

    assert episode.length == 3
    assert episode.censored is False
    assert episode.censoring == ""
    assert episode.first_down_at.startswith("2026-09-02")
    assert episode.recovered_at.startswith("2026-09-05")


def test_a_source_that_never_recovers_yields_one_censored_episode() -> None:
    rows = _rows("tx", ["reachable", "unreachable", "unreachable"])

    (episode,) = outage_episodes(rows)

    assert episode.censored is True
    assert episode.censoring == "right"
    report = probe_report(rows)
    assert report["episodes_total"] == 1
    assert report["episodes_measured"] == 0
    assert report["episodes_censored"] == 1
    assert report["episode_length_probes"]["n"] == 0
    assert report["episode_length_probes"]["max"] is None, (
        "a censored episode's length was published as a measurement"
    )
    assert "not a finding that outages are short" in report["episode_length_probes"]["reason"]


def test_a_probe_run_writes_to_probes_and_to_nothing_else(tmp_path: Path) -> None:
    """The separation the whole channel rests on, asserted on the database rather than trusted."""
    _registry(tmp_path, [_entry("tx", ELIGIBLE_URL, eligible=True)])
    prober = StubProber(
        {ELIGIBLE_URL: ProbeResult(url=ELIGIBLE_URL, outcome="reachable", status=200)}
    )
    db = tmp_path / "probe.db"

    assert (
        main(
            ["--registry", str(tmp_path / "registry.json"), "probe", "--db", str(db)], prober=prober
        )
        == 0
    )

    connection = sqlite3.connect(db)
    try:
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in ("probes", "snapshots", "changes", "source_health", "watch_runs")
        }
    finally:
        connection.close()

    assert counts["probes"] == 1
    assert counts["snapshots"] == 0
    assert counts["changes"] == 0
    assert counts["source_health"] == 0
    assert counts["watch_runs"] == 0, (
        "a probe created a watch run; the availability channel has entered the watch record"
    )


def test_status_json_validates_with_the_availability_block_and_an_unchanged_state(
    tmp_path: Path,
) -> None:
    schema = json.loads(STATUS_SCHEMA_PATH.read_text(encoding="utf-8"))
    db = tmp_path / "s.db"
    with SnapshotStore(db) as store:
        store.record_probe_run(
            "r1",
            as_of="2026-09-05",
            probed_at="2026-09-05T06:11:00+00:00",
            results=[("tx", ELIGIBLE_URL, "unreachable", None, None, None, "", "timeout")],
        )
        store.record_probe_run(
            "r2",
            as_of="2026-09-06",
            probed_at="2026-09-06T06:11:00+00:00",
            results=[("tx", ELIGIBLE_URL, "reachable", 200, 90, True, "", "")],
        )
        status = build_public_status(store, now=datetime(2026, 9, 6, tzinfo=UTC))
        without = build_public_status(store, now=datetime(2026, 9, 6, tzinfo=UTC))

    payload = json.loads(status_json(status, generated_at=datetime(2026, 9, 6, tzinfo=UTC)))

    assert payload["schema_version"] == STATUS_SCHEMA_VERSION
    assert _validate(payload, schema, schema, "$") == []
    assert payload["availability"]["channel"] == "head-probe"
    assert payload["availability"]["episodes_censored"] == 1
    # `state` is watch health and must not move because a probe ran. A probe is not a watch.
    assert payload["state"] == without.state == "stale"


# ---- absence is reported as absence -------------------------------------------------------


def test_an_unrun_channel_omits_the_block_rather_than_publishing_zeroes() -> None:
    """A block full of zeroes would say "no outages" over the top of "no measurements"."""
    assert availability_block([]) is None


def test_status_json_omits_availability_when_nothing_has_been_probed(tmp_path: Path) -> None:
    schema = json.loads(STATUS_SCHEMA_PATH.read_text(encoding="utf-8"))
    with SnapshotStore(tmp_path / "s.db") as store:
        status = build_public_status(store, now=datetime(2026, 9, 6, tzinfo=UTC))
    payload = json.loads(status_json(status, generated_at=datetime(2026, 9, 6, tzinfo=UTC)))

    assert "availability" not in payload
    assert _validate(payload, schema, schema, "$") == []


def test_the_committed_status_json_still_validates_against_the_widened_schema() -> None:
    """1.2 is additive: the artifact Pages is serving today is still a valid document."""
    schema = json.loads(STATUS_SCHEMA_PATH.read_text(encoding="utf-8"))
    committed = json.loads((ROOT / "docs" / "status.json").read_text(encoding="utf-8"))
    assert _validate(committed, schema, schema, "$") == []


def test_a_head_unsupported_probe_is_not_an_outage() -> None:
    """A 405 is a fact about a server, not about availability.

    Folding it into the down set would publish a host's refusal to answer HEAD as downtime,
    and would make the outage history of every such source pure noise.
    """
    rows = _rows("tx", ["reachable", "head_unsupported", "reachable"])

    assert outage_episodes(rows) == ()
    report = probe_report(rows)
    (detail,) = report["sources_detail"]
    assert detail["probes"] == 3
    assert detail["measured"] == 2
    assert detail["unmeasured"] == 1
    assert detail["reachable"] == 2


def test_an_unmeasured_probe_mid_outage_leaves_the_episode_open() -> None:
    """Skipping is not the same as treating it as up: nothing observed a recovery."""
    rows = _rows("tx", ["reachable", "unreachable", "head_unsupported", "unreachable", "reachable"])

    (episode,) = outage_episodes(rows)

    assert episode.length == 2
    assert episode.censored is False


def test_a_source_down_at_its_first_probe_is_left_censored() -> None:
    """We did not see it start, so its length is a lower bound and not a measurement."""
    rows = _rows("tx", ["unreachable", "unreachable", "reachable"])

    (episode,) = outage_episodes(rows)

    assert episode.censoring == "left"
    assert episode.censored is True
    assert probe_report(rows)["episode_length_probes"]["n"] == 0


def test_a_source_down_for_its_whole_record_is_censored_at_both_ends() -> None:
    rows = _rows("tx", ["unreachable", "unreachable"])
    (episode,) = outage_episodes(rows)
    assert episode.censoring == "both"
    assert probe_report(rows)["censoring_by_end"] == {"left": 0, "right": 0, "both": 1}


def test_a_source_with_only_unmeasured_probes_produces_no_episodes_and_no_uptime_claim() -> None:
    rows = _rows("tx", ["head_unsupported", "robots_disallowed", "not_eligible"])

    assert outage_episodes(rows) == ()
    (detail,) = probe_report(rows)["sources_detail"]
    assert detail["measured"] == 0
    assert detail["reachable"] == 0
    assert detail["probes"] == 3


def test_every_duration_figure_is_printed_beside_its_censoring_count() -> None:
    rows = _rows("tx", ["reachable", "unreachable", "reachable", "unreachable"])
    rendered = render_report(probe_report(rows))

    assert "censored" in rendered
    assert "measured" in rendered
    assert "PROBES, not hours" in rendered


# ---- the run ------------------------------------------------------------------------------


def test_an_ineligible_source_is_recorded_rather_than_omitted(tmp_path: Path) -> None:
    """A source dropped from the record leaves a hole, and a hole reads as uptime."""
    registry = _registry(
        tmp_path,
        [
            _entry("tx", ELIGIBLE_URL, eligible=True),
            _entry("ca", SECOND_URL, eligible=False),
        ],
    )
    prober = StubProber(
        {ELIGIBLE_URL: ProbeResult(url=ELIGIBLE_URL, outcome="reachable", status=200)}
    )

    run = run_probe(registry, prober, as_of=date(2026, 9, 6), run_id="r1")

    assert prober.asked == [ELIGIBLE_URL], "an ineligible source was probed"
    outcomes = {source_id: result.outcome for source_id, result in run.results}
    assert outcomes == {"tx": "reachable", "ca": "not_eligible"}
    assert run.attempted == 1
    assert run.skipped == 1


def test_the_run_is_deterministic_in_source_order(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        [_entry("tx", ELIGIBLE_URL, eligible=True), _entry("ca", SECOND_URL, eligible=True)],
    )
    prober = StubProber(
        {
            ELIGIBLE_URL: ProbeResult(url=ELIGIBLE_URL, outcome="reachable", status=200),
            SECOND_URL: ProbeResult(url=SECOND_URL, outcome="reachable", status=200),
        }
    )
    run = run_probe(registry, prober, as_of=date(2026, 9, 6), run_id="r1")
    assert [source_id for source_id, _ in run.results] == ["ca", "tx"]


def test_an_unknown_outcome_cannot_be_constructed() -> None:
    with pytest.raises(ProbeError, match="unknown probe outcome"):
        ProbeResult(url=ELIGIBLE_URL, outcome="probably_fine")


def test_a_negative_latency_is_refused() -> None:
    with pytest.raises(ProbeError, match="latency_ms"):
        ProbeResult(url=ELIGIBLE_URL, outcome="reachable", status=200, latency_ms=-1)


def test_the_outcome_vocabulary_is_closed_and_matches_the_stores_check_constraint() -> None:
    """A value the code can emit that the store rejects is a run that dies at 06:11 on a Monday."""
    from id_churn_sentinel.core import store as store_module

    migration = next(sql for version, _, sql in store_module._MIGRATIONS if version == 10)
    declared = set(
        re.findall(r"'([a-z_]+)'", migration.split("CHECK (outcome IN")[1].split(")")[0])
    )
    assert declared == set(PROBE_OUTCOMES)


def test_the_store_refuses_an_outcome_outside_the_vocabulary(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "s.db") as store, pytest.raises(sqlite3.IntegrityError):
        store.record_probe_run(
            "r1",
            as_of="2026-09-06",
            probed_at="2026-09-06T06:11:00+00:00",
            results=[("tx", ELIGIBLE_URL, "probably_fine", 200, 10, True, "", "")],
        )


def test_the_store_refuses_a_latency_on_a_probe_that_was_never_sent(tmp_path: Path) -> None:
    """A duration attached to a request nobody made is a fabricated measurement."""
    with SnapshotStore(tmp_path / "s.db") as store, pytest.raises(sqlite3.IntegrityError):
        store.record_probe_run(
            "r1",
            as_of="2026-09-06",
            probed_at="2026-09-06T06:11:00+00:00",
            results=[("tx", ELIGIBLE_URL, "not_eligible", None, 42, None, "", "")],
        )


def test_the_store_refuses_a_reachable_probe_with_no_status(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "s.db") as store, pytest.raises(sqlite3.IntegrityError):
        store.record_probe_run(
            "r1",
            as_of="2026-09-06",
            probed_at="2026-09-06T06:11:00+00:00",
            results=[("tx", ELIGIBLE_URL, "reachable", None, 10, True, "", "")],
        )


# ---- the command ---------------------------------------------------------------------------


def test_the_command_exits_zero_when_every_source_is_down(tmp_path: Path) -> None:
    """An outage is the fact this channel records, not a broken tool.

    A daily job that goes red because a state website is down teaches its operator to ignore
    it, and then they ignore it on the day that matters.
    """
    _registry(tmp_path, [_entry("tx", ELIGIBLE_URL, eligible=True)])
    prober = StubProber({ELIGIBLE_URL: ProbeResult(url=ELIGIBLE_URL, outcome="unreachable")})
    argv = [
        "--registry",
        str(tmp_path / "registry.json"),
        "probe",
        "--db",
        str(tmp_path / "p.db"),
    ]
    assert main(argv, prober=prober) == 0


def test_the_command_says_in_band_that_a_probe_is_not_a_watch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry(tmp_path, [_entry("tx", ELIGIBLE_URL, eligible=True)])
    prober = StubProber(
        {ELIGIBLE_URL: ProbeResult(url=ELIGIBLE_URL, outcome="reachable", status=200)}
    )
    main(
        ["--registry", str(tmp_path / "registry.json"), "probe", "--db", str(tmp_path / "p.db")],
        prober=prober,
    )
    out = capsys.readouterr().out
    assert "created no observations" in out
    assert "NOT a source that was watched" in out


def test_the_report_command_reads_the_store_and_needs_no_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "p.db"
    with SnapshotStore(db) as store:
        for index, outcome in enumerate(["reachable", "unreachable", "reachable"]):
            store.record_probe_run(
                f"r{index}",
                as_of=f"2026-09-0{index + 1}",
                probed_at=f"2026-09-0{index + 1}T06:11:00+00:00",
                results=[
                    (
                        "tx",
                        ELIGIBLE_URL,
                        outcome,
                        200 if outcome == "reachable" else None,
                        90,
                        True,
                        "",
                        "",
                    )
                ],
            )

    assert main(["probe", "report", "--db", str(db), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["episodes_total"] == 1
    assert report["episodes_measured"] == 1
    assert report["episode_length_probes"]["max"] == 1


def test_the_report_is_byte_identical_across_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "p.db"
    with SnapshotStore(db) as store:
        store.record_probe_run(
            "r1",
            as_of="2026-09-01",
            probed_at="2026-09-01T06:11:00+00:00",
            results=[("tx", ELIGIBLE_URL, "reachable", 200, 90, True, "", "")],
        )
    argv = ["probe", "report", "--db", str(db), "--json"]
    assert main(argv) == 0
    first = capsys.readouterr().out
    assert main(argv) == 0
    assert capsys.readouterr().out == first
    assert "generated_at" not in first, "the report carries a clock and cannot be re-derived"


def test_every_outcome_is_classified_exactly_once() -> None:
    """The partition, asserted — because the overlap is silently harmless until it is not.

    `outage_episodes` filters unmeasured rows out before it consults the down set, so an
    outcome sitting in BOTH sets today changes nothing. That is exactly why this needs a test:
    the day somebody removes `head_unsupported` from the unmeasured set while leaving it in
    the down set, every 405 becomes an outage and no existing assertion notices. Three
    disjoint buckets covering the whole vocabulary is the invariant; the filter order is an
    implementation detail that happens to hide a violation of it.
    """
    from id_churn_sentinel.core import probe as module

    down = module._DOWN_OUTCOMES
    unmeasured = module._UNMEASURED_OUTCOMES
    up = {module.PROBE_REACHABLE}

    assert not (down & unmeasured), (
        f"outcome(s) {sorted(down & unmeasured)} are both an outage and not a measurement"
    )
    assert not (down & up) and not (unmeasured & up)
    assert down | unmeasured | up == set(PROBE_OUTCOMES), (
        "an outcome belongs to no bucket: it would neither open an episode nor be counted as "
        "unmeasured, which is a probe result that vanishes"
    )


# ---- the real prober, through its injected opener --------------------------------------------
#
# `HttpProber` is the only part of this channel that can open a socket, so it is the part most
# worth testing and the part a suite is most tempted to leave to integration. The `opener` seam
# exists for exactly this: every branch below is exercised with no network.


class _Response:
    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Fetcher:
    """Stands in for `HttpFetcher`'s politeness decisions, and records that they were asked."""

    def __init__(self, refusal: str | None = None) -> None:
        self.refusal = refusal
        self.spaced: list[str] = []

    def may_request(self, url: str) -> str | None:
        return self.refusal

    def space_before_request(self, host: str) -> None:
        self.spaced.append(host)


def _prober(opener: Any, *, refusal: str | None = None) -> Any:
    from id_churn_sentinel.core.probe import HttpProber

    clock = iter([0.0, 0.25, 0.5, 0.75])
    return HttpProber(
        fetcher=_Fetcher(refusal),  # type: ignore[arg-type]
        opener=opener,
        monotonic=lambda: next(clock),
    )


def test_a_two_hundred_is_reachable_and_carries_its_latency() -> None:
    calls: list[str] = []

    def opener(request: Any, timeout: float) -> Any:
        calls.append(request.get_method())
        return _Response(200, ELIGIBLE_URL)

    result = _prober(opener).probe(ELIGIBLE_URL)

    assert calls == ["HEAD"], "the availability channel issued something other than a HEAD"
    assert result.outcome == "reachable"
    assert result.status == 200
    assert result.latency_ms == 250
    assert result.redirect_target == ""


def test_a_redirect_records_where_it_landed() -> None:
    def opener(request: Any, timeout: float) -> Any:
        return _Response(200, "https://dps.texas.gov/moved")

    result = _prober(opener).probe(ELIGIBLE_URL)

    assert result.outcome == "reachable"
    assert result.redirect_target == "https://dps.texas.gov/moved"


@pytest.mark.parametrize("status", [405, 501])
def test_a_host_that_refuses_head_is_never_retried_as_get(status: int) -> None:
    """Falling back to GET would turn a cheap channel into a second crawl on a host that
    has just said no, and would make the probe record incomparable with itself."""
    import urllib.error

    methods: list[str] = []

    def opener(request: Any, timeout: float) -> Any:
        methods.append(request.get_method())
        raise urllib.error.HTTPError(ELIGIBLE_URL, status, "no", {}, None)  # type: ignore[arg-type]

    result = _prober(opener).probe(ELIGIBLE_URL)

    assert methods == ["HEAD"], f"the prober retried: {methods}"
    assert result.outcome == "head_unsupported"
    assert result.status == status


def test_a_http_error_status_is_an_http_error_not_an_unreachable_host() -> None:
    import urllib.error

    def opener(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(ELIGIBLE_URL, 503, "down", {}, None)  # type: ignore[arg-type]

    result = _prober(opener).probe(ELIGIBLE_URL)

    assert result.outcome == "http_error"
    assert result.status == 503
    assert result.tls_ok is True


def test_a_verification_failure_is_a_tls_error_not_an_outage() -> None:
    """Named separately because it is the host's defect, and because the two-line change that
    would 'resolve' it is disabling verification, which this project does not do."""
    import ssl
    import urllib.error

    def opener(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError(ssl.SSLCertVerificationError("chain not trusted"))

    result = _prober(opener).probe(ELIGIBLE_URL)

    assert result.outcome == "tls_error"
    assert result.tls_ok is False


def test_a_transport_failure_is_unreachable() -> None:
    import urllib.error

    def opener(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError("name resolution failed")

    result = _prober(opener).probe(ELIGIBLE_URL)

    assert result.outcome == "unreachable"
    assert result.status is None


def test_a_robots_refusal_never_opens_a_socket() -> None:
    from id_churn_sentinel.core.fetch import ERROR_CLASS_ROBOTS_DISALLOWED

    calls: list[str] = []

    def opener(request: Any, timeout: float) -> Any:  # pragma: no cover - must not run
        calls.append("opened")
        raise AssertionError("the prober requested a URL robots.txt disallows")

    result = _prober(opener, refusal=ERROR_CLASS_ROBOTS_DISALLOWED).probe(ELIGIBLE_URL)

    assert calls == []
    assert result.outcome == "robots_disallowed"
    assert result.latency_ms is None


def test_the_prober_applies_the_fetchers_crawl_spacing() -> None:
    """Two channels each politely waiting two seconds are one impolite channel."""
    from id_churn_sentinel.core.probe import HttpProber

    fetcher = _Fetcher()
    clock = iter([0.0, 0.1])
    prober = HttpProber(
        fetcher=fetcher,  # type: ignore[arg-type]
        opener=lambda request, timeout: _Response(200, ELIGIBLE_URL),
        monotonic=lambda: next(clock),
    )

    prober.probe(ELIGIBLE_URL)

    assert fetcher.spaced == ["dps.texas.gov"], "the probe skipped the shared crawl spacing"
