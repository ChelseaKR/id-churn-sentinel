"""`sentinel` — the command-line surface.

    sentinel sources validate                      the registry gate (merge-blocking)
    sentinel sources check                         live liveness check (network; NOT a gate)
    sentinel sources check --twice                 find false-drift sources (network; NOT a gate)
    sentinel sources policy --source-id ...        a human's dated robots/terms fetch decision
    sentinel verify [--jurisdiction TX]            THE HUMAN VERIFICATION QUEUE (network)
    sentinel coverage                              the derived coverage numbers + the burn-down
    sentinel coverage --check-docs                 self-description drift gate (merge-blocking)
    sentinel watch [--jurisdiction TX]             fetch, hash, diff, record drift
    sentinel baseline write                        commit the store's hashes to sources/
    sentinel baseline check                        drift vs the COMMITTED baseline (no store)
    sentinel diff <change-id>                      the full diff for one change
    sentinel review --list [--jurisdiction TX]     the pending REVIEW queue (no network, no writes)
    sentinel review <change-id> --reviewer ...     the human gate on a CHANGE
    sentinel publish --out docs/                   the site, the feeds, the inventory

Three different humans, three different commands, and they are not interchangeable. `review` is
a judgment about a **change** ("this diff matters"). `verify` is a judgment about a **source**
("this URL is the official page"). `sources policy` is a judgment about a **host** ("their
robots.txt and terms permit us to watch this"). All three refuse to run without a name; none
can be done by a machine; and a source is fetched only when the last two have both been done to
it — today 0 of 152 sources have had either, which every published artifact says out loud.

The fetcher is a parameter of :func:`main`, not a global. `main()` with no fetcher and no
`watch` subcommand opens no sockets, which is why every test in this repo runs offline: the
suite calls `main([...], fetcher=StubFetcher())` and never once resolves a hostname. `ask` is
injected the same way, so the interactive verify loop is testable without a terminal.

Exit codes: 0 success, 1 a real failure (invalid registry, unknown id, refused review), 2
argparse usage error. `watch` exits 0 when it *finds* drift — drift is the tool working, not
the tool failing. Only `sources validate` is merge-blocking.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from id_churn_sentinel import __version__
from id_churn_sentinel.core.baseline import (
    BaselineReport,
    check_baselines,
    default_baseline_path,
    load_baselines,
    write_baselines,
)
from id_churn_sentinel.core.changes import (
    DEFAULT_PUBLIC_COPY,
    LIFECYCLE_REASONS,
    ChangeKind,
    ChangeRecord,
    IndependentReviewStatus,
    PublicationStatus,
    ReviewStatus,
    Significance,
)
from id_churn_sentinel.core.coverage import (
    DOC_PATHS,
    check_docs,
    completeness_violations,
    coverage,
)
from id_churn_sentinel.core.detect import (
    MIN_REMOVAL_SILENCE,
    REMOVAL_THRESHOLD,
    check_stability,
    watch,
)
from id_churn_sentinel.core.eligibility import (
    SourceEligibility,
    eligibility_report,
    evaluate_source,
    parse_as_of,
)
from id_churn_sentinel.core.fetch import Fetcher, HttpFetcher
from id_churn_sentinel.core.normalize import (
    CURRENT_CONTRACT,
    ContentKind,
    kind_for_content_type,
    normalize_html,
    normalize_text,
    page_title,
    passages,
)
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import (
    DOCUMENT_CLASSES,
    FETCH_POLICY_ALLOW,
    FETCH_POLICY_DENY,
    Registry,
    default_registry_path,
    load_registry,
)
from id_churn_sentinel.core.site import REPO_URL
from id_churn_sentinel.core.status import build_public_status
from id_churn_sentinel.core.store import SnapshotStore
from id_churn_sentinel.core.verify import (
    DEFAULT_EVIDENCE_DIR,
    FETCH_POLICY_RECHECK_DAYS,
    VERIFICATION_RECHECK_DAYS,
    Candidate,
    confirm,
    pending,
    record_fetch_policy,
    reject,
    run_verification,
    today,
    write_verification_receipt,
)
from id_churn_sentinel.errors import SentinelError

__all__ = ["build_parser", "main", "run"]

DEFAULT_DB = Path("var/sentinel.db")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description=(
            "Change detection over official US transgender ID-document sources. "
            "Reports that a source changed and what changed in it. Never asserts what "
            "the law is."
        ),
    )
    parser.add_argument("--version", action="version", version=f"id-churn-sentinel {__version__}")
    parser.add_argument("--registry", type=Path, default=None, help="path to sources/registry.json")
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"snapshot store path (default {DEFAULT_DB})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sources = sub.add_parser("sources", help="registry commands")
    sources_sub = sources.add_subparsers(dest="sources_command", required=True)
    sources_sub.add_parser("validate", help="validate the committed registry (merge-blocking)")
    eligibility_cmd = sources_sub.add_parser(
        "eligibility",
        help="report the fail-closed V1 watcher/publisher source denominator",
    )
    eligibility_cmd.add_argument(
        "--as-of",
        default=datetime.now(UTC).date().isoformat(),
        help="policy date in YYYY-MM-DD (default: today in UTC)",
    )
    # The second of the two decisions a source needs before it can be watched, and until now
    # the one nothing in this codebase could write: a human's dated reading of a host's
    # robots.txt and terms. Working the verification queue alone leaves every source
    # `fetch-policy-unreviewed` and the attempt denominator at zero (issue #18).
    policy_cmd = sources_sub.add_parser(
        "policy",
        help="record a NAMED human's dated robots/terms fetch-policy decision for one source",
        description=(
            "Record the fetch-policy decision the eligibility predicate requires. This tool "
            "does not make the decision and cannot: whether a host's robots.txt and terms "
            "permit a weekly watch is a reading of somebody else's document. It records who "
            "read it, when, on what evidence, for what reason, and until when. Verification "
            "and this decision are both required before a source is attempted."
        ),
    )
    policy_cmd.add_argument("--source-id", required=True, help="the registry entry to decide")
    policy_cmd.add_argument(
        "--outcome",
        required=True,
        choices=[FETCH_POLICY_ALLOW, FETCH_POLICY_DENY],
        help="allow: a person read the policy and we may watch it. deny: we may not.",
    )
    policy_cmd.add_argument(
        "--reviewer", required=True, help="the name of the human who read the policy. Required."
    )
    policy_cmd.add_argument(
        "--reason", required=True, help="why this outcome follows from what they read. Required."
    )
    policy_cmd.add_argument(
        "--evidence",
        required=True,
        help=(
            "a reference to what was read — a path to a saved robots.txt/terms receipt, or the "
            "URL of the terms and the date they were retrieved. Required, and not written for "
            "you: unlike a page excerpt, nothing in this tool has read these terms."
        ),
    )
    policy_cmd.add_argument(
        "--expires",
        default="",
        help=(
            f"YYYY-MM-DD this decision falls due for re-reading (default: "
            f"{FETCH_POLICY_RECHECK_DAYS} days out). A permission we have not re-read is not a "
            "permission we hold."
        ),
    )
    check_cmd = sources_sub.add_parser(
        "check", help="fetch every source and report status (network)"
    )
    check_cmd.add_argument(
        "--twice",
        action="store_true",
        help=(
            "fetch each source TWICE and report any whose normalized hash differs between "
            "the two — a page that re-rolls a rotating widget on every request is a "
            "false-drift source and must not be watched as-is. Doubles the load on the "
            "host: an operator's diagnostic, never the weekly job."
        ),
    )

    verify_cmd = sub.add_parser(
        "verify",
        help=(
            "THE HUMAN VERIFICATION QUEUE: fetch each unverified source, show a human its "
            "title and text, and record their confirm/reject WITH THEIR NAME (network)"
        ),
        description=(
            "Work the source-verification queue. For each source it prints the jurisdiction, "
            "document class, authority, URL, the page's own title and an excerpt of its "
            "normalized text, and asks ONE question: is this the official page for this "
            "document class in this jurisdiction? It records the answer in "
            "sources/registry.json with the verifier's name and the date, immediately, so the "
            "work is resumable. It will not record a verification without a name. It never "
            "answers the question itself. See docs/VERIFYING.md."
        ),
    )
    verify_cmd.add_argument(
        "--verifier",
        default="",
        help=(
            "the name of the human doing the verifying. Required to record anything — if it "
            "is not given here, you are asked for it per decision, and an empty answer is "
            "refused. An unsigned verification is indistinguishable from a machine's."
        ),
    )
    verify_cmd.add_argument("--jurisdiction", help="only this jurisdiction, e.g. TX or US")
    verify_cmd.add_argument(
        "--document-class",
        choices=sorted(DOCUMENT_CLASSES),
        help="only this document class (e.g. verify every state's birth certificate in one sitting)",
    )
    verify_cmd.add_argument(
        "--federal-first",
        action="store_true",
        help=(
            "put the US federal sources (passport, Social Security, Selective Service) at the "
            "front of the queue — they are the entries every jurisdiction's readers depend on"
        ),
    )
    verify_cmd.add_argument(
        "--limit", type=int, default=None, help="stop after this many sources (a sitting)"
    )
    verify_cmd.add_argument(
        "--list",
        action="store_true",
        help="print the pending queue and exit. No network, no prompts, no writes.",
    )
    # The non-interactive path: one decision, one command, scriptable — and subject to exactly
    # the same rule, because the rule is not about the interface. `--reason` is required to
    # reject, and a name is required to do either.
    verify_cmd.add_argument("--source-id", help="record a decision for ONE source, then exit")
    decision = verify_cmd.add_mutually_exclusive_group()
    decision.add_argument(
        "--confirm",
        action="store_true",
        help="with --source-id: record `verified: true`, naming --verifier and today's date",
    )
    decision.add_argument(
        "--reject",
        action="store_true",
        help="with --source-id: record that this is NOT the official page (needs --reason)",
    )
    verify_cmd.add_argument("--reason", default="", help="with --reject: why. Required.")
    verify_cmd.add_argument(
        "--evidence-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_DIR,
        help=(
            "where confirmation receipts are written — the record of what you were shown, "
            f"which the registry entry then cites (default {DEFAULT_EVIDENCE_DIR}). Under "
            "var/ and therefore untracked: a receipt carries an excerpt of whatever the page "
            "was serving, and raw evidence is never automatically public "
            "(docs/05-DATA-AND-EVIDENCE.md)."
        ),
    )
    verify_cmd.add_argument(
        "--evidence",
        default="",
        help=(
            "with --source-id --confirm: cite this reference instead of fetching the page and "
            "writing a receipt. For a source you verified elsewhere; the tool never invents one."
        ),
    )
    verify_cmd.add_argument(
        "--expires",
        default="",
        help=(
            f"with --confirm: YYYY-MM-DD this verification falls due for a recheck (default: "
            f"{VERIFICATION_RECHECK_DAYS} days out). A verification that cannot go stale can "
            "never be re-checked."
        ),
    )
    verify_cmd.add_argument(
        "--gap",
        action="store_true",
        help=(
            "with --reject: no right page exists to substitute, so move the entry OUT of the "
            "registry and into the named-gap list (reason `wrong-page`) rather than leaving it "
            "flagged for repair"
        ),
    )

    coverage_cmd = sub.add_parser(
        "coverage",
        help="the coverage numbers, DERIVED from the registry (never hand-written)",
    )
    coverage_cmd.add_argument(
        "--check-docs",
        action="store_true",
        help=(
            "MERGE GATE: re-derive every coverage number from the registry and fail if any "
            "doc disagrees — and fail if a jurisdiction/document-class pair is neither "
            "watched nor a named gap. A project whose pitch is 'we tell you what went "
            "stale' cannot have a stale front page."
        ),
    )
    coverage_cmd.add_argument("--json", action="store_true", help="machine-readable output")

    watch_cmd = sub.add_parser("watch", help="fetch sources and record any drift")
    watch_cmd.add_argument("--jurisdiction", help="limit to one jurisdiction, e.g. TX or US")
    watch_cmd.add_argument(
        "--removal-threshold",
        type=int,
        default=REMOVAL_THRESHOLD,
        help=(
            "consecutive failed fetches before a source escalates to `possibly_removed` "
            f"and requires human review (default {REMOVAL_THRESHOLD})"
        ),
    )
    watch_cmd.add_argument(
        "--min-removal-silence-days",
        type=int,
        default=int(MIN_REMOVAL_SILENCE.total_seconds() // 86_400),
        help=(
            "minimum days of unbroken silence before an escalation is allowed, whatever "
            "the failure count — so re-running the watcher several times in one sitting "
            "cannot manufacture a removal alarm (default "
            f"{int(MIN_REMOVAL_SILENCE.total_seconds() // 86_400)})"
        ),
    )

    baseline_cmd = sub.add_parser(
        "baseline", help="the committed baseline hashes (sources/baseline-hashes.json)"
    )
    baseline_sub = baseline_cmd.add_subparsers(dest="baseline_command", required=True)
    baseline_write = baseline_sub.add_parser(
        "write", help="export the store's latest hash per source into the committed file"
    )
    baseline_write.add_argument("--out", type=Path, default=None)
    baseline_check = baseline_sub.add_parser(
        "check",
        help=(
            "fetch every attempt-eligible source and compare against the COMMITTED baseline "
            "(network). Works from a clean checkout with no snapshot store."
        ),
    )
    baseline_check.add_argument("--baselines", type=Path, default=None)
    baseline_check.add_argument("--jurisdiction", help="limit to one jurisdiction, e.g. TX or US")

    diff_cmd = sub.add_parser("diff", help="show the full diff for one change")
    diff_cmd.add_argument("change_id")

    review_cmd = sub.add_parser("review", help="record a HUMAN review of one change")
    review_cmd.add_argument(
        "change_id", nargs="?", default=None, help="the change to review (omit with --list)"
    )
    review_cmd.add_argument(
        "--list",
        action="store_true",
        help=(
            "print the pending REVIEW queue (unreviewed changes already in the local store) "
            "and exit. No network, no prompts, no writes — the store-backed twin of "
            "`verify --list`, for a reviewer coming back after `watch`'s output has scrolled "
            "away or a review-queue issue has closed."
        ),
    )
    review_cmd.add_argument(
        "--jurisdiction", help="with --list: only this jurisdiction, e.g. TX or US"
    )
    review_cmd.add_argument(
        "--reviewer",
        default="",
        help=(
            "the name of the human doing the review — required to record anything, and not "
            "optional by accident"
        ),
    )
    review_cmd.add_argument(
        "--significance",
        choices=[str(s) for s in Significance],
        help="the human's judgment; the tool never sets this itself",
    )
    review_cmd.add_argument(
        "--status",
        choices=[str(ReviewStatus.CONFIRMED), str(ReviewStatus.DISMISSED)],
    )
    review_cmd.add_argument(
        "--note",
        default="",
        help="private internal rationale; never copied to public artifacts",
    )
    review_cmd.add_argument(
        "--public-copy",
        default=DEFAULT_PUBLIC_COPY,
        help="bounded factual observation copy; legal-claim terms fail closed",
    )

    approve_cmd = sub.add_parser(
        "approve", help="record an independent decision for a substantive first review"
    )
    approve_cmd.add_argument("change_id")
    approve_cmd.add_argument("--reviewer", required=True)
    approve_cmd.add_argument(
        "--status", required=True, choices=[str(value) for value in IndependentReviewStatus]
    )
    approve_cmd.add_argument("--qualification-ref", required=True)
    approve_cmd.add_argument("--conflict-attestation-ref", required=True)
    approve_cmd.add_argument(
        "--note", default="", help="private independent-review rationale; never public"
    )

    correct_cmd = sub.add_parser(
        "correct", help="append a visible supersession link without deleting history"
    )
    correct_cmd.add_argument("change_id")
    correct_cmd.add_argument("--replacement-id", required=True)
    correct_cmd.add_argument("--actor", required=True)
    correct_cmd.add_argument("--reason", required=True, choices=LIFECYCLE_REASONS)

    withdraw_cmd = sub.add_parser(
        "withdraw", help="append a visible withdrawal without deleting history"
    )
    withdraw_cmd.add_argument("change_id")
    withdraw_cmd.add_argument("--actor", required=True)
    withdraw_cmd.add_argument("--reason", required=True, choices=LIFECYCLE_REASONS)

    publish_cmd = sub.add_parser("publish", help="write feed.xml + changes.json (reviewed only)")
    # `docs/`, not `dist/`, and the reason is a hosting constraint rather than a preference:
    # branch-based GitHub Pages will serve exactly two source paths — the repo root or `/docs`
    # — and the Actions-based deploy that could serve any directory will never run under this
    # account's Actions spending limit. The published surface is committed, so `docs/` is
    # servable from the branch with no build step and no CI. See docs/README.md.
    publish_cmd.add_argument("--out", type=Path, default=Path("docs"))
    # The canonical home written into every artifact's `feed_url`. It defaults to the
    # repository, which resolves today; point it at the Pages URL once Pages is switched on.
    publish_cmd.add_argument("--feed-url", default=REPO_URL)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    fetcher: Fetcher | None = None,
    ask: Callable[[str], str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args, fetcher, ask)
    except SentinelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(
    args: argparse.Namespace, fetcher: Fetcher | None, ask: Callable[[str], str] | None
) -> int:
    registry = load_registry(args.registry)
    if args.command == "sources":
        return _dispatch_sources(args, registry, fetcher)
    if args.command == "baseline":
        if args.baseline_command == "check":
            return _cmd_baseline_check(args, registry, fetcher)
        return _cmd_baseline_write(args, registry)
    if args.command == "verify":
        return _cmd_verify(args, registry, fetcher, ask)
    if args.command == "coverage":
        return _cmd_coverage(args, registry)
    if args.command == "watch":
        return _cmd_watch(args, registry, fetcher)
    if args.command == "diff":
        return _cmd_diff(args)
    if args.command in {"review", "approve", "correct", "withdraw"}:
        return _dispatch_change_command(args)
    return _cmd_publish(args, registry)


def _dispatch_change_command(args: argparse.Namespace) -> int:
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "review": _cmd_review,
        "approve": _cmd_approve,
        "correct": _cmd_correct,
        "withdraw": _cmd_withdraw,
    }
    return handlers[args.command](args)


def _dispatch_sources(args: argparse.Namespace, registry: Registry, fetcher: Fetcher | None) -> int:
    if args.sources_command == "eligibility":
        return _cmd_sources_eligibility(registry, args.as_of)
    if args.sources_command == "policy":
        return _cmd_sources_policy(args)
    if args.sources_command == "check":
        if args.twice:
            return _cmd_sources_stability(registry, fetcher)
        return _cmd_sources_check(registry, fetcher)
    return _cmd_sources_validate(registry, args.registry or default_registry_path())


def _cmd_sources_policy(args: argparse.Namespace) -> int:
    """Write one dated fetch-policy decision, and say what it did and did not unlock.

    The closing line is the point. A reviewer who records `allow` has done half of what a
    source needs, and the other half is a different person's job on a different day — so the
    command reports the source's eligibility *after* the write rather than implying the
    decision was sufficient (issue #18).
    """
    path = args.registry or default_registry_path()
    decision = record_fetch_policy(
        path,
        args.source_id,
        outcome=args.outcome,
        reviewer=args.reviewer,
        reason=args.reason,
        evidence=args.evidence,
        expires_at=args.expires,
    )
    print(f"sources policy: {args.source_id} → {decision.outcome}")
    print(f"  reviewer:    {decision.reviewer} on {decision.at}")
    print(f"  evidence:    {decision.evidence}")
    print(f"  re-read due: {decision.expires_at}")
    print(f"  written to {path}")

    reloaded = load_registry(path)
    source = next((entry for entry in reloaded.sources if entry.id == args.source_id), None)
    if source is None:  # pragma: no cover - the writer above would have raised
        return 0
    decided = evaluate_source(source, as_of=datetime.now(UTC).date())
    if decided.eligible:
        print("  this source is now attempt-eligible: the watcher will attempt it.")
    else:
        print(
            "  this source is NOT yet attempt-eligible. A fetch-policy decision is one of two "
            "a source needs; still missing:"
        )
        for reason in decided.reasons:
            print(f"    {reason}")
    return 0


def _cmd_sources_eligibility(registry: Registry, raw_as_of: str) -> int:
    """Show the exact set enforced by both the watcher and publisher."""

    report = eligibility_report(registry, as_of=parse_as_of(raw_as_of))
    print(
        f"source eligibility as of {report.as_of.isoformat()}: "
        f"attempt denominator {len(report.attempt_source_ids)} source(s)"
    )
    print(
        f"  registry audit: {len(report.eligible)}/{len(report.decisions)} entries attempt-eligible"
    )
    for reason, count in report.reason_counts:
        print(f"  {reason}: {count}")
    print("  enforced by watcher and publisher; no policy decision is inferred")
    return 0


def _cmd_sources_validate(registry: Registry, path: Path) -> int:
    """The gate. Reaching this line means the registry loaded, which means every entry
    already passed: closed-vocabulary jurisdiction, closed-vocabulary document class,
    well-formed https URL with no fragment and no credentials, a named authority, a unique
    id, and no duplicate watch target — and that no entry claims `verified: true` without a
    named human and a date behind it. `load_registry` raises otherwise; there is no "warn and
    continue"."""
    print(f"sources validate: {len(registry)} entr(ies) OK in {path}")
    print(f"  jurisdictions: {len({s.jurisdiction for s in registry.sources})}")
    print(f"  document classes: {len({s.document_class for s in registry.sources})}")
    print(f"  named gaps: {len(registry.gaps)} (what we deliberately do NOT watch, and why)")
    print(
        f"  watched in name only: {len(registry.unreachable)} "
        f"(registered, but our own crawler cannot fetch them)"
    )
    print(f"  human-verified: {len(registry.verified_sources)}/{len(registry)}")
    if registry.rejected:
        print(f"  ✗ rejected by a human (wrong page, flagged for repair): {len(registry.rejected)}")
    # Loud, permanent, and deliberately not a failure. The registry is SEEDED, not verified;
    # pretending otherwise would be the exact overclaim this tool exists to avoid. It prints
    # every run until a human has checked each entry — and now it says how to do that, because
    # a warning with no next action is a warning people learn to scroll past.
    if registry.unverified:
        print(
            f"  ⚠️  {len(registry.unverified)}/{len(registry)} entries are `verified: false` — "
            f"machine-checked, awaiting human verification. Every published artifact says so, "
            f"next to every source, in words. Burn it down:\n"
            f"        sentinel verify --verifier 'Your Name' --federal-first   "
            f"(see docs/VERIFYING.md)"
        )
    return 0


def _cmd_verify(
    args: argparse.Namespace,
    registry: Registry,
    fetcher: Fetcher | None,
    ask: Callable[[str], str] | None,
) -> int:
    """The verification queue — the command that exists so the 152 can actually get done.

    Note what it will not do. It will not confirm anything on its own; it will not suggest an
    answer; it will not record a decision without a name. It fetches the page, shows the human
    what the page says about itself, and writes down what the human decided. The machine's job
    here is to make the human's job take thirty seconds instead of five minutes.
    """
    path = args.registry or default_registry_path()

    if args.list:
        queue = pending(
            registry,
            jurisdiction=args.jurisdiction,
            document_class=args.document_class,
            federal_first=args.federal_first,
            limit=args.limit,
        )
        for source in queue:
            print(f"  {source.jurisdiction:<3} {source.document_class:<24} {source.id}")
            print(f"      {source.url}")
        print(f"verify --list: {len(queue)} source(s) pending human verification")
        # The queue's length is not the answer to "is this repo watching anything yet", and a
        # volunteer deciding whether to spend an afternoon deserves the answer that is (#18).
        _print_eligibility_after_verification(registry)
        return 0

    if args.source_id:
        return _cmd_verify_one(args, path, fetcher)

    outcome = run_verification(
        registry,
        path,
        fetcher or HttpFetcher(),
        ask or input,
        print,
        verifier=args.verifier,
        jurisdiction=args.jurisdiction,
        document_class=args.document_class,
        federal_first=args.federal_first,
        limit=args.limit,
        evidence_dir=args.evidence_dir,
    )
    print(f"\nverify: {outcome.summary()}")
    print(f"verify: {outcome.eligibility_summary()}")
    print("Everything decided is already written to the registry — re-run to continue.")
    return 0


def _print_eligibility_after_verification(registry: Registry) -> None:
    """What the registry will actually watch, and what is stopping the rest.

    Printed by `verify --list` because that is the screen someone reads *before* deciding to
    work the queue. Ending a queue command on a count of pending items says how much work is
    left; it does not say whether the work already done reached the thing it was for.
    """
    report = eligibility_report(registry, as_of=datetime.now(UTC).date())
    print(
        f"  attempt-eligible today: {len(report.eligible)} of {len(report.decisions)} "
        f"registered source(s)"
    )
    if not report.ineligible:
        return
    print("  a source needs BOTH a human verification (with evidence and a recheck date) and a")
    print("  dated robots/terms fetch-policy decision before it is ever attempted. Blocked by:")
    for reason, count in report.reason_counts:
        print(f"    {reason}: {count}")
    print("  the second decision is `sentinel sources policy` — see docs/VERIFYING.md.")


def _cmd_verify_one(args: argparse.Namespace, path: Path, fetcher: Fetcher | None) -> int:
    """The scriptable single-source path. Same rules: a name, or nothing is written.

    A confirmation here fetches the page too, for the same reason the interactive path does:
    the evidence a verification cites is a receipt of what was actually on the page at the
    moment of the decision, and a script that skipped it would write the one kind of
    verification the predicate throws away (issue #18).
    """
    if args.confirm:
        source = next(
            (entry for entry in load_registry(path).sources if entry.id == args.source_id), None
        )
        if source is None:
            print(f"error: unknown source id: {args.source_id!r}", file=sys.stderr)
            return 1
        evidence = args.evidence
        if not evidence:
            candidate = Candidate.of(source, (fetcher or HttpFetcher()).fetch(source.url))
            evidence = str(
                write_verification_receipt(
                    candidate,
                    verifier=args.verifier.strip(),
                    at=today(),
                    directory=args.evidence_dir,
                )
            )
        recorded = confirm(
            path,
            args.source_id,
            verifier=args.verifier,
            evidence=evidence,
            expires_at=args.expires,
        )
        print(f"verify: {args.source_id} → {recorded.label}")
        print(f"  evidence:    {recorded.evidence}")
        print(f"  recheck due: {recorded.expires_at}")
        print(f"  written to {path}")
        return 0
    if args.reject:
        recorded = reject(
            path,
            args.source_id,
            verifier=args.verifier,
            reason=args.reason,
            to_gap=args.gap,
        )
    else:
        print(
            "error: --source-id needs --confirm or --reject. This command records a HUMAN's "
            "decision; it does not have one of its own.",
            file=sys.stderr,
        )
        return 1
    print(f"verify: {args.source_id} → {recorded.label}")
    print(f"  written to {path}")
    return 0


def _cmd_sources_check(registry: Registry, fetcher: Fetcher | None) -> int:
    """Live-fetch every source and print its status. This is the tool a human uses to
    verify a seeded entry before flipping `verified: true`. It is NOT a merge gate: a state
    website being down must never fail someone's build.

    Reachability alone does not mean a page has anything to watch (issue #19): a JS shell, a
    soft 404, and a bot-wall all answer `ok`. So a reachable text/HTML source also prints its
    passage count and its own `<title>` — the two things CLAUDE.md's guardrail #7 already asks
    a human to check by opening `sentinel sources check --twice` output and then reading the
    page, made visible here without a second command or opening the URL by hand. Zero passages
    is flagged inline; a title of "404 Page Not Found" or "Request Access" served with `ok` is
    exactly the trap this line exists to surface.
    """
    active = fetcher or HttpFetcher()
    failures = 0
    for source in registry.sources:
        result = active.fetch(source.url)
        if result.ok:
            line = f"  ok    {source.id:<28} {result.status} {source.url}"
        else:
            failures += 1
            line = f"  FAIL  {source.id:<28} {result.error} {source.url}"
        # flush=True: this loop can take minutes against two dozen government servers, and
        # Python buffers stdout when it is piped. Without the flush an operator watching
        # `sentinel sources check | tee log` sees nothing at all until the run ends — and
        # sees *nothing* if they lose patience and Ctrl-C it.
        print(line, flush=True)
        if result.ok and kind_for_content_type(result.content_type) != ContentKind.BINARY:
            print(f"        {_text_check_line(result.body, result.content_type)}", flush=True)
    print(f"sources check: {len(registry) - failures}/{len(registry)} reachable")
    return 0  # never a gate — an outage is not a build failure


def _text_check_line(body: bytes, content_type: str | None) -> str:
    """The passage count and page title a human would otherwise have to open the URL to see."""
    decoded = body.decode("utf-8", errors="replace")
    normalized = (
        normalize_html(decoded)
        if kind_for_content_type(content_type) == ContentKind.HTML
        else normalize_text(decoded)
    )
    count = len(passages(normalized))
    title = page_title(body) or "(no <title>)"
    if count == 0:
        return f'⚠ 0 passages — "{title}" — JS shell, soft 404, or bot-wall are typical causes'
    return f'{count} passage(s) — "{title}"'


def _cmd_sources_stability(registry: Registry, fetcher: Fetcher | None) -> int:
    """`sources check --twice`: find the sources that would cry wolf.

    A page that re-rolls a rotating widget on every request hashes differently twice in a
    row, and would therefore mint a change record every single week — with a diff about a
    rotating link list or a state-symbol fun fact. That is not a finding about the world; it
    is a defect in the registry, and the honest response is to watch a different page or to
    record the source as an unwatchable GAP. Not a gate: it is the tool a maintainer runs
    *before* adding a source, and it costs the host two fetches.

    A source that served no extractable text is printed on its own line and named in its own
    clause of the summary (issue #19). It used to be counted as `stable` and print nothing at
    all, which made this command answer "safe to watch" about a page `watch()` can never
    observe — the reassuring half of the sentence, on the guardrail that gates registry
    additions.
    """
    active = fetcher or HttpFetcher()
    report = check_stability(registry.sources, active)
    for source_id, first, second in report.unstable:
        print(f"  UNSTABLE  {source_id:<28} {first[:12]} != {second[:12]} (two fetches, no wait)")
    for source_id, url in report.no_text:
        print(f"  NO TEXT   {source_id:<28} 0 passages — not compared, stability unknown: {url}")
    for source_id, error in report.unreachable:
        print(f"  unreach   {source_id:<28} {error}", flush=True)
    print(f"sources check --twice: {report.summary()}")
    if report.unstable:
        print(
            "\nA source that hashes differently on two back-to-back fetches is a FALSE-DRIFT\n"
            "source: it will report a change every week forever, and the reviewer will learn\n"
            "to ignore the feed. Watch a stable page on that host, or record it as a GAP.\n"
            "Note the limit: passing this check does NOT prove a source is stable week over\n"
            "week — a widget that re-rolls hourly looks perfectly stable across two fetches."
        )
    if report.no_text:
        print(
            "\nA source that served NO extractable text was NOT judged stable or unstable —\n"
            "it was not compared at all. A JS shell, an empty 200 and a bot-wall all normalize\n"
            "to zero passages, which hashes to sha256('') and matches itself on every fetch,\n"
            "so this check cannot tell you anything about it. `sentinel watch` will route it to\n"
            "`no_text` every run and never observe it. Run `sentinel sources check` to see the\n"
            "page's own <title> — the soft 404s and bot-walls name themselves there — then\n"
            "watch a readable page on that host, or record the source as a GAP."
        )
    return 0  # never a gate


def _cmd_coverage(args: argparse.Namespace, registry: Registry) -> int:
    """Print the derived coverage numbers — and, with `--check-docs`, enforce them.

    This is the answer to a specific, unglamorous way that honest projects go dishonest:
    someone adds twenty sources, the README still says the old number, and the *most-read
    document in the repo* is now making a false claim about coverage — in the direction that
    understates or overstates what a legal-aid org can rely on. Nobody lied. Nobody noticed.

    So the numbers are not written; they are derived, and the gate re-derives them. It also
    checks the closed loop that matters more than any count: every (state, core document
    class) pair is either watched or a **named gap**. A hole nobody named is a hole nobody
    knows about, and this repo's whole claim is that its silence can be trusted to mean
    something. (It found DC and RI missing on the day it was written.)
    """
    report = coverage(registry)

    if args.json:
        payload = {
            "sources": report.sources_total,
            "jurisdictions_covered": report.jurisdictions_covered,
            "jurisdictions_total": report.jurisdictions_total,
            "named_gaps": report.gaps_total,
            "watched_in_name_only": report.unreachable_total,
            # Derived. This was the literal integer `0`, which was true when it was typed and
            # would have gone on being printed long after it stopped being true — the exact
            # class of stale self-description this module exists to make impossible.
            "human_verified": report.verified_total,
            "unverified": report.unverified_total,
            "rejected_by_a_human": report.rejected_total,
            "by_document_class": dict(report.by_document_class),
            "gaps_by_reason": dict(report.by_reason),
        }
        print(json.dumps(payload, indent=2))
        return 0

    for line in report.lines():
        print(line)

    if not args.check_docs:
        return 0

    holes = completeness_violations(registry)
    drifts = check_docs(report)
    if not holes and not drifts:
        print(
            f"\ncoverage --check-docs: OK — every coverage number in {len(DOC_PATHS)} "
            f"document(s) matches the registry, and every unwatched jurisdiction/"
            f"document-class pair is a named gap."
        )
        return 0

    if holes:
        print("\nREGISTRY IS NOT HONEST ABOUT ITS OWN HOLES:", file=sys.stderr)
        for hole in holes:
            print(f"  ✗ {hole}", file=sys.stderr)
    if drifts:
        print("\nA DOCUMENT DISAGREES WITH THE REGISTRY:", file=sys.stderr)
        for drift in drifts:
            print(f"  ✗ {drift}", file=sys.stderr)
        print(
            "\nDo not 'fix' this by editing the registry to match the prose. Run "
            "`sentinel coverage`, and write down what it actually says.",
            file=sys.stderr,
        )
    return 1


def _cmd_watch(args: argparse.Namespace, registry: Registry, fetcher: Fetcher | None) -> int:
    active = fetcher or HttpFetcher()
    with SnapshotStore(args.db) as store:
        report = watch(
            registry,
            store,
            active,
            jurisdiction=args.jurisdiction,
            removal_threshold=args.removal_threshold,
            min_removal_silence=timedelta(days=args.min_removal_silence_days),
        )

    print(
        f"watch: run {report.run_id} {report.state.upper()} — "
        f"{len(report.eligible_source_ids)} attempt-eligible source(s); {report.summary()}"
    )
    _print_ineligible_sources(report.ineligible)
    if report.state == "failed":
        print(
            "  FAILED: no eligible source was fetched. This run is not evidence that "
            "nothing changed.",
            file=sys.stderr,
        )
        return 1
    for source_id, old_url, new_url in report.rebaselined:
        # The registry's URL for this source changed, so the stored baseline belongs to a
        # different page. Diffing them would produce a change record that says "the source
        # changed" when what changed is which page we watch. Re-baselined, and said out loud.
        print(f"  ↻ re-baselined (registry URL changed, NOT drift): {source_id}")
        print(f"      was: {old_url}")
        print(f"      now: {new_url}")
    _print_renormalized_sources(report.renormalized)
    for source_id, recorded in report.unrenormalizable:
        # We could not restate this baseline under today's normalizer and we retain no bytes
        # to try again with. That is a gap in our evidence, not a finding about the page, and
        # it is said as such: no drift is claimed in either direction.
        print(
            f"  ↻ re-baselined (baseline recorded under {recorded} and NOT re-derivable; "
            f"NO drift claimed either way): {source_id}"
        )
    for source_id, url in report.no_text:
        # Zero passages, this run — not baselined, not compared against last week, and NOT
        # reported as unchanged. Printed every single run it recurs, on purpose: the bug this
        # closes (#19) was exactly a source going quiet inside a permanently green "unchanged"
        # bucket once its empty page first hashed the same as itself.
        print(f"  ∅ NO EXTRACTABLE TEXT (not baselined, NO drift claimed either way): {source_id}")
        print(f"      {url}")
        print("      a human should open this page: JS shell, soft 404, or bot-wall are typical")
    escalated = {change.source_id for change in report.possibly_removed}
    for source_id, error in report.unreachable:
        # Reported, never counted as drift. This is the discipline inherited from
        # an earlier content-hash watcher, and it is the reason this tool can be
        # trusted: an outage cannot manufacture a policy change.
        if source_id in escalated:
            continue  # printed below, louder
        print(f"  ⚠️  unreachable (previous hash held, NOT drift): {source_id} — {error}")
    for gone in report.possibly_removed:
        _print_pending_change(gone)
    for change in report.changed:
        _print_pending_change(change)
    pending = len(report.changed) + len(report.possibly_removed)
    if pending:
        print(
            f"\n{pending} change(s) recorded as UNCLASSIFIED/UNREVIEWED. "
            f"Nothing reaches the feed until a named human reviews it."
        )
    return 0


def _print_pending_change(change: ChangeRecord) -> None:
    """One line block for a change still waiting on a human. Shared by `watch` (fresh off the
    fetch) and `review --list` (re-read from the store later) so a reviewer sees the identical
    wording — and the same `sentinel diff` prompt — no matter which command told them about it.
    """
    if change.kind is ChangeKind.POSSIBLY_REMOVED:
        # A source that has stopped answering for long enough that "it'll be back" is no
        # longer the most likely explanation. Not a content change, and NOT an assertion
        # that it was taken down — an escalation that a human is required to resolve.
        print(
            f"  ⛔ POSSIBLY REMOVED: {change.source_id}  {change.jurisdiction}/{change.document_class}"
        )
        print(f"      {change.url}")
        print("      unreachable for too many consecutive runs — this is NOT auto-classified")
        print("      as a policy change. A human must decide: removed, blocked, or down?")
        print(f"      sentinel diff {change.id}")
    else:
        print(f"  ✎ drift: {change.id}  {change.jurisdiction}/{change.document_class}")
        print(f"      {change.url}")
        print("      unreviewed — a human must review it before it can be published:")
        print(f"      sentinel diff {change.id}")


def _print_renormalized_sources(renormalized: list[tuple[str, str, str]]) -> None:
    """One grouped line per contract transition — never one alarm per source.

    This is the shape the whole design is for. The first pass of a new normalizer over an
    existing corpus touches *every* source at once, and the operator reading it at 7am needs
    one sentence explaining why, not N lines they have to individually decide are harmless.
    Grouping by the transition itself is what makes it one sentence: the transition is the
    event, and the source list is its extent.
    """
    if not renormalized:
        return
    by_transition: dict[tuple[str, str], list[str]] = {}
    for source_id, was, now in renormalized:
        by_transition.setdefault((was, now), []).append(source_id)
    for (was, now), source_ids in sorted(by_transition.items()):
        print(f"  ↻ {len(source_ids)} source(s) re-baselined onto a new normalizer, NOT drift:")
        print(f"      {was} → {now}")
        print("      each baseline was re-normalized from its retained bytes and compared")
        print("      under the current normalizer; none of them changed. A version bump")
        print("      cannot report drift here, and cannot hide it either.")
        shown = ", ".join(sorted(source_ids)[:8])
        rest = len(source_ids) - 8
        print(f"      {shown}{f', … and {rest} more' if rest > 0 else ''}")


def _print_ineligible_sources(decisions: tuple[SourceEligibility, ...]) -> None:
    if not decisions:
        return
    counts: dict[str, int] = {}
    for decision in decisions:
        for reason in decision.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    print(f"  {len(decisions)} registry source(s) excluded by dated eligibility:")
    for reason, count in sorted(counts.items()):
        print(f"    {reason}: {count}")


def _cmd_baseline_write(args: argparse.Namespace, registry: Registry) -> int:
    """Export the store's latest hash per source into `sources/baseline-hashes.json`.

    Committed, because without it a clean checkout has no memory: every source is a first
    sighting, a first sighting is a baseline rather than drift, and the tool cannot tell you
    that anything moved until it has watched for a week.
    """
    out = args.out or default_baseline_path()
    with SnapshotStore(args.db) as store:
        written = write_baselines(store, registry, out)
    print(f"baseline write: {written.written}/{len(registry)} source(s) → {out}")
    if written.unreachable:
        print(
            f"  ({written.unreachable} source(s) have never been fetched successfully and "
            f"carry NO hash — a hash we did not observe is not a hash)"
        )
    if written.unmeasurable:
        # Only reachable from a store written before issue #19 was fixed, or by a writer that
        # bypasses the watcher. Said out loud rather than counted with the unreachable ones:
        # the operator needs to know a page answered and was unreadable, which is a different
        # thing to chase than a host that never answered.
        print(
            f"  ({written.unmeasurable} source(s) have a stored snapshot with NO readable text "
            f"and carry NO hash — the sha256 of nothing is not a baseline)"
        )
    return 0


def _refuse_empty_baseline_check() -> int:
    """A pass with an empty attempt denominator, reported as one and exited as one.

    Fail closed, exactly as `sentinel watch` does for the same condition and for the same
    reason: a run that attempted nothing observed nothing, and exiting 0 hands a caller a
    clean result it did not earn. This is deliberately NOT the "never a gate" case — that
    rule protects a state website being *down*, which is a source we tried and could not
    reach. Nothing was tried here, so no socket is opened and no fetcher is constructed.

    Every count marker is still emitted, and still zero. A workflow must be able to parse the
    same lines on every run: a marker that appears only on success makes its own absence
    ambiguous, which is the failure this whole block exists to remove.
    """
    print(f"baseline check: {BaselineReport().summary()}")
    print("baseline-check-moved-count: 0")
    print("baseline-check-cross-contract-count: 0")
    print("baseline-check-no-text-count: 0")
    print("baseline-check-url-changed-count: 0")
    print("baseline-check-unreachable-count: 0")
    print("baseline-check-observed-count: 0")
    print(
        "  FAILED: no attempt-eligible source was checked. This run is not evidence that "
        "nothing changed.",
        file=sys.stderr,
    )
    return 1


def _refuse_blind_baseline_check(report: BaselineReport) -> int:
    """A pass that reached every source it tried and read none of them, exited as one.

    The narrow companion to `_refuse_empty_baseline_check`, and the reason it is narrow is the
    rule it must not break: **a state website being down is never a broken build.** That rule
    protects a source we tried and could not reach, so that a real outage does not teach the
    humans to ignore a red badge — and it is untouched here. One unreachable source still
    exits 0. Ten still exit 0. A hundred and fifty-five out of a hundred and fifty-six still
    exit 0.

    This fires only when the count of sources we actually READ is zero: every single source
    either never answered or answered with nothing in it. At that point the sentence "a state
    website is down" has stopped being the explanation — every state's website is not down at
    once — and the likely cause is on our side of the wire: no egress from the runner, DNS,
    a proxy, TLS, a robots or redirect refusal, or a bug in the fetcher. Either way the report
    is the same one the empty denominator produces: this run observed nothing, and a caller
    handed exit 0 would read that as "nothing moved".

    `sentinel watch` has always refused this state — its receipt cannot be `quiet` unless every
    eligible source was retrieved AND measured (`_validate_terminal_evidence`). This command,
    the one the hosted weekly job actually runs, had no such refusal.
    """
    print(
        f"  FAILED: {report.total} source(s) were attempted and NOT ONE was read "
        f"({len(report.unreachable)} unreachable, {len(report.no_text)} with no extractable "
        "text). This run is not evidence that nothing changed. A single unreachable source is "
        "an outage and never fails this command; every source unreachable at once is usually "
        "this end of the wire (egress, DNS, proxy, TLS, robots) rather than every government "
        "host going down together.",
        file=sys.stderr,
    )
    return 1


def _print_baseline_buckets(report: BaselineReport) -> None:
    """One line per source, in its own bucket. Each bucket is a different claim, and the
    three non-drift ones are printed as loudly as MOVED on purpose: a source this pass could
    not compare is not a source it found unchanged."""
    for source_id, committed, current in report.moved:
        # The qualifier rides on the line itself, not only in a footer. A reviewer who scans
        # the MOVED lines and stops there must not come away believing a page changed when
        # what changed may be the normalizer the committed hash was taken with.
        recorded = report.moved_across_contracts.get(source_id)
        caveat = f"  (baseline normalizer: {recorded}; MAY be an artifact)" if recorded else ""
        print(
            f"  ✎ MOVED   {source_id:<28} {committed[:12]} → {current[:12]}{caveat}",
            flush=True,
        )
    for source_id in report.unbaselined:
        print(f"  ?  no committed baseline: {source_id}", flush=True)
    for source_id, baselined_url, registry_url in report.url_changed:
        # The registry points this source id somewhere else now, so the committed hash is
        # about a page this run never fetched. Reported loudly and as its own thing: calling
        # it MOVED would be a change record about a page that may not have changed, and
        # calling it a match would be worse.
        print(
            f"  ↻ REGISTRY URL CHANGED since the baseline was taken (NOT compared, no drift "
            f"claimed either way): {source_id}",
            flush=True,
        )
        print(f"      baseline was taken from: {baselined_url}", flush=True)
        print(f"      registry now points at:  {registry_url}", flush=True)
    for source_id, url in report.no_text:
        # Fetched fine, and unreadable: zero passages out of a page that promised text. Not
        # compared against the committed hash at all, because the comparison would be against
        # the hash of nothing — which is what every blind page in the registry hashes to.
        print(
            f"  ∅ NO EXTRACTABLE TEXT (NOT compared, no drift claimed either way): {source_id}",
            flush=True,
        )
        print(f"      {url}", flush=True)
        print(
            "      a human should open this page: JS shell, soft 404, or bot-wall are typical",
            flush=True,
        )
    for source_id, error in report.unreachable:
        # Same rule as everywhere else in this tool: an outage is not a content change.
        print(f"  ⚠️  unreachable (NOT drift): {source_id} — {error}", flush=True)


def _cmd_baseline_check(
    args: argparse.Namespace, registry: Registry, fetcher: Fetcher | None
) -> int:
    """Compare eligible live sources against the COMMITTED baseline. Network; never a gate.

    This is the command that makes a clean checkout useful. It answers "which of these pages
    is not what it was when the baseline was taken?" without the snapshot store — and it is
    honest about what it cannot do: it has the previous *hash*, not the previous *text*, so
    it cannot show the passage that changed. `sentinel watch` does that. It deliberately uses
    the same dated eligibility predicate as `sentinel watch`: a portable diagnostic is not
    permission to fetch a source whose verification or fetch-policy review is incomplete.
    """
    baselines = load_baselines(args.baselines)
    selected = (
        registry.for_jurisdiction(args.jurisdiction) if args.jurisdiction else registry.sources
    )
    as_of = datetime.now(UTC).date()
    eligibility = eligibility_report(registry, as_of=as_of)
    selected_ids = {source.id for source in selected}
    selected_decisions = tuple(
        decision for decision in eligibility.decisions if decision.source_id in selected_ids
    )
    eligible_ids = {decision.source_id for decision in selected_decisions if decision.eligible}
    sources = tuple(source for source in selected if source.id in eligible_ids)

    print(
        f"baseline eligibility as of {as_of.isoformat()}: "
        f"{len(sources)}/{len(selected)} selected source(s) attempt-eligible"
    )
    _print_ineligible_sources(
        tuple(decision for decision in selected_decisions if not decision.eligible)
    )
    # The attempt denominator, on its own machine-readable line and BEFORE any fetch, for the
    # same reason the three count markers below exist — and for a stronger one. Every numerator
    # this command prints is zero when nothing was checked, which is byte-identical to what a
    # complete run over sources that all matched prints. A workflow branching on a numerator
    # alone therefore reads "we examined nothing" as "nothing moved". Branch on this first.
    print(f"baseline-check-attempted-count: {len(sources)}")
    if not sources:
        return _refuse_empty_baseline_check()
    active = fetcher or HttpFetcher()
    report = check_baselines(sources, active, baselines)

    _print_baseline_buckets(report)
    print(f"baseline check: {report.summary()}")
    # A machine-readable count, on its own line, for CI to branch on. The prose summary above
    # always contains the word "MOVED" — including when it reads "0 MOVED" — so a workflow that
    # greps for the bare word fires on every single run. An alert that fires every week for
    # nothing is worse than no alert: the reviewer learns to close it unread, and then closes
    # it unread on the week a state quietly rewrites its passport page. Branch on this line,
    # never on the prose.
    print(f"baseline-check-moved-count: {len(report.moved)}")
    # The subset of that count whose committed hash came from a different normalizer, on its
    # own machine-readable line for the same reason the line above exists. A workflow that
    # alerts on the MOVED count alone would page a human for every one of these on the first
    # pass after a version bump — and by the reasoning above, an alert that fires for nothing
    # is the one that gets closed unread on the week it mattered. Subtract this from that to
    # get the count that is unambiguously about a page.
    print(f"baseline-check-cross-contract-count: {len(report.moved_across_contracts)}")
    # Sources this pass could not read at all, on their own machine-readable line, for the same
    # reason the two lines above exist — and because a workflow branching only on MOVED treats
    # a blind page as a quiet one. Zero here is a real measurement; the absence of the line is
    # not, which is why the workflow fails loudly when it is missing rather than assuming zero.
    print(f"baseline-check-no-text-count: {len(report.no_text)}")
    # Sources whose committed hash describes a page the registry no longer points at. Its own
    # machine-readable line for the same reason as the three above, and it must never be added
    # to the MOVED count: that count is what a workflow alerts a human with as "a source is no
    # longer what the baseline said", and this is a source we could not check at all.
    print(f"baseline-check-url-changed-count: {len(report.url_changed)}")
    # Sources that never answered. This bucket has been printed one line at a time since the
    # command was written and had NO machine-readable count, so the only number a workflow
    # could see about it was the one it was missing. An outage at one source is not a build
    # failure and never becomes one — but a workflow cannot tell "one host is down" from
    # "every host is down" without this line, and those are not the same fact.
    print(f"baseline-check-unreachable-count: {len(report.unreachable)}")
    # THE OBSERVATION NUMERATOR, and the twin of the attempt denominator printed before the
    # first fetch. `attempted` is deliberately reachability-blind: a source we tried and could
    # not reach stays in it, which is right for eligibility accounting and wrong as evidence
    # that anything was looked at. So a run in which all 156 hosts refused to answer prints
    # attempted=156 with every other count at 0 — byte-identical to a complete pass over 156
    # pages that all matched. That is the same fail-open the attempt denominator was added to
    # close (issue #25), one level down: there it was "we examined nothing", here it is "we
    # reached nothing", and both are the absence of evidence rather than a finding of quiet.
    # Branch on this together with the denominator, never on a drift numerator alone.
    print(f"baseline-check-observed-count: {report.observed}")
    if report.url_changed:
        print(
            f"\n{len(report.url_changed)} source(s) are registered at a DIFFERENT URL than the\n"
            "one their committed hash was taken from. Those pages were NOT compared against\n"
            "anything: the committed hash describes a document this run never fetched, and\n"
            "subtracting one page from an unrelated one is not drift detection. This run says\n"
            "nothing about whether those pages changed. Refresh the file with:\n"
            "  sentinel watch && sentinel baseline write"
        )
    if report.moved:
        print(
            "\nA MOVED source is a fact about bytes, not a finding about the law, and this\n"
            "command cannot show you the passage that changed — the committed baseline holds\n"
            "the hash, not the text. Run `sentinel watch` (which retains the bytes) to get a\n"
            "reviewable diff, and a human decides what it means."
        )
    if report.no_text:
        print(
            f"\n{len(report.no_text)} source(s) answered with no extractable text. Those pages\n"
            "were NOT compared against anything: every blind page hashes to the same sha256 of\n"
            "an empty string, so 'it matches the baseline' would be true of a page nobody can\n"
            "read. This run says nothing about whether those pages changed — a human has to\n"
            "open them, and a source that keeps landing here belongs in the GAPS block of\n"
            "sources/registry.json (`spa-no-text`), not in a reviewer's queue."
        )
    if report.moved_across_contracts:
        # Said once, loudly, and only when it applies. This command holds hashes and no
        # bytes, so unlike `sentinel watch` it cannot re-derive the old baseline and settle
        # the question — the honest move is to hand the operator the ambiguity plus the one
        # command that resolves it, not to pick an answer on their behalf.
        print(
            f"\n{len(report.moved_across_contracts)} of those MOVED hashes were recorded by a\n"
            f"DIFFERENT normalizer than the {CURRENT_CONTRACT} this build runs. A hash is only\n"
            "comparable against a hash from the same normalizer, and this file holds no bytes\n"
            "to re-normalize — so those lines may be measuring our normalizer, not the page.\n"
            "`sentinel watch` re-derives its baselines from retained bytes and IS able to tell\n"
            "the difference; it is the authority here. Refresh this file with:\n"
            "  sentinel watch && sentinel baseline write"
        )
    if not report.observed:
        return _refuse_blind_baseline_check(report)
    return 0  # never a gate — a state website being down is not a broken build


def _cmd_diff(args: argparse.Namespace) -> int:
    with SnapshotStore(args.db) as store:
        change = store.get_change(args.change_id)
    print(f"change {change.id}  [{change.jurisdiction}] {change.document_class}")
    print(f"kind:          {change.kind}")
    print(f"source:        {change.url}")
    print(f"observed:      {change.observed_at.isoformat()}")
    print(f"previous hash: {change.previous_hash}")
    print(f"new hash:      {change.new_hash or '(none — the source could not be fetched)'}")
    print(f"significance:  {change.significance}  (review status: {change.review_status})")
    if change.reviewer:
        print(f"reviewed by:   {change.reviewer}")
        print(f"public copy:   {change.review_note or '(not audited for publication)'}")
        print(f"internal note: {change.internal_rationale or '(none)'}")
    if change.independent_review_status is not None:
        print(f"independent:   {change.independent_review_status} by {change.independent_reviewer}")
    if change.publication_status is not PublicationStatus.ACTIVE:
        print(
            f"lifecycle:     {change.publication_status} "
            f"({change.lifecycle_reason}; by {change.lifecycle_actor})"
        )
    if change.kind is ChangeKind.POSSIBLY_REMOVED:
        print("\n--- source unreachable: escalation for human review ---")
    else:
        print("\n--- changed passages (unified diff of normalized text) ---")
    print(change.diff_excerpt)
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """The human-in-the-loop gate, at the command line.

    `--reviewer` is checked here (empty is refused), again by `ChangeRecord.reviewed_by`
    (non-empty), and again by the store's SQL CHECK (non-null-when-classified). Three layers,
    because "the tool decided Texas substantively changed its policy" is a sentence that must
    never be true.

    `--list` is the store-backed twin of `verify --list`: it answers "what still needs me"
    from what `watch` has already recorded, with no fetch and no write, for a reviewer who
    does not still have this morning's `watch` output on their screen.
    """
    with SnapshotStore(args.db) as store:
        if args.list:
            queue = store.changes(
                review_status=ReviewStatus.UNREVIEWED, jurisdiction=args.jurisdiction
            )
            for change in queue:
                _print_pending_change(change)
            print(f"review --list: {len(queue)} change(s) pending human review")
            return 0

        if not args.change_id or not args.reviewer or not args.significance or not args.status:
            print(
                "error: review needs a change id, --reviewer, --significance and --status "
                "(or `review --list` to see what is pending)",
                file=sys.stderr,
            )
            return 1

        change = store.get_change(args.change_id)
        reviewed = change.reviewed_by(
            reviewer=args.reviewer,
            significance=Significance(args.significance),
            status=ReviewStatus(args.status),
            note=args.note,
            public_copy=args.public_copy,
        )
        store.update_change(reviewed)
    verb = "publishable" if reviewed.publishable else "recorded, not published"
    print(
        f"review: {reviewed.id} → {reviewed.significance}/{reviewed.review_status} "
        f"by {reviewed.reviewer} ({verb})"
    )
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    with SnapshotStore(args.db) as store:
        change = store.get_change(args.change_id)
        reviewed = change.independently_reviewed_by(
            reviewer=args.reviewer,
            status=IndependentReviewStatus(args.status),
            qualification_ref=args.qualification_ref,
            conflict_attestation_ref=args.conflict_attestation_ref,
            rationale=args.note,
        )
        store.record_independent_review(reviewed)
    result = (
        "publishable"
        if reviewed.publishable
        else "returned, terminal for this immutable observation and not publishable"
    )
    print(
        f"approve: {reviewed.id} → {reviewed.independent_review_status} by "
        f"{reviewed.independent_reviewer} ({result})"
    )
    return 0


def _cmd_correct(args: argparse.Namespace) -> int:
    with SnapshotStore(args.db) as store:
        change = store.get_change(args.change_id)
        replacement = store.get_change(args.replacement_id)
        if not replacement.publishable:
            raise SentinelError("correction replacement is not independently publishable")
        corrected = change.corrected_by(
            replacement_id=replacement.id,
            actor=args.actor,
            reason=args.reason,
        )
        store.record_lifecycle_event(corrected)
    print(f"correct: {corrected.id} → {corrected.superseded_by} ({corrected.lifecycle_reason})")
    return 0


def _cmd_withdraw(args: argparse.Namespace) -> int:
    with SnapshotStore(args.db) as store:
        change = store.get_change(args.change_id)
        withdrawn = change.withdrawn_by(actor=args.actor, reason=args.reason)
        store.record_lifecycle_event(withdrawn)
    print(f"withdraw: {withdrawn.id} ({withdrawn.lifecycle_reason})")
    return 0


def _cmd_publish(args: argparse.Namespace, registry: Registry) -> int:
    with SnapshotStore(args.db) as store:
        # Confirmed only, projected from immutable review decisions. `publish()`
        # re-asserts the predicate on every record — see core/publish.py::_guard.
        records = store.changes(review_status=ReviewStatus.CONFIRMED)
        unreviewed = len(store.changes(review_status=ReviewStatus.UNREVIEWED))
        run_status = build_public_status(store)
    result = publish(
        records,
        args.out,
        registry=registry,
        feed_url=args.feed_url,
        run_status=run_status,
    )
    print(
        f"publish: {result.published} reviewed change(s) → {result.feed_path}, {result.changes_path}"
    )
    print(f"  site:      {result.site_path}")
    print(f"  inventory: {result.sources_path}")
    print(f"  run health: {result.status_path} ({run_status.state})")
    print(
        f"  per-jurisdiction feeds: {len(result.jurisdiction_feeds)} "
        f"(feed-us-tx.xml, changes-us-tx.json, … — one per jurisdiction, published whether "
        f"or not it has items yet)"
    )
    if unreviewed:
        print(f"  ({unreviewed} unreviewed change(s) withheld — they need a human first)")
    if registry.unverified:
        print(
            f"  ⚠️  every artifact above states that {len(registry.unverified)} of "
            f"{len(registry)} sources are UNVERIFIED — machine-checked, not human-confirmed. "
            f"That is published as a field on every source, not as a footnote."
        )
    return 0


def run() -> None:
    """Console-script entry point."""
    raise SystemExit(main())
