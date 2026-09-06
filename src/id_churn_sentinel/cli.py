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
    sentinel evidence export <id> --out DIR        a portable bundle a third party can check
    sentinel evidence verify DIR                   recheck one, offline, with no store
    sentinel review --list [--jurisdiction TX]     the pending REVIEW queue (no network, no writes)
    sentinel review <change-id> --reviewer ...     the human gate on a CHANGE
    sentinel publish --out docs/                   the site, the feeds, the inventory

Three different humans, three different commands, and they are not interchangeable. `review` is
a judgment about a **change** ("this diff matters"). `verify` is a judgment about a **source**
("this URL is the official page"). `sources policy` is a judgment about a **host** ("their
robots.txt and terms permit us to watch this"). All three refuse to run without a name; none
can be done by a machine; and a source is fetched only when the last two have both been done to
it — today no source has had either, which every published artifact says out loud.

The fetcher is a parameter of :func:`main`, not a global. `main()` with no fetcher and no
`watch` subcommand opens no sockets, which is why every test in this repo runs offline: the
suite calls `main([...], fetcher=StubFetcher())` and never once resolves a hostname. `ask` is
injected the same way, so the interactive verify loop is testable without a terminal.

Exit codes: 0 success, 1 a real failure (invalid registry, unknown id, refused review), 2
argparse usage error. `watch` exits 0 when it *finds* drift — drift is the tool working, not
the tool failing. Only `sources validate` is merge-blocking.

The two `evidence` commands narrow 2 to a third meaning, and it is the one a consumer's
script has to be able to tell apart from a mismatch: `evidence verify` exits 0 verified, 1
a mismatch with the first failing file named, 2 the bundle could not be read at all, and
`evidence export` exits 2 when it refuses — a pruned snapshot, a removal escalation, a
populated destination — having written nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

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
    repo_root,
)
from id_churn_sentinel.core.detect import (
    MIN_REMOVAL_SILENCE,
    REMOVAL_THRESHOLD,
    WatchReport,
    check_stability,
    watch,
)
from id_churn_sentinel.core.eligibility import (
    SourceEligibility,
    eligibility_report,
    evaluate_source,
    parse_as_of,
    registry_revision,
)
from id_churn_sentinel.core.evidence import (
    EXIT_UNREADABLE,
    EXIT_VERIFIED,
    BundleError,
    export_bundle,
    render_verification,
    verify_bundle,
)
from id_churn_sentinel.core.fetch import Fetcher, HttpFetcher
from id_churn_sentinel.core.normalize import (
    CURRENT_CONTRACT,
    EXTRACTION_OUTCOME_PDF_REFUSED,
    EXTRACTION_OUTCOME_PDF_TEXT,
    ContentKind,
    content_evidence,
    kind_for_content_type,
    normalize_html,
    normalize_text,
    page_title,
    passages,
)
from id_churn_sentinel.core.probe import (
    HttpProber,
    Prober,
    dumps_report,
    probe_report,
    render_report,
    run_probe,
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
from id_churn_sentinel.core.registry_changelog import (
    changelog_document,
    default_changelog_path,
    diff_registries,
    dumps_changelog,
    load_changelog,
    read_registry_at,
    reconcile,
    seed_document,
)
from id_churn_sentinel.core.rotation import ROTATION_THRESHOLD, RotationReport, rotation_report
from id_churn_sentinel.core.site import REPO_URL
from id_churn_sentinel.core.staleness import (
    load_changes_document,
    load_manifest,
    render_text,
    staleness_report,
)
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
    rotation_cmd = sources_sub.add_parser(
        "rotation",
        help="name sources a reviewer keeps dismissing as editorial (reads the store; no network)",
        description=(
            "`sources check --twice` catches a page that re-rolls a widget on every REQUEST. "
            "It cannot catch one that re-rolls hourly or daily, and it cannot catch a page "
            "that starts rotating after it was registered — until now the only thing that "
            "noticed either was a reviewer dismissing the same source as `editorial` week "
            "after week, which is a signal that lived in one person's memory. This reads that "
            "signal out of the review record. It suppresses nothing, normalizes nothing, and "
            "decides nothing: repeated editorial dismissals are what a rotating page looks "
            "like from the queue, and also what a page that is genuinely edited every week "
            "looks like."
        ),
    )
    rotation_cmd.add_argument(
        "--threshold",
        type=int,
        default=ROTATION_THRESHOLD,
        help=(
            "consecutive editorial dismissals on one source before it is named (default "
            f"{ROTATION_THRESHOLD} — the number RESPONSIBLE-TECH already commits to; it is a "
            "policy in units of reviewed observations, not a measurement, and the streak "
            "length is always printed so the evidence outlives the constant)"
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

    registry_cmd = sub.add_parser("registry", help="commands over the registry as it changes")
    registry_sub = registry_cmd.add_subparsers(dest="registry_command", required=True)
    changelog_cmd = registry_sub.add_parser(
        "changelog",
        help="derive registry change events between two revisions (no network, no clock)",
        description=(
            "The registry is not a fixed list — sources get swapped for deeper pages, "
            "jurisdictions get closed through statute pages, a form moves from a source to a "
            "named gap. A consumer subscribed to feed-us-az.xml is subscribed to a "
            "jurisdiction and a document class, not to a URL, so it has no way to learn that "
            "the page behind an entry is not the page it was. This derives that history from "
            "two committed revisions, as closed-vocabulary events. It reads no network and "
            "no clock: the same two revisions always produce the same bytes."
        ),
    )
    changelog_cmd.add_argument(
        "--from",
        dest="from_ref",
        default="",
        metavar="REV_OR_PATH",
        help=(
            "the earlier revision: a git revision (e.g. HEAD~1, a tag, a sha) or a path to a "
            "registry file. A revision is read with `git show <rev>:sources/registry.json` "
            "and validated by the same loader the current registry goes through."
        ),
    )
    changelog_cmd.add_argument(
        "--to",
        dest="to_ref",
        default="",
        metavar="REV_OR_PATH",
        help="the later revision (default: the registry this invocation loaded)",
    )
    changelog_cmd.add_argument(
        "--append",
        action="store_true",
        help=(
            f"append the derived events to {default_changelog_path().name} instead of printing "
            "them. Refuses to append events already recorded for the same revision pair, so "
            "re-running it cannot duplicate history."
        ),
    )
    changelog_cmd.add_argument(
        "--init",
        action="store_true",
        help=(
            "write an EMPTY log that starts at the `--to` revision. Everything before it is "
            "marked unrecorded rather than reconstructed: deriving events from revisions that "
            "predate this schema is out of scope, and a log whose first entry looks like a "
            "beginning is worse than one that says where it begins."
        ),
    )

    stale_cmd = sub.add_parser(
        "stale",
        help="which of YOUR pages cite a source that has since changed (no network, no account)",
        description=(
            "The feed says a government page changed. It does not say which of your pages "
            "depend on it. Give this a manifest of your own pages — each with the source URLs "
            "it cites and its last-reviewed date — and it reports, per page, the confirmed "
            "changes to those sources observed since that date. The manifest never leaves "
            "your machine and nothing is sent anywhere: it reads a published artifact you "
            "already have a copy of. A citation this registry does not watch is reported as "
            "`unwatched`, never as current — silence about a page nobody watches is not "
            "evidence about that page."
        ),
    )
    stale_cmd.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="your manifest (docs/schema/consumer-manifest-v1.schema.json)",
    )
    stale_cmd.add_argument(
        "--changes",
        type=Path,
        default=None,
        help=(
            "a published changes document (default: the committed docs/changes.json, so this "
            "works from a clean clone with no network)"
        ),
    )
    stale_cmd.add_argument("--json", action="store_true", help="machine-readable output")

    probe_cmd = sub.add_parser(
        "probe",
        help="one HEAD per eligible source: availability only, no body, no snapshot (network)",
        description=(
            "The channel docs/THRESHOLD-EVIDENCE.md names and nothing implemented. "
            "REMOVAL_THRESHOLD and MIN_REMOVAL_SILENCE are guesses because weekly sampling "
            "cannot resolve a sub-weekly outage in principle — you cannot measure the length "
            "of something you look at once every seven days. This sends ONE HEAD request per "
            "eligible source and records whether the URL answered and how fast. It reads no "
            "body, writes no snapshot, creates no change record, and is never counted as a "
            "watch observation: a source that probed fine is not a source that was read."
        ),
    )
    probe_cmd.add_argument("--db", type=Path, default=DEFAULT_DB, help="snapshot store path")
    probe_cmd.add_argument("--jurisdiction", help="limit to one jurisdiction, e.g. TX or US")
    probe_cmd.add_argument("--json", action="store_true", help="machine-readable output")
    probe_sub = probe_cmd.add_subparsers(dest="probe_command")
    probe_report_cmd = probe_sub.add_parser(
        "report",
        help="derive outage episodes from the probe record (no network)",
        description=(
            "Outage episodes per source, with censoring reported rather than rounded away. An "
            "episode whose start or end this channel never saw has no measured length: it is "
            "excluded from every distribution here and counted separately, because a mean "
            "outage length taken over only the outages that happened to end is exactly the "
            "shape of number this project exists not to publish. Lengths are counted in "
            "PROBES, not hours, because the probe cadence is operator configuration."
        ),
    )
    probe_report_cmd.add_argument("--db", type=Path, default=DEFAULT_DB, help="snapshot store")
    probe_report_cmd.add_argument("--source-id", help="limit to one source")
    probe_report_cmd.add_argument("--json", action="store_true", help="machine-readable output")

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

    evidence_cmd = sub.add_parser(
        "evidence",
        help="export or re-check a portable evidence bundle for one change (no network)",
        description=(
            "A published change record carries hashes and an excerpt; the bytes that prove it "
            "live in the operator's store in var/ and are pruned to the newest few snapshots "
            "per source. An evidence bundle is a directory an operator hands over: both sides' "
            "raw bytes, both normalized texts, the contract versions, the fetch receipts, the "
            "re-derivable diff, the published record, and a manifest hashing every file. "
            "`verify` recomputes all of it from a clean clone, with no store and no network."
        ),
    )
    evidence_sub = evidence_cmd.add_subparsers(dest="evidence_command", required=True)
    evidence_export = evidence_sub.add_parser(
        "export",
        help="write the evidence bundle for one change, or refuse and write nothing",
        description=(
            "Refuses rather than exporting half an argument: a change whose baseline or "
            "current snapshot has been pruned names the missing side and writes nothing, and "
            "a removal escalation is refused because there are no `after` bytes at all. "
            "Exporting at review time is what pins the bytes against retention."
        ),
    )
    evidence_export.add_argument("change_id")
    evidence_export.add_argument(
        "--out",
        required=True,
        type=Path,
        metavar="DIR",
        help="destination directory; must not already contain anything",
    )
    evidence_verify = evidence_sub.add_parser(
        "verify",
        help="recompute every claim a bundle makes about itself (0 verified, 1 mismatch, 2 unreadable)",
        description=(
            "Recomputes every listed hash, refuses any file the manifest does not list, "
            "re-runs normalization under the recorded contract version and fails closed on a "
            "version this build does not implement, re-derives the diff, and checks that the "
            "bytes hash to the values the published record cites. A check that could not run "
            "is reported as SKIPPED with its reason and is never counted as a pass."
        ),
    )
    evidence_verify.add_argument("bundle", type=Path, metavar="DIR")
    evidence_verify.add_argument(
        "--changes",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "a published changes.json to cross-check the bundle's record against. Omitted, "
            "that one check reports SKIPPED — it is not silently treated as agreement."
        ),
    )
    evidence_verify.add_argument("--json", action="store_true", help="machine-readable output")

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
    prober: Prober | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args, fetcher, ask, prober)
    except SentinelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(
    args: argparse.Namespace,
    fetcher: Fetcher | None,
    ask: Callable[[str], str] | None,
    prober: Prober | None = None,
) -> int:
    registry = load_registry(args.registry)
    if args.command == "sources":
        return _dispatch_sources(args, registry, fetcher)
    if args.command in {"registry", "stale"}:
        return _dispatch_consumer_command(args, registry)
    if args.command == "baseline":
        if args.baseline_command == "check":
            return _cmd_baseline_check(args, registry, fetcher)
        return _cmd_baseline_write(args, registry)
    if args.command == "verify":
        return _cmd_verify(args, registry, fetcher, ask)
    if args.command == "coverage":
        return _cmd_coverage(args, registry)
    if args.command in {"watch", "probe"}:
        return _dispatch_network_command(args, registry, fetcher, prober)
    if args.command in {"diff", "evidence"}:
        return _dispatch_evidence_command(args)
    if args.command in {"review", "approve", "correct", "withdraw"}:
        return _dispatch_change_command(args)
    return _cmd_publish(args, registry)


def _dispatch_consumer_command(args: argparse.Namespace, registry: Registry) -> int:
    """The two commands that read the registry's own history or a consumer's own manifest."""
    if args.command == "stale":
        return _cmd_stale(args, registry)
    return _cmd_registry_changelog(args, registry)


def _dispatch_evidence_command(args: argparse.Namespace) -> int:
    """The three read-only commands over one recorded change and its retained bytes."""
    if args.command == "diff":
        return _cmd_diff(args)
    if args.evidence_command == "export":
        return _cmd_evidence_export(args)
    return _cmd_evidence_verify(args)


def _cmd_evidence_export(args: argparse.Namespace) -> int:
    """Write one bundle, or refuse with exit 2 having written nothing."""
    try:
        with SnapshotStore(args.db) as store:
            result = export_bundle(store, args.change_id, args.out)
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(f"evidence bundle for change {result.change_id} written to {result.bundle}")
    for name in result.files:
        print(f"  {name}")
    print("")
    print("Hand over the whole directory. A third party re-checks it with no store and no")
    print(f"network:  sentinel evidence verify {result.bundle}")
    return EXIT_VERIFIED


def _cmd_evidence_verify(args: argparse.Namespace) -> int:
    """Re-check one bundle. The exit code is the answer; the report says which check said so."""
    result = verify_bundle(args.bundle, changes_path=args.changes)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_verification(result))
    return result.exit_code


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
    if args.sources_command == "rotation":
        return _cmd_sources_rotation(args)
    if args.sources_command == "check":
        if args.twice:
            return _cmd_sources_stability(registry, fetcher)
        return _cmd_sources_check(registry, fetcher)
    return _cmd_sources_validate(registry, args.registry or default_registry_path())


def _cmd_stale(args: argparse.Namespace, registry: Registry) -> int:
    """Report which of a consumer's own pages cite a source that has since changed.

    Exit 0 whether or not anything is stale. This is a report a consumer runs against their own
    editorial queue, not a gate: a page that cites a changed source is a page somebody should
    look at, and turning that into a failing exit code would make the useful answer look like a
    broken tool.
    """
    manifest = load_manifest(args.manifest)
    changes_path = args.changes or (repo_root() / "docs" / "changes.json")
    document = load_changes_document(changes_path)
    report = staleness_report(manifest, document, registry)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(render_text(report), end="")
    return 0


def _cmd_registry_changelog(args: argparse.Namespace, registry: Registry) -> int:
    """Derive registry change events between two revisions, and optionally accumulate them.

    Three modes, and only one of them writes: printing (the default), `--init` (write the
    empty log that says where recorded history begins), and `--append` (extend it).

    `--append` refuses a revision pair the log already carries. That is not politeness about
    duplicates: an event list with the same swap in it twice would report two swaps, and a
    consumer counting source changes per jurisdiction would be counting a re-run of this
    command.
    """
    target = default_changelog_path()

    if args.init:
        if target.exists():
            print(
                f"error: {target} already exists. `--init` writes the starting marker and "
                f"would overwrite recorded history.",
                file=sys.stderr,
            )
            return 1
        target.write_text(dumps_changelog(seed_document(registry)), encoding="utf-8")
        print(f"wrote {target} — empty, starting at this registry revision.")
        return 0

    if not args.from_ref:
        print(
            "error: --from is required unless --init is given. There is no default earlier "
            "revision: guessing one would silently pick which history got recorded.",
            file=sys.stderr,
        )
        return 1

    before = read_registry_at(args.from_ref)
    after = read_registry_at(args.to_ref) if args.to_ref else registry
    events = diff_registries(before, after)

    if not args.append:
        print(
            dumps_changelog(
                changelog_document(events, unrecorded_before=registry_revision(before))
            ),
            end="",
        )
        return 0

    document = load_changelog(target)
    recorded = document["events"]
    pairs = {(event["from_revision"], event["to_revision"]) for event in recorded}
    fresh = [event.to_dict() for event in events]
    already = sorted({(e["from_revision"], e["to_revision"]) for e in fresh} & pairs)
    if already:
        print(
            f"error: {target} already records events for revision pair "
            f"{already[0][0][:12]}..{already[0][1][:12]}. Appending them again would report "
            f"one change twice.",
            file=sys.stderr,
        )
        return 1

    document["events"] = [*recorded, *fresh]
    violations = reconcile(document, registry)
    if violations:
        print("\nTHE APPENDED LOG DOES NOT RECONCILE WITH THE REGISTRY:", file=sys.stderr)
        for violation in violations:
            print(f"  ✗ {violation}", file=sys.stderr)
        return 1
    target.write_text(dumps_changelog(document), encoding="utf-8")
    print(f"appended {len(fresh)} event(s) to {target}.")
    return 0


def _dispatch_network_command(
    args: argparse.Namespace,
    registry: Registry,
    fetcher: Fetcher | None,
    prober: Prober | None,
) -> int:
    """The two commands that touch the network, kept apart on purpose.

    `watch` gets a `Fetcher` and `probe` gets a `Prober`, and neither can be handed the
    other's client. A prober that could reach `watch`, or a fetcher that could reach `probe`,
    is one refactor away from a body request on the availability channel.
    """
    if args.command == "watch":
        return _cmd_watch(args, registry, fetcher)
    return _dispatch_probe(args, registry, prober)


def _dispatch_probe(args: argparse.Namespace, registry: Registry, prober: Prober | None) -> int:
    if getattr(args, "probe_command", None) == "report":
        return _cmd_probe_report(args)
    return _cmd_probe(args, registry, prober)


def _cmd_probe(args: argparse.Namespace, registry: Registry, prober: Prober | None) -> int:
    """One HEAD pass, recorded to `probes` and to nothing else.

    Exit 0 whether or not anything was reachable. An outage is the fact this channel exists to
    record; turning it into a non-zero exit would make a working measurement look like a
    broken tool, and would make the daily job red for as long as a state website is down.
    """
    active = prober if prober is not None else HttpProber()
    as_of = datetime.now(UTC).date()
    started = datetime.now(UTC)
    run_id = uuid4().hex
    run = run_probe(
        registry,
        active,
        as_of=as_of,
        run_id=run_id,
        now=started,
        jurisdiction=args.jurisdiction,
    )
    with SnapshotStore(args.db) as store:
        store.record_probe_run(
            run_id,
            as_of=as_of.isoformat(),
            probed_at=started.isoformat(),
            results=[
                (
                    source_id,
                    result.url,
                    result.outcome,
                    result.status,
                    result.latency_ms,
                    result.tls_ok,
                    result.redirect_target,
                    result.error,
                )
                for source_id, result in run.results
            ],
        )

    counts = run.counts()
    if args.json:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "as_of": as_of.isoformat(),
                    "probed_at": started.isoformat(),
                    "attempted": run.attempted,
                    "skipped": run.skipped,
                    "outcomes": counts,
                },
                indent=2,
            )
        )
        return 0
    print(f"probe run {run_id} ({as_of.isoformat()})")
    print(f"  attempted: {run.attempted}   not measured: {run.skipped}")
    for outcome, count in counts.items():
        print(f"    {outcome:<20} {count}")
    print(
        "\nThis run read no page bodies and created no observations. A source that probed "
        "reachable is NOT a source that was watched."
    )
    return 0


def _cmd_probe_report(args: argparse.Namespace) -> int:
    with SnapshotStore(args.db) as store:
        rows = store.probes(source_id=args.source_id)
    report = probe_report(rows)
    if args.json:
        print(dumps_report(report), end="")
        return 0
    print(render_report(report), end="")
    return 0


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
        # The whole queue first, then the sitting. `--limit` is a page size, not a
        # measurement: asking for it must never shrink the count this reports. Taking the
        # length of the *truncated* list said "verify --list: 5 source(s) pending human
        # verification" for `--limit 5` against a registry with 151 pending — a capped read
        # rendered as the total, which is the one thing this project refuses everywhere else.
        matching = pending(
            registry,
            jurisdiction=args.jurisdiction,
            document_class=args.document_class,
            federal_first=args.federal_first,
        )
        queue = matching[: args.limit] if args.limit else matching
        for source in queue:
            print(f"  {source.jurisdiction:<3} {source.document_class:<24} {source.id}")
            print(f"      {source.url}")
        if len(queue) < len(matching):
            print(
                f"verify --list: showing {len(queue)} of {len(matching)} source(s) pending "
                f"human verification (--limit {args.limit})"
            )
        else:
            print(f"verify --list: {len(matching)} source(s) pending human verification")
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
        if result.ok:
            print(f"        {_text_check_line(result.body, result.content_type)}", flush=True)
    print(f"sources check: {len(registry) - failures}/{len(registry)} reachable")
    return 0  # never a gate — an outage is not a build failure


def _text_check_line(body: bytes, content_type: str | None) -> str:
    """The passage count and page title a human would otherwise have to open the URL to see."""
    if kind_for_content_type(content_type) == ContentKind.BINARY:
        return _binary_check_line(body, content_type)
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


def _binary_check_line(body: bytes, content_type: str | None) -> str:
    """Whether a PDF source will produce a reviewable diff — the thing a maintainer adding
    one needs to know, and could previously only find out by watching it for a week.

    A refusal is printed with its reason rather than hidden, for two reasons. It tells the
    maintainer what this source's weekly alert will actually look like (`the bytes changed`,
    with no passages), which is a fair thing to know before registering it. And across the
    registry it makes the population of documents the extractor cannot read *countable*,
    which is the only honest basis for deciding whether to widen the subset — see
    `core/pdf.py`.
    """
    evidence = content_evidence(body, content_type)
    if evidence.extraction_outcome == EXTRACTION_OUTCOME_PDF_TEXT:
        return f"{len(passages(evidence.normalized_text))} PDF passage(s) extracted — diffable"
    if evidence.extraction_outcome == EXTRACTION_OUTCOME_PDF_REFUSED:
        return (
            f"⚠ PDF NOT extracted ({evidence.extraction_detail}) — this source can only ever "
            "report that its bytes changed"
        )
    return "binary, no extractor — this source can only ever report that its bytes changed"


def _cmd_sources_rotation(args: argparse.Namespace) -> int:
    """`sources rotation`: the store-backed half of the false-drift signal.

    Not a gate, and not a network call. A source a reviewer keeps dismissing is a question
    for a person — is this page churning, or is it genuinely edited every week? — and this
    command's whole job is to make sure that question gets asked at all rather than dying in
    one reviewer's memory.
    """
    with SnapshotStore(args.db) as store:
        report = rotation_report(store, threshold=args.threshold)
    _print_rotation_report(report)
    print(report.summary())
    return 0  # never a gate: a page that churns is not a broken build


def _print_rotation_report(report: RotationReport) -> None:
    """The suspects, their evidence, and — said in-band every time — what was NOT done.

    The "nothing has been suppressed" line is not reassurance padding. An operator reading
    "possible rotation" about a government page has every reason to wonder whether the tool
    has quietly stopped alerting on it, and if they believed that, this feature would have
    manufactured the exact wrong "no change" it exists to help prevent.
    """
    for suspect in report.suspects:
        days = suspect.span.days
        print(
            f"  ↻ POSSIBLE ROTATION  {suspect.source_id:<28} "
            f"{suspect.streak} consecutive editorial dismissal(s) over {days}d"
        )
        print(f"      {suspect.jurisdiction}/{suspect.document_class}  {suspect.url}")
        print(
            f"      first {suspect.first_dismissed_at.date()}, "
            f"last {suspect.last_dismissed_at.date()}, "
            f"dismissed by: {', '.join(suspect.reviewers)}"
        )
        # One runnable line per record rather than the ids joined together: the reviewer's
        # next action is to read these diffs, and a line they have to edit before it runs is
        # a line they do not run.
        for change_id in suspect.change_ids:
            print(f"      sentinel diff {change_id}")
        if suspect.pending:
            print(
                f"      ({suspect.pending} observation(s) on this source are still "
                "unreviewed and are NOT counted either way)"
            )
        print(
            "      Nothing has been suppressed: this source is still watched and will still\n"
            "      produce a change record next run. Repeated editorial dismissals are what a\n"
            "      page with rotating content looks like from the review queue — and also what\n"
            "      a page that is genuinely edited every week looks like. This tool is not\n"
            "      deciding between them.\n"
            "      Read the diffs above: if the same passage keeps moving, run\n"
            "      `sentinel sources check --twice` on it, then either watch a stable page on\n"
            "      that host or record the source as a GAP. Do not normalize the text away —\n"
            "      a normalizer that hides real text can hide a real change."
        )


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
    # The third form of the same question, asked of the registry's own history. The two checks
    # above hold the registry and the prose to each other at one instant. A changelog is a
    # claim about how the registry got here, and a claim about the past is exactly the kind
    # nothing re-derives: a hand-edited entry, or one left behind by a source that has since
    # been swapped, reads as history forever and is believed precisely because it looks like
    # a record rather than a summary.
    unreconciled = reconcile(load_changelog(), registry)
    if not holes and not drifts and not unreconciled:
        print(
            f"\ncoverage --check-docs: OK — every coverage number in {len(DOC_PATHS)} "
            f"document(s) matches the registry, every unwatched jurisdiction/"
            f"document-class pair is a named gap, and the registry changelog reconciles "
            f"with the registry it describes."
        )
        return 0

    _report_violations(
        "REGISTRY IS NOT HONEST ABOUT ITS OWN HOLES:",
        holes,
        remedy="",
    )
    _report_violations(
        "A DOCUMENT DISAGREES WITH THE REGISTRY:",
        drifts,
        remedy=(
            "Do not 'fix' this by editing the registry to match the prose. Run "
            "`sentinel coverage`, and write down what it actually says."
        ),
    )
    _report_violations(
        "THE REGISTRY CHANGELOG DOES NOT DESCRIBE THIS REGISTRY:",
        unreconciled,
        remedy=(
            "Derive the missing events with `sentinel registry changelog --from <rev> "
            "--append`. Do not hand-edit the log to agree: a changelog is only worth "
            "anything while nothing but a diff has ever written it."
        ),
    )
    return 1


def _report_violations(headline: str, violations: Sequence[str], *, remedy: str) -> None:
    """Print one category of gate failure, or nothing at all when it has none."""
    if not violations:
        return
    print(f"\n{headline}", file=sys.stderr)
    for violation in violations:
        print(f"  ✗ {violation}", file=sys.stderr)
    if remedy:
        print(f"\n{remedy}", file=sys.stderr)


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
        # Computed inside the store's lifetime, printed at the end of the run. The streak is
        # a fact about the review record rather than about this pass, so it is read after
        # every watch and not only when something changed: a source whose last three
        # observations were dismissed as editorial is worth naming on the quiet week too,
        # and a signal that only appears alongside an alarm is a signal nobody sees when the
        # alarm stops being read. It cannot alter detection — `watch()` has already returned.
        rotation = rotation_report(store)

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
    _print_watch_tail(report, rotation)
    return 0


def _print_watch_tail(report: WatchReport, rotation: RotationReport) -> None:
    """What the operator is left holding: this run's queue, and the standing registry doubt.

    The two are printed together on purpose. The queue is what to do now; the rotation block
    is what to stop doing — and a reviewer who has just dismissed the same source for the
    third time is the one person in a position to act on it, at the one moment they are
    looking.
    """
    pending = len(report.changed) + len(report.possibly_removed)
    if pending:
        print(
            f"\n{pending} change(s) recorded as UNCLASSIFIED/UNREVIEWED. "
            f"Nothing reaches the feed until a named human reviews it."
        )
    if rotation.suspects:
        print(
            f"\n{len(rotation.suspects)} source(s) a reviewer keeps dismissing as editorial — "
            "a question about the registry, not a finding about the world:"
        )
        _print_rotation_report(rotation)


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
    print("baseline-check-unbaselined-count: 0")
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
        # Read, and then compared against nothing: there is no committed hash for this source,
        # so the one thing this command does was not done to it. Printed as loudly as the other
        # two not-compared buckets, and for the identical reason — a single quiet `?` line next
        # to a MOVED block reads as housekeeping, when what it actually says is that this run
        # establishes nothing about that page. A first pass over a source cannot find drift.
        print(
            f"  ⊘ NO COMMITTED BASELINE (NOT compared, no drift claimed either way): {source_id}",
            flush=True,
        )
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
    # Sources with no committed hash at all, and the only bucket in this block that is INSIDE
    # the observation numerator below (issue #51). That makes it the one bucket a workflow
    # cannot infer: a blind or unreachable page is at least subtracted from `observed`, so a
    # pass made entirely of them fails the refusal below — but a pass in which every source was
    # read and NOT ONE had a baseline to compare against prints a healthy `observed`, zero
    # drift, and exits 0. Its own machine-readable line for the same reason as the four above,
    # and it must never be added to the MOVED count: these pages were not compared at all.
    print(f"baseline-check-unbaselined-count: {len(report.unbaselined)}")
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
    if report.unbaselined:
        print(
            f"\n{len(report.unbaselined)} source(s) have NO committed baseline. Those pages were\n"
            "fetched and read, and then compared against nothing: there is no hash on file to\n"
            "compare them against. This run says nothing about whether they changed — a first\n"
            "pass over a source cannot find drift, and a zero MOVED count is not evidence that\n"
            "they were quiet. They stay uncompared every week until a baseline is minted:\n"
            "  sentinel watch && sentinel baseline write"
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
