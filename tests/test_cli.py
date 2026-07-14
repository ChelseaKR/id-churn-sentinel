"""Tests for :mod:`id_churn_sentinel.cli`.

Every test injects a `StubFetcher`. `main()` is never called with a live fetcher, so the
CLI suite opens no sockets either.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from id_churn_sentinel.cli import build_parser, main
from id_churn_sentinel.core.changes import ChangeKind, ReviewStatus
from id_churn_sentinel.core.fetch import FetchResult
from id_churn_sentinel.core.registry import Source, default_registry_path
from id_churn_sentinel.core.store import SnapshotStore

from .conftest import StubFetcher, eligible_source_entry


@pytest.fixture
def cli_registry(tmp_path: Path, source: Source) -> Path:
    california = Source(
        id="ca-dmv",
        jurisdiction="CA",
        document_class="drivers_license",
        url="https://www.dmv.ca.gov/portal/x",
        authority="California DMV",
        verified=False,
        notes="synthetic test fixture",
    )
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "registry_version": "1.0",
                "sources": [
                    eligible_source_entry(source),
                    eligible_source_entry(california),
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def base_args(registry: Path, db: Path) -> list[str]:
    return ["--registry", str(registry), "--db", str(db)]


@pytest.mark.parametrize(
    "command", [("watch", "--as-of", "2026-01-01"), ("publish", "--as-of", "2026-01-01")]
)
def test_operational_commands_reject_an_operator_selected_policy_date(
    command: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(command)


# -- sources ---------------------------------------------------------------------


def test_sources_validate_passes_on_the_committed_registry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """This is `make sources-validate`, the merge gate."""
    assert main(["sources", "validate"]) == 0
    out = capsys.readouterr().out
    assert "entr(ies) OK" in out
    assert str(default_registry_path()) in out


def test_sources_validate_shouts_about_unverified_entries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Loud, permanent, and deliberately not a failure. The registry is SEEDED; pretending
    otherwise would be the exact overclaim this tool exists to avoid."""
    main(["sources", "validate"])
    out = capsys.readouterr().out
    assert "verified: false" in out
    assert "awaiting human verification" in out


def test_sources_validate_fails_on_a_bad_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "registry.json"
    bad.write_text('{"registry_version": "1.0", "sources": []}', encoding="utf-8")

    assert main(["--registry", str(bad), "sources", "validate"]) == 1
    assert "error:" in capsys.readouterr().err


def test_sources_check_reports_reachability_and_never_fails_the_build(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A state website being down must never fail someone's build, so `sources check` exits
    0 even when a source is unreachable. It is a human's verification aid, not a gate."""
    stub = StubFetcher({source.url: (fixture_before, "text/html")})  # ca-dmv is NOT configured

    exit_code = main(
        [*base_args(cli_registry, tmp_path / "s.db"), "sources", "check"], fetcher=stub
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "ok    tx-dps-change-dl-id" in out
    assert "FAIL  ca-dmv" in out
    assert "1/2 reachable" in out


def test_sources_check_twice_names_the_false_drift_sources(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--twice` is how a maintainer finds a page that would cry wolf every week. It is not
    a gate — a rotating widget on a state website is not a broken build — but it must name
    the source, loudly, before that source reaches a reviewer's queue."""

    class Rotating:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, url: str) -> FetchResult:
            self.calls += 1
            return FetchResult(
                url=url,
                ok=True,
                status=200,
                content_type="text/html",
                body=f"<p>state fish #{self.calls}</p>".encode(),
                fetched_at=datetime.now(UTC),
            )

    exit_code = main(
        [*base_args(cli_registry, tmp_path / "s.db"), "sources", "check", "--twice"],
        fetcher=Rotating(),
    )

    out = capsys.readouterr().out
    assert exit_code == 0  # never a gate
    assert f"UNSTABLE  {source.id}" in out
    assert "UNSTABLE (false-drift by construction)" in out
    assert "learn\nto ignore the feed" in out  # the reason it matters, said in-band


# -- watch -----------------------------------------------------------------------


def test_watch_records_a_baseline_then_detects_drift(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    fixture_after: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "s.db"
    args = base_args(cli_registry, db)
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    assert main([*args, "watch", "--jurisdiction", "TX"], fetcher=stub) == 0
    assert "1 new baseline" in capsys.readouterr().out

    stub.set(source.url, fixture_after)
    assert main([*args, "watch", "--jurisdiction", "TX"], fetcher=stub) == 0
    out = capsys.readouterr().out
    assert "1 changed" in out
    assert "drift:" in out
    assert "a human must review it" in out


def test_watch_reports_an_outage_as_not_drift(
    cli_registry: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [*base_args(cli_registry, tmp_path / "s.db"), "watch"], fetcher=StubFetcher({})
    )
    out = capsys.readouterr().out

    assert exit_code == 0  # an outage is not a build failure
    assert "2 unreachable (not drift)" in out
    assert "previous hash held, NOT drift" in out


def test_watch_escalates_a_long_dead_source_and_says_it_is_not_classified(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The M3 escalation, through the real CLI. A source that stops answering for long
    enough gets a loud, distinct line — and that line explicitly says the tool has NOT
    decided what the silence means."""
    db = tmp_path / "s.db"
    args = base_args(cli_registry, db)

    main(
        [*args, "watch", "--jurisdiction", "TX"],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )
    capsys.readouterr()

    for _ in range(3):
        exit_code = main(
            [*args, "watch", "--jurisdiction", "TX", "--removal-threshold", "3"],
            fetcher=StubFetcher({}),  # every fetch fails
        )

    out = capsys.readouterr().out
    assert exit_code == 0  # a dead source is not a build failure
    assert "POSSIBLY REMOVED" in out
    assert "NOT auto-classified" in out
    assert "removed, blocked, or down?" in out

    with SnapshotStore(db) as store:
        recorded = store.changes()
        assert len(recorded) == 1
        assert recorded[0].kind is ChangeKind.POSSIBLY_REMOVED
        assert recorded[0].review_status is ReviewStatus.UNREVIEWED


def test_watch_by_jurisdiction_only_fetches_that_jurisdiction(
    cli_registry: Path, tmp_path: Path, source: Source, fixture_before: bytes
) -> None:
    stub = StubFetcher({source.url: (fixture_before, "text/html")})
    main(
        [*base_args(cli_registry, tmp_path / "s.db"), "watch", "--jurisdiction", "TX"], fetcher=stub
    )

    assert stub.calls == [source.url]  # the CA source was never touched


def test_watch_with_an_unknown_jurisdiction_is_an_error(
    cli_registry: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--jurisdiction XX` silently watching nothing is the failure this tool exists to
    prevent, so a typo exits 1 rather than reporting a cheerful zero."""
    exit_code = main(
        [*base_args(cli_registry, tmp_path / "s.db"), "watch", "--jurisdiction", "XX"],
        fetcher=StubFetcher({}),
    )
    assert exit_code == 1
    assert "unknown jurisdiction" in capsys.readouterr().err


# -- diff / review / publish -----------------------------------------------------


@pytest.fixture
def drifted(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    fixture_after: bytes,
) -> tuple[list[str], str]:
    """Drive the CLI to a state with exactly one unreviewed change; return (args, change_id)."""
    db = tmp_path / "s.db"
    args = base_args(cli_registry, db)
    stub = StubFetcher({source.url: (fixture_before, "text/html")})
    main([*args, "watch"], fetcher=stub)
    stub.set(source.url, fixture_after)
    main([*args, "watch"], fetcher=stub)
    with SnapshotStore(db) as store:
        change_id = store.changes(review_status=ReviewStatus.UNREVIEWED)[0].id
    return args, change_id


def test_diff_shows_the_changed_passage(
    drifted: tuple[list[str], str], capsys: pytest.CaptureFixture[str]
) -> None:
    args, change_id = drifted
    capsys.readouterr()  # drop the watch output

    assert main([*args, "diff", change_id]) == 0
    out = capsys.readouterr().out
    assert "changed passages" in out
    assert "+a court order is required to change the sex field" in out
    assert "significance:  unclassified" in out


def test_diff_of_an_unknown_change_is_an_error(
    tmp_path: Path, cli_registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([*base_args(cli_registry, tmp_path / "s.db"), "diff", "deadbeef"])
    assert exit_code == 1
    assert "unknown change id" in capsys.readouterr().err


def test_review_confirms_a_change(
    drifted: tuple[list[str], str], capsys: pytest.CaptureFixture[str]
) -> None:
    args, change_id = drifted
    capsys.readouterr()

    exit_code = main(
        [
            *args,
            "review",
            change_id,
            "--reviewer",
            "Chelsea Kelly-Reif",
            "--significance",
            "substantive",
            "--status",
            "confirmed",
            "--note",
            "TX now requires a court order.",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "substantive/confirmed" in out
    assert "Chelsea Kelly-Reif" in out
    assert "(publishable)" in out


def test_review_can_dismiss_a_change_as_editorial(
    drifted: tuple[list[str], str], capsys: pytest.CaptureFixture[str]
) -> None:
    args, change_id = drifted
    capsys.readouterr()

    main(
        [
            *args,
            "review",
            change_id,
            "--reviewer",
            "A Human",
            "--significance",
            "editorial",
            "--status",
            "dismissed",
        ]
    )

    assert "recorded, not published" in capsys.readouterr().out


def test_review_rejects_confirming_without_classifying(
    drifted: tuple[list[str], str], capsys: pytest.CaptureFixture[str]
) -> None:
    args, change_id = drifted
    capsys.readouterr()

    exit_code = main(
        [
            *args,
            "review",
            change_id,
            "--reviewer",
            "A Human",
            "--significance",
            "unclassified",
            "--status",
            "confirmed",
        ]
    )

    assert exit_code == 1
    assert "requires classifying it" in capsys.readouterr().err


def test_publish_withholds_unreviewed_and_says_so(
    drifted: tuple[list[str], str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args, _ = drifted
    capsys.readouterr()
    out_dir = tmp_path / "published"

    assert main([*args, "publish", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "0 reviewed change(s)" in out
    assert "1 unreviewed change(s) withheld — they need a human first" in out
    assert json.loads((out_dir / "changes.json").read_text())["changes"] == []


def test_publish_emits_a_reviewed_change(
    drifted: tuple[list[str], str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args, change_id = drifted
    out_dir = tmp_path / "published"
    main(
        [
            *args,
            "review",
            change_id,
            "--reviewer",
            "A Human",
            "--significance",
            "substantive",
            "--status",
            "confirmed",
        ]
    )
    capsys.readouterr()

    assert main([*args, "publish", "--out", str(out_dir)]) == 0
    assert "1 reviewed change(s)" in capsys.readouterr().out
    payload = json.loads((out_dir / "changes.json").read_text())
    assert [c["id"] for c in payload["changes"]] == [change_id]
    assert (out_dir / "feed.xml").exists()


# -- plumbing --------------------------------------------------------------------


def test_no_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0


# -- baseline --------------------------------------------------------------------


def test_baseline_write_then_check_round_trips_without_a_store(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    fixture_after: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The point of the committed baseline: a CLEAN CHECKOUT, with no snapshot store, can
    still tell you which pages have moved. Here the check runs against a fresh db path that
    has never been written to — exactly the clean-clone case."""
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    main([*base_args(cli_registry, db), "watch"], fetcher=stub)
    assert main([*base_args(cli_registry, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    moved = StubFetcher({source.url: (fixture_after, "text/html")})
    exit_code = main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=moved,
    )

    out_text = capsys.readouterr().out
    assert exit_code == 0  # never a gate
    assert f"MOVED   {source.id}" in out_text
    assert "1 MOVED" in out_text
    assert "cannot show you the passage that changed" in out_text  # the honest limit
