"""`sentinel` — the command-line surface.

    sentinel sources validate                      the registry gate (merge-blocking)
    sentinel sources check                         live liveness check (network; NOT a gate)
    sentinel sources check --twice                 find false-drift sources (network; NOT a gate)
    sentinel verify [--jurisdiction TX]            THE HUMAN VERIFICATION QUEUE (network)
    sentinel coverage                              the derived coverage numbers + the burn-down
    sentinel coverage --check-docs                 self-description drift gate (merge-blocking)
    sentinel watch [--jurisdiction TX]             fetch, hash, diff, record drift
    sentinel baseline write                        commit the store's hashes to sources/
    sentinel baseline check                        drift vs the COMMITTED baseline (no store)
    sentinel diff <change-id>                      the full diff for one change
    sentinel review <change-id> --reviewer ...     the human gate on a CHANGE
    sentinel publish --out dist/                   the site, the feeds, the inventory

Two different humans, two different commands, and they are not interchangeable. `review` is a
judgment about a **change** ("this diff matters"). `verify` is a judgment about a **source**
("this URL is the official page"). Both refuse to run without a name; neither can be done by a
machine; and today 0 of 152 sources have had the second one done to them, which every
published artifact says out loud.

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
from pathlib import Path

from id_churn_sentinel import __version__
from id_churn_sentinel.core.baseline import (
    check_baselines,
    default_baseline_path,
    load_baselines,
    write_baselines,
)
from id_churn_sentinel.core.changes import ChangeKind, ReviewStatus, Significance
from id_churn_sentinel.core.coverage import (
    DOC_PATHS,
    check_docs,
    completeness_violations,
    coverage,
)
from id_churn_sentinel.core.detect import REMOVAL_THRESHOLD, check_stability, watch
from id_churn_sentinel.core.fetch import Fetcher, HttpFetcher
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import (
    DOCUMENT_CLASSES,
    Registry,
    default_registry_path,
    load_registry,
)
from id_churn_sentinel.core.store import SnapshotStore
from id_churn_sentinel.core.verify import confirm, pending, reject, run_verification
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
            "fetch every source and compare against the COMMITTED baseline (network). Works "
            "from a clean checkout with no snapshot store."
        ),
    )
    baseline_check.add_argument("--baselines", type=Path, default=None)
    baseline_check.add_argument("--jurisdiction", help="limit to one jurisdiction, e.g. TX or US")

    diff_cmd = sub.add_parser("diff", help="show the full diff for one change")
    diff_cmd.add_argument("change_id")

    review_cmd = sub.add_parser("review", help="record a HUMAN review of one change")
    review_cmd.add_argument("change_id")
    review_cmd.add_argument(
        "--reviewer",
        required=True,
        help="the name of the human doing the review — required, and not optional by accident",
    )
    review_cmd.add_argument(
        "--significance",
        required=True,
        choices=[str(s) for s in Significance],
        help="the human's judgment; the tool never sets this itself",
    )
    review_cmd.add_argument(
        "--status",
        required=True,
        choices=[str(ReviewStatus.CONFIRMED), str(ReviewStatus.DISMISSED)],
    )
    review_cmd.add_argument("--note", default="", help="why (shown in the published feed)")

    publish_cmd = sub.add_parser("publish", help="write feed.xml + changes.json (reviewed only)")
    publish_cmd.add_argument("--out", type=Path, default=Path("dist"))

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
    if args.command == "review":
        return _cmd_review(args)
    return _cmd_publish(args, registry)


def _dispatch_sources(args: argparse.Namespace, registry: Registry, fetcher: Fetcher | None) -> int:
    if args.sources_command == "check":
        if args.twice:
            return _cmd_sources_stability(registry, fetcher)
        return _cmd_sources_check(registry, fetcher)
    return _cmd_sources_validate(registry, args.registry or default_registry_path())


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
        return 0

    if args.source_id:
        return _cmd_verify_one(args, path)

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
    )
    print(f"\nverify: {outcome.summary()}")
    print("Everything decided is already written to the registry — re-run to continue.")
    return 0


def _cmd_verify_one(args: argparse.Namespace, path: Path) -> int:
    """The scriptable single-source path. Same rules: a name, or nothing is written."""
    if args.confirm:
        recorded = confirm(path, args.source_id, verifier=args.verifier)
    elif args.reject:
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
    website being down must never fail someone's build."""
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
    print(f"sources check: {len(registry) - failures}/{len(registry)} reachable")
    return 0  # never a gate — an outage is not a build failure


def _cmd_sources_stability(registry: Registry, fetcher: Fetcher | None) -> int:
    """`sources check --twice`: find the sources that would cry wolf.

    A page that re-rolls a rotating widget on every request hashes differently twice in a
    row, and would therefore mint a change record every single week — with a diff about a
    rotating link list or a state-symbol fun fact. That is not a finding about the world; it
    is a defect in the registry, and the honest response is to watch a different page or to
    record the source as an unwatchable GAP. Not a gate: it is the tool a maintainer runs
    *before* adding a source, and it costs the host two fetches.
    """
    active = fetcher or HttpFetcher()
    report = check_stability(registry.sources, active)
    for source_id, first, second in report.unstable:
        print(f"  UNSTABLE  {source_id:<28} {first[:12]} != {second[:12]} (two fetches, no wait)")
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
    sources = (
        registry.for_jurisdiction(args.jurisdiction) if args.jurisdiction else registry.sources
    )
    active = fetcher or HttpFetcher()
    with SnapshotStore(args.db) as store:
        report = watch(sources, store, active, removal_threshold=args.removal_threshold)

    print(f"watch: {report.summary()}")
    for source_id, old_url, new_url in report.rebaselined:
        # The registry's URL for this source changed, so the stored baseline belongs to a
        # different page. Diffing them would produce a change record that says "the source
        # changed" when what changed is which page we watch. Re-baselined, and said out loud.
        print(f"  ↻ re-baselined (registry URL changed, NOT drift): {source_id}")
        print(f"      was: {old_url}")
        print(f"      now: {new_url}")
    escalated = {change.source_id for change in report.possibly_removed}
    for source_id, error in report.unreachable:
        # Reported, never counted as drift. This is the discipline inherited from
        # trans-docs-navigator's source-watch.ts, and it is the reason this tool can be
        # trusted: an outage cannot manufacture a policy change.
        if source_id in escalated:
            continue  # printed below, louder
        print(f"  ⚠️  unreachable (previous hash held, NOT drift): {source_id} — {error}")
    for gone in report.possibly_removed:
        # A source that has stopped answering for long enough that "it'll be back" is no
        # longer the most likely explanation. Not a content change, and NOT an assertion
        # that it was taken down — an escalation that a human is required to resolve.
        print(f"  ⛔ POSSIBLY REMOVED: {gone.source_id}  {gone.jurisdiction}/{gone.document_class}")
        print(f"      {gone.url}")
        print("      unreachable for too many consecutive runs — this is NOT auto-classified")
        print("      as a policy change. A human must decide: removed, blocked, or down?")
        print(f"      sentinel diff {gone.id}")
    for change in report.changed:
        print(f"  ✎ drift: {change.id}  {change.jurisdiction}/{change.document_class}")
        print(f"      {change.url}")
        print("      unreviewed — a human must review it before it can be published:")
        print(f"      sentinel diff {change.id}")
    pending = len(report.changed) + len(report.possibly_removed)
    if pending:
        print(
            f"\n{pending} change(s) recorded as UNCLASSIFIED/UNREVIEWED. "
            f"Nothing reaches the feed until a named human reviews it."
        )
    return 0


def _cmd_baseline_write(args: argparse.Namespace, registry: Registry) -> int:
    """Export the store's latest hash per source into `sources/baseline-hashes.json`.

    Committed, because without it a clean checkout has no memory: every source is a first
    sighting, a first sighting is a baseline rather than drift, and the tool cannot tell you
    that anything moved until it has watched for a week.
    """
    out = args.out or default_baseline_path()
    with SnapshotStore(args.db) as store:
        written = write_baselines(store, registry, out)
    print(f"baseline write: {written}/{len(registry)} source(s) → {out}")
    if written < len(registry):
        print(
            f"  ({len(registry) - written} source(s) have never been fetched successfully and "
            f"carry NO hash — a hash we did not observe is not a hash)"
        )
    return 0


def _cmd_baseline_check(
    args: argparse.Namespace, registry: Registry, fetcher: Fetcher | None
) -> int:
    """Compare every live source against the COMMITTED baseline. Network; never a gate.

    This is the command that makes a clean checkout useful. It answers "which of these pages
    is not what it was when the baseline was taken?" without the snapshot store — and it is
    honest about what it cannot do: it has the previous *hash*, not the previous *text*, so
    it cannot show the passage that changed. `sentinel watch` does that.
    """
    baselines = load_baselines(args.baselines)
    sources = (
        registry.for_jurisdiction(args.jurisdiction) if args.jurisdiction else registry.sources
    )
    active = fetcher or HttpFetcher()
    report = check_baselines(sources, active, baselines)

    for source_id, committed, current in report.moved:
        print(f"  ✎ MOVED   {source_id:<28} {committed[:12]} → {current[:12]}", flush=True)
    for source_id in report.unbaselined:
        print(f"  ?  no committed baseline: {source_id}", flush=True)
    for source_id, error in report.unreachable:
        # Same rule as everywhere else in this tool: an outage is not a content change.
        print(f"  ⚠️  unreachable (NOT drift): {source_id} — {error}", flush=True)
    print(f"baseline check: {report.summary()}")
    if report.moved:
        print(
            "\nA MOVED source is a fact about bytes, not a finding about the law, and this\n"
            "command cannot show you the passage that changed — the committed baseline holds\n"
            "the hash, not the text. Run `sentinel watch` (which retains the bytes) to get a\n"
            "reviewable diff, and a human decides what it means."
        )
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
        print(f"reviewed by:   {change.reviewer} — {change.review_note or '(no note)'}")
    if change.kind is ChangeKind.POSSIBLY_REMOVED:
        print("\n--- source unreachable: escalation for human review ---")
    else:
        print("\n--- changed passages (unified diff of normalized text) ---")
    print(change.diff_excerpt)
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """The human-in-the-loop gate, at the command line.

    `--reviewer` is required by argparse, non-empty by `ChangeRecord.reviewed_by`, and
    non-null-when-classified by the store's SQL CHECK. Three layers, because "the tool
    decided Texas substantively changed its policy" is a sentence that must never be true.
    """
    with SnapshotStore(args.db) as store:
        change = store.get_change(args.change_id)
        reviewed = change.reviewed_by(
            reviewer=args.reviewer,
            significance=Significance(args.significance),
            status=ReviewStatus(args.status),
            note=args.note,
        )
        store.update_change(reviewed)
    verb = "publishable" if reviewed.publishable else "recorded, not published"
    print(
        f"review: {reviewed.id} → {reviewed.significance}/{reviewed.review_status} "
        f"by {reviewed.reviewer} ({verb})"
    )
    return 0


def _cmd_publish(args: argparse.Namespace, registry: Registry) -> int:
    with SnapshotStore(args.db) as store:
        # Confirmed only, filtered in SQL. `publish()` re-asserts the predicate on every
        # record anyway — see core/publish.py::_guard.
        records = store.changes(review_status=ReviewStatus.CONFIRMED)
        unreviewed = len(store.changes(review_status=ReviewStatus.UNREVIEWED))
    result = publish(records, args.out, registry=registry)
    print(
        f"publish: {result.published} reviewed change(s) → {result.feed_path}, {result.changes_path}"
    )
    print(f"  site:      {result.site_path}")
    print(f"  inventory: {result.sources_path}")
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
