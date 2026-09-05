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
from .test_detect import record_v1_baseline


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


def test_sources_check_prints_the_passage_count_and_title_for_a_reachable_source(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The follow-on from issue #19: `ok` alone does not tell a maintainer whether a page has
    anything to watch. A real fixture with real page text prints a nonzero passage count and
    the page's own `<title>`, without a second command or opening the URL by hand."""
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    main([*base_args(cli_registry, tmp_path / "s.db"), "sources", "check"], fetcher=stub)

    out = capsys.readouterr().out
    assert "passage(s)" in out
    assert "⚠" not in out


def test_sources_check_flags_a_reachable_source_with_no_extractable_text(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The exact trap `docs/VERIFYING.md` warns a human about: a JS shell answers `ok` and
    carries nothing to watch. Visible here without reading the page by hand."""
    stub = StubFetcher(
        {
            source.url: (
                b"<html><head><script>var a=1;</script></head><body></body></html>",
                "text/html",
            )
        }
    )

    main([*base_args(cli_registry, tmp_path / "s.db"), "sources", "check"], fetcher=stub)

    out = capsys.readouterr().out
    assert "⚠ 0 passages" in out


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


def test_sources_check_twice_never_reports_a_blind_page_as_stable(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end, on the command CLAUDE.md guardrail #7 makes the gate for adding a source.

    A JS shell hashes to `sha256("")` twice in a row and matched itself, so `--twice` counted
    it as `stable` and printed no line for it at all — a silent clean pass for a page
    `sentinel watch` can never observe. The summary now has to say so, and the page has to
    appear on stdout.
    """
    stub = StubFetcher(
        {
            source.url: (
                b"<html><head><script>var a=1;</script></head>"
                b'<body><div id="root"></div></body></html>',
                "text/html",
            )
        }
    )

    exit_code = main(
        [*base_args(cli_registry, tmp_path / "s.db"), "sources", "check", "--twice"],
        fetcher=stub,
    )

    out = capsys.readouterr().out
    assert exit_code == 0  # still never a gate
    assert f"NO TEXT   {source.id}" in out
    assert "0 stable" in out
    assert "NO extractable text" in out
    assert "stability unknown" in out
    assert "UNSTABLE  " not in out  # it is not false drift either — it is unread


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

    # `--min-removal-silence-days 0` because three runs in one test take milliseconds, not
    # three weeks. The flag exists precisely so that distinction is explicit rather than
    # accidental: without it, this loop models an afternoon and must not escalate.
    for _ in range(3):
        exit_code = main(
            [
                *args,
                "watch",
                "--jurisdiction",
                "TX",
                "--removal-threshold",
                "3",
                "--min-removal-silence-days",
                "0",
            ],
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
    assert "recorded, not published" in out

    exit_code = main(
        [
            *args,
            "approve",
            change_id,
            "--reviewer",
            "Independent Reviewer",
            "--status",
            "confirmed",
            "--qualification-ref",
            "tests/qualification.json",
            "--conflict-attestation-ref",
            "tests/conflict.json",
        ]
    )
    assert exit_code == 0
    assert "(publishable)" in capsys.readouterr().out


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


def test_review_list_prints_the_pending_queue_without_writing_anything(
    drifted: tuple[list[str], str], capsys: pytest.CaptureFixture[str]
) -> None:
    """The store-backed twin of `verify --list`: a reviewer who does not still have
    `watch`'s output on screen can ask the local store what is still waiting on them, with
    no fetch and no write."""
    args, change_id = drifted
    capsys.readouterr()

    exit_code = main([*args, "review", "--list"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert change_id in out
    assert f"sentinel diff {change_id}" in out
    assert "unreviewed — a human must review it" in out
    assert "review --list: 1 change(s) pending human review" in out

    # Listing is read-only: the change is exactly as unreviewed as it was before.
    db = Path(args[args.index("--db") + 1])
    with SnapshotStore(db) as store:
        assert store.get_change(change_id).review_status is ReviewStatus.UNREVIEWED


def test_review_list_filters_by_jurisdiction(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    fixture_after: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "s.db"
    args = base_args(cli_registry, db)
    california_url = "https://www.dmv.ca.gov/portal/x"  # cli_registry's ca-dmv entry
    stub = StubFetcher(
        {
            source.url: (fixture_before, "text/html"),
            california_url: (fixture_before, "text/html"),
        }
    )
    main([*args, "watch"], fetcher=stub)
    stub.set(source.url, fixture_after)
    stub.set(california_url, fixture_after)
    main([*args, "watch"], fetcher=stub)
    capsys.readouterr()

    exit_code = main([*args, "review", "--list", "--jurisdiction", "CA"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "CA/drivers_license" in out
    assert "TX/drivers_license" not in out
    assert "review --list: 1 change(s) pending human review" in out


def test_review_list_reports_a_possibly_removed_escalation_too(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`review --list` is the same read as `watch`'s live output, so an escalation a
    reviewer missed when it first scrolled by is just as visible later."""
    db = tmp_path / "s.db"
    args = base_args(cli_registry, db)

    main(
        [*args, "watch", "--jurisdiction", "TX"],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )
    # `--min-removal-silence-days 0` because three runs in one test take milliseconds, not
    # three weeks — see the identical comment on the `watch`-side escalation test above.
    for _ in range(3):
        main(
            [
                *args,
                "watch",
                "--jurisdiction",
                "TX",
                "--removal-threshold",
                "3",
                "--min-removal-silence-days",
                "0",
            ],
            fetcher=StubFetcher({}),  # every fetch fails
        )
    capsys.readouterr()

    exit_code = main([*args, "review", "--list"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "POSSIBLY REMOVED" in out
    assert "NOT auto-classified" in out


def test_review_with_missing_arguments_is_a_clear_error_not_a_crash(
    drifted: tuple[list[str], str], capsys: pytest.CaptureFixture[str]
) -> None:
    """No `--list`, and not enough to record a decision either — refused with a message
    that names the way out, on both sides of the required set."""
    args, change_id = drifted
    capsys.readouterr()

    assert main([*args, "review"]) == 1
    assert "or `review --list`" in capsys.readouterr().err

    exit_code = main([*args, "review", change_id, "--reviewer", "A Human"])
    assert exit_code == 1
    assert "or `review --list`" in capsys.readouterr().err

    db = Path(args[args.index("--db") + 1])
    with SnapshotStore(db) as store:
        assert store.get_change(change_id).review_status is ReviewStatus.UNREVIEWED


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
    main(
        [
            *args,
            "approve",
            change_id,
            "--reviewer",
            "Independent Reviewer",
            "--status",
            "confirmed",
            "--qualification-ref",
            "tests/qualification.json",
            "--conflict-attestation-ref",
            "tests/conflict.json",
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
    assert "baseline-check-moved-count: 1" in out_text


def test_baseline_check_moved_count_is_zero_when_nothing_moved(
    cli_registry: Path,
    source: Source,
    fixture_before: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The count line must read 0 when nothing moved — and it is the ONLY line CI may branch on.

    The prose summary contains the word "MOVED" unconditionally ("… 0 MOVED, …"), so the
    watch workflow's original `grep -q "MOVED"` was true on every run and refiled the
    review-queue issue forever. This test pins the distinction: on a no-drift run the report
    still says "MOVED" somewhere, but the machine-readable count says zero.
    """
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    main([*base_args(cli_registry, db), "watch"], fetcher=stub)
    assert main([*base_args(cli_registry, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    unchanged = StubFetcher({source.url: (fixture_before, "text/html")})
    exit_code = main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=unchanged,
    )

    out_text = capsys.readouterr().out
    assert exit_code == 0
    assert "0 MOVED" in out_text  # the prose still carries the word — that was the trap
    assert "baseline-check-moved-count: 0" in out_text
    assert "cannot show you the passage that changed" not in out_text


def test_baseline_check_reports_an_unreadable_page_instead_of_a_match(
    cli_registry: Path,
    source: Source,
    fixture_before: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A page that turns into a JS shell must not be reported as matching anything, and the
    workflow needs its own machine-readable count: a job branching only on the MOVED count
    treats a blind page as a quiet one, which is how a scrubbed page stays unnoticed."""
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"

    main(
        [*base_args(cli_registry, db), "watch"],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )
    assert main([*base_args(cli_registry, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    # `ca-dmv` answers with a readable page so this pass is not blind end to end. The
    # unreadable source is the subject; a run in which NOTHING was read is a different
    # finding with its own refusal, and mixing the two here would test both badly.
    blind = StubFetcher(
        {
            source.url: (b"<html><head><script>x</script></head></html>", "text/html"),
            "https://www.dmv.ca.gov/portal/x": (fixture_before, "text/html"),
        }
    )
    exit_code = main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=blind,
    )

    out_text = capsys.readouterr().out
    assert exit_code == 0
    assert "baseline-check-no-text-count: 1" in out_text
    assert "baseline-check-moved-count: 0" in out_text
    assert "0 match the committed baseline" in out_text
    assert (
        f"NO EXTRACTABLE TEXT (NOT compared, no drift claimed either way): {source.id}" in out_text
    )
    assert "says nothing about whether those pages changed" in out_text


def test_baseline_check_emits_a_zero_no_text_count_when_every_page_was_readable(
    cli_registry: Path,
    source: Source,
    fixture_before: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The count line is unconditional. A missing line is not a zero, and the workflow is
    entitled to tell those apart rather than defaulting the absence to 'nothing wrong'."""
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    main([*base_args(cli_registry, db), "watch"], fetcher=stub)
    assert main([*base_args(cli_registry, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )

    out_text = capsys.readouterr().out
    assert "baseline-check-no-text-count: 0" in out_text
    assert "NO EXTRACTABLE TEXT" not in out_text


def test_baseline_check_refuses_to_call_a_run_that_read_nothing_a_clean_result(
    cli_registry: Path,
    source: Source,
    fixture_before: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The all-blind run, which used to be byte-identical to a clean one.

    `attempted` is deliberately reachability-blind, so a pass in which NO host answered still
    prints a non-zero attempt denominator with every drift count at zero — exactly what a
    complete pass over pages that all matched prints. The weekly workflow branched on those
    counts, concluded `needs-review=false`, filed nothing and went green, for a run that had
    not read a single page. This is the same refusal `sentinel watch` has always made (its
    receipt cannot be `quiet` unless every eligible source was retrieved and measured), made
    in the command the hosted job actually runs.
    """
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"
    main(
        [*base_args(cli_registry, db), "watch"],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )
    assert main([*base_args(cli_registry, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    # Nothing is configured, so every source in the registry fails to answer.
    exit_code = main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=StubFetcher({}),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    # The denominator is NOT zero — that is the whole trap this closes.
    assert "baseline-check-attempted-count: 2" in captured.out
    assert "baseline-check-observed-count: 0" in captured.out
    assert "baseline-check-unreachable-count: 2" in captured.out
    # And every drift count is zero, which is precisely why they cannot be branched on alone.
    assert "baseline-check-moved-count: 0" in captured.out
    assert "baseline-check-no-text-count: 0" in captured.out
    assert "NO SOURCE WAS READ" in captured.out
    assert "absence of evidence either way" in captured.out
    assert "NOT ONE was read" in captured.err


def test_baseline_check_still_exits_zero_when_only_some_sources_are_unreachable(
    cli_registry: Path,
    source: Source,
    fixture_before: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rule the refusal above must not break: a state website being down is NOT a build
    failure. `ca-dmv` never answers here and `tx-dps` does, so one page was read and the
    command exits 0 exactly as it always has. If this ever goes red, the humans learn to
    ignore the badge, and then they ignore it on the week that matters."""
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"
    main(
        [*base_args(cli_registry, db), "watch"],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )
    assert main([*base_args(cli_registry, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    exit_code = main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),  # ca-dmv is down
    )

    out_text = capsys.readouterr().out
    assert exit_code == 0
    assert "baseline-check-unreachable-count: 1" in out_text
    assert "baseline-check-observed-count: 1" in out_text
    assert "NO SOURCE WAS READ" not in out_text


def test_watch_names_an_unreadable_source_and_refuses_to_call_the_run_quiet(
    cli_registry: Path,
    source: Source,
    fixture_before: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What an operator actually reads at 7am. Every other source in this run is readable and
    unchanged, so the one blind page is the only thing standing between this run and QUIET —
    and it must be enough. The bug was that page settling into a permanently green
    `unchanged` bucket the moment its empty fetch hashed the same as itself."""
    db = tmp_path / "s.db"
    blind = StubFetcher(
        {
            source.url: (b"<html><body></body></html>", "text/html"),
            "https://www.dmv.ca.gov/portal/x": (fixture_before, "text/html"),
        }
    )

    assert main([*base_args(cli_registry, db), "watch"], fetcher=blind) == 0
    first = capsys.readouterr().out
    assert main([*base_args(cli_registry, db), "watch"], fetcher=blind) == 0
    second = capsys.readouterr().out

    assert "0 unreachable" in first  # nothing failed; the blind page is the only shortfall
    for output in (first, second):
        assert "QUIET" not in output
        assert "PARTIAL" in output
        assert "1 served NO extractable text" in output
        assert (
            f"NO EXTRACTABLE TEXT (not baselined, NO drift claimed either way): {source.id}"
            in output
        )
    # The second run is the one the old code got wrong: identical nothing, hashed against
    # itself, reported as an unremarkable unchanged source.
    assert "1 unchanged" in second  # the California page, and only it
    assert "2 unchanged" not in second


def test_baseline_check_never_fetches_sources_that_fail_canonical_eligibility(
    cli_registry: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The portable hash diagnostic is still a network path, so it must not become a
    backdoor around human verification or the dated fetch-policy decision.
    """
    raw = json.loads(cli_registry.read_text(encoding="utf-8"))
    for entry in raw["sources"]:
        entry["verified"] = False
        entry["verification"] = {
            "status": "unverified",
            "verifier": "",
            "at": "",
            "evidence": "",
            "expires_at": "",
        }
        entry["fetch_policy"] = {
            "outcome": "unreviewed",
            "reviewer": "",
            "at": "",
            "expires_at": "",
            "evidence": "",
            "reason": "",
        }
    ineligible_registry = tmp_path / "ineligible-registry.json"
    ineligible_registry.write_text(json.dumps(raw), encoding="utf-8")
    baselines = tmp_path / "baseline-hashes.json"
    baselines.write_text(json.dumps({"baseline_version": "1.0", "baselines": {}}), encoding="utf-8")
    stub = StubFetcher()

    exit_code = main(
        [
            *base_args(ineligible_registry, tmp_path / "unused.db"),
            "baseline",
            "check",
            "--baselines",
            str(baselines),
        ],
        fetcher=stub,
    )

    output = capsys.readouterr().out
    # The property this test is named for is unchanged and still asserted below: no source
    # that fails canonical eligibility is fetched. The exit code moved from 0 to 1, because a
    # run that fetched nothing observed nothing — and exiting 0 was how the weekly job read
    # "we checked no sources" as "no source moved". Nothing here is relaxed to accommodate it.
    assert exit_code == 1
    assert stub.calls == []
    assert "0/2 selected source(s) attempt-eligible" in output
    assert "fetch-policy-unreviewed: 2" in output
    assert "unverified: 2" in output


def test_watch_explains_a_normalizer_bump_once_instead_of_alarming_per_source(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_loose_end_tag: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What the operator actually sees on the v1→v2 transition: one grouped block naming the
    contract transition, not one drift alarm per source. `1 changed` must not appear — the
    pages did not move, the normalizer did."""
    db = tmp_path / "s.db"
    with SnapshotStore(db) as store:
        record_v1_baseline(store, source, fixture_loose_end_tag)
    stub = StubFetcher({source.url: (fixture_loose_end_tag, "text/html")})

    assert main([*base_args(cli_registry, db), "watch", "--jurisdiction", "TX"], fetcher=stub) == 0

    out = capsys.readouterr().out
    assert "1 source(s) re-baselined onto a new normalizer, NOT drift" in out
    assert "passage-text-v1/none-v1 → passage-text-v2/none-v1" in out
    assert "cannot report drift here, and cannot hide it either" in out
    assert "1 changed" not in out
    assert "✎ drift:" not in out  # the marker a reviewer scans for; no alarm was raised


def test_watch_says_so_when_a_baseline_cannot_be_re_derived(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gap-in-our-evidence case, said out loud rather than resolved in either direction."""
    db = tmp_path / "s.db"
    with SnapshotStore(db) as store:
        store.record_snapshot(
            source_id=source.id,
            url=source.url,
            fetched_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
            http_status=200,
            content_sha256="a" * 64,
            raw_bytes=b"",
            normalized_text="a passage we can no longer reproduce",
            normalizer_version="passage-text-v1",
            extractor_version="none-v1",
        )
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    assert main([*base_args(cli_registry, db), "watch", "--jurisdiction", "TX"], fetcher=stub) == 0

    out = capsys.readouterr().out
    assert "NO drift claimed either way" in out
    assert "passage-text-v1/none-v1" in out
    assert "1 changed" not in out


def test_baseline_check_flags_a_hash_recorded_by_a_different_normalizer(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The committed `sources/baseline-hashes.json` was written under `passage-text-v1`, so
    this is what a clean checkout running v2 sees today. The MOVED line still appears — a
    fresh clone must not be left mute — and it appears carrying the reason it might not mean
    what it says, on the line itself and once in full at the end."""
    committed = tmp_path / "baseline-hashes.json"
    committed.write_text(
        json.dumps(
            {
                "baseline_version": "1.0",
                "baselines": {
                    source.id: {
                        "url": source.url,
                        "sha256": "a" * 64,
                        "observed_at": "2026-07-13T19:58:22+00:00",
                        "normalizer_version": "passage-text-v1",
                        "extractor_version": "none-v1",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    exit_code = main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(committed),
        ],
        fetcher=stub,
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "MAY be an artifact" in out
    assert "baseline normalizer: passage-text-v1/none-v1" in out
    assert "may be a normalization artifact, not drift" in out
    assert "sentinel watch && sentinel baseline write" in out
    # Machine-readable, alongside #13's MOVED count: a workflow that alerts on MOVED alone
    # would page a human for every artifact on the first pass after a version bump.
    assert "baseline-check-moved-count: 1" in out
    assert "baseline-check-cross-contract-count: 1" in out


# -- the empty attempt denominator ------------------------------------------------
#
# `sentinel watch` has always failed closed on this condition. `sentinel baseline check` —
# the command the WEEKLY HOSTED JOB runs — did not: it printed "0 source(s): 0 match the
# committed baseline, 0 MOVED", emitted `baseline-check-moved-count: 0`, and exited 0. Those
# are the exact bytes a complete run over sources that all matched emits, so the workflow
# concluded `needs-review=false` and went green. With the committed registry at 0/152
# attempt-eligible that was the branch it took every single week: a monitor that observed
# nothing, reporting as a monitor that saw no change.


@pytest.fixture
def ineligible_cli_registry(tmp_path: Path, source: Source) -> Path:
    """A registry whose entries are real and whose eligibility is incomplete — the shape the
    committed `sources/registry.json` is in today (unverified, fetch-policy-unreviewed)."""
    path = tmp_path / "ineligible-registry.json"
    path.write_text(
        json.dumps(
            {
                "registry_version": "1.0",
                "sources": [
                    {
                        "id": source.id,
                        "jurisdiction": source.jurisdiction,
                        "document_class": source.document_class,
                        "url": source.url,
                        "authority": source.authority,
                        "verified": False,
                        "notes": "synthetic test fixture",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_baseline_check_fails_closed_when_no_source_was_attempted(
    ineligible_cli_registry: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A zero attempt denominator is not a clean result, and must not exit as one."""
    committed = tmp_path / "baseline-hashes.json"
    committed.write_text(
        json.dumps({"baseline_version": "1.0", "baselines": {}}),
        encoding="utf-8",
    )
    stub = StubFetcher()

    exit_code = main(
        [
            *base_args(ineligible_cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(committed),
        ],
        fetcher=stub,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    # Nothing was fetched, so no government server was contacted for a run that could not
    # have observed anything anyway.
    assert stub.calls == []
    assert "NO SOURCE WAS CHECKED" in captured.out
    assert "not evidence that nothing changed" in captured.err
    # The denominator marker exists and is zero, so a workflow can branch on the one number
    # that distinguishes "nothing moved" from "nothing was looked at".
    assert "baseline-check-attempted-count: 0" in captured.out


def test_baseline_check_publishes_its_attempt_denominator_on_a_real_run(
    cli_registry: Path,
    source: Source,
    fixture_before: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The marker is not a failure-only signal: a workflow must be able to read the
    denominator on every run, or the absence of the line is itself ambiguous."""
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"
    main(
        [*base_args(cli_registry, db), "watch"],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )
    assert main([*base_args(cli_registry, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    exit_code = main(
        [
            *base_args(cli_registry, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )

    out_text = capsys.readouterr().out
    assert exit_code == 0
    assert "baseline-check-attempted-count: 2" in out_text
    assert "NO SOURCE WAS CHECKED" not in out_text


def test_baseline_check_never_counts_a_re_pointed_source_as_moved(
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    fixture_after: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The MOVED count is what a workflow alerts a human with. A source whose registry URL
    changed was not compared against anything, so it must never reach that number."""
    db = tmp_path / "s.db"
    out = tmp_path / "baseline-hashes.json"
    original = tmp_path / "registry-original.json"
    original.write_text(
        json.dumps({"registry_version": "1.0", "sources": [eligible_source_entry(source)]}),
        encoding="utf-8",
    )
    main(
        [*base_args(original, db), "watch"],
        fetcher=StubFetcher({source.url: (fixture_before, "text/html")}),
    )
    assert main([*base_args(original, db), "baseline", "write", "--out", str(out)]) == 0
    capsys.readouterr()

    repointed_url = "https://www.dps.texas.gov/an-entirely-different-page"
    repointed = tmp_path / "registry-repointed.json"
    entry = eligible_source_entry(source)
    entry["url"] = repointed_url
    repointed.write_text(
        json.dumps({"registry_version": "1.0", "sources": [entry]}), encoding="utf-8"
    )

    exit_code = main(
        [
            *base_args(repointed, tmp_path / "never-written.db"),
            "baseline",
            "check",
            "--baselines",
            str(out),
        ],
        fetcher=StubFetcher({repointed_url: (fixture_after, "text/html")}),
    )

    out_text = capsys.readouterr().out
    assert exit_code == 0
    assert "baseline-check-moved-count: 0" in out_text
    assert "baseline-check-url-changed-count: 1" in out_text
    assert "REGISTRY URL CHANGED" in out_text
    assert f"baseline was taken from: {source.url}" in out_text
    assert f"registry now points at:  {repointed_url}" in out_text
    # The command must not offer the passage-diff follow-up here: there is no observed drift
    # to read, and pointing a reviewer at one would send them looking for a record that was
    # never minted.
    assert "cannot show you the passage that changed" not in out_text


def _dismiss_repeatedly(db: Path, source: Source, times: int) -> None:
    """Record `times` weekly drift observations on one source, each dismissed as editorial by
    a named human — the pattern a rotating page leaves in the review record."""
    from datetime import timedelta

    from id_churn_sentinel.core.changes import ChangeRecord, Significance

    monday = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
    with SnapshotStore(db) as store:
        for week in range(times):
            change = ChangeRecord.observed(
                source_id=source.id,
                jurisdiction=source.jurisdiction,
                document_class=source.document_class,
                url=source.url,
                previous_hash=str(week) * 64,
                new_hash=str(week + 1) * 64,
                diff_excerpt="-state fish\n+state reptile",
                observed_at=monday + timedelta(weeks=week),
            )
            store.record_change(change)
            store.update_change(
                change.reviewed_by(
                    reviewer="A Human",
                    significance=Significance.EDITORIAL,
                    status=ReviewStatus.DISMISSED,
                    reviewed_at=change.observed_at + timedelta(hours=2),
                )
            )


def test_sources_rotation_names_a_source_a_reviewer_keeps_dismissing(
    cli_registry: Path, tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    """The store-backed half of the false-drift signal, at the command line. No network, no
    registry fetch — it reads decisions humans already made and signed."""
    db = tmp_path / "s.db"
    _dismiss_repeatedly(db, source, 3)

    assert main([*base_args(cli_registry, db), "sources", "rotation"]) == 0

    out = capsys.readouterr().out
    assert f"POSSIBLE ROTATION  {source.id}" in out
    assert "3 consecutive editorial dismissal(s) over 14d" in out
    assert "dismissed by: A Human" in out
    assert out.count("sentinel diff ") == 3  # one runnable line per record, not ids joined
    assert "1 source(s) with 2 or more consecutive editorial dismissals" in out


def test_sources_rotation_says_out_loud_that_it_suppressed_nothing(
    cli_registry: Path, tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator reading "possible rotation" about a government page has every reason to
    wonder whether the tool has quietly stopped alerting on it. If they believed that, this
    feature would have manufactured the wrong "no change" it exists to help prevent — so the
    report says what it did NOT do, in band, every time, and names the fix a person has to
    make instead."""
    db = tmp_path / "s.db"
    _dismiss_repeatedly(db, source, 2)

    main([*base_args(cli_registry, db), "sources", "rotation"])

    out = capsys.readouterr().out
    assert "Nothing has been suppressed" in out
    assert "still watched and will still" in out
    assert "This tool is not" in out and "deciding between them." in out
    assert "Do not normalize the text away" in out
    assert "record the source as a GAP" in out


def test_sources_rotation_threshold_is_settable_and_the_command_is_never_a_gate(
    cli_registry: Path, tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "s.db"
    _dismiss_repeatedly(db, source, 2)

    assert main([*base_args(cli_registry, db), "sources", "rotation", "--threshold", "3"]) == 0

    out = capsys.readouterr().out
    assert "POSSIBLE ROTATION" not in out
    assert "no source has 3 consecutive editorial dismissal(s)" in out


def test_watch_surfaces_repeated_editorial_dismissals_without_being_asked(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ "Done when: repeated editorial dismissals on one source surface AUTOMATICALLY as a
    stability problem." A diagnostic an operator has to remember to run is a diagnostic that
    gets run once, so the weekly pass prints it — including on a quiet week, because a signal
    that only appears alongside an alarm is one nobody sees when the alarms stop being read.
    """
    db = tmp_path / "s.db"
    _dismiss_repeatedly(db, source, 2)
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    assert main([*base_args(cli_registry, db), "watch", "--jurisdiction", "TX"], fetcher=stub) == 0

    out = capsys.readouterr().out
    assert "a question about the registry, not a finding about the world" in out
    assert f"POSSIBLE ROTATION  {source.id}" in out


def test_watch_says_nothing_about_rotation_when_there_is_no_pattern(
    cli_registry: Path,
    tmp_path: Path,
    source: Source,
    fixture_before: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of "a reviewer actually works the queue": a block that printed on every
    run whether or not it had anything to say would be scrolled past by week three."""
    db = tmp_path / "s.db"
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    main([*base_args(cli_registry, db), "watch", "--jurisdiction", "TX"], fetcher=stub)

    assert "POSSIBLE ROTATION" not in capsys.readouterr().out
