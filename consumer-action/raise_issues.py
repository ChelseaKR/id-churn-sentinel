#!/usr/bin/env python3
"""Open one issue in YOUR repository for each newly confirmed change in your scope.

This is the consumer half of the feed, and it runs in the consumer's repository,
not in this one. It fetches the published `changes.json` from a URL (or reads a
local copy), compares it with a small state file the consumer commits, and opens
or updates one issue per newly confirmed change that falls inside a mapping file
the consumer wrote. Nothing is sent back here: there is no account, no callback,
no telemetry, and no subscriber list — the subscription lives entirely in the
consumer's own repository, which is what makes a notification channel possible
without one.

Zero dependencies, standard library only, matching the posture of the tool it
consumes: a scheduled workflow an organization forgets about for a year should
not carry a dependency tree that rots faster than the law it watches.

**It never interprets a change.** The issue title is the jurisdiction, the
document class and the change id. The body is the published record — excerpt,
hashes, reviewer trail, source verification status — reproduced rather than
summarized. "A machine noticed this, so it must matter" is the claim this whole
project refuses to make, and a consumer-side summarizer would make it on the
project's behalf, in the consumer's own tracker, where it looks like the
consumer said it.

Three refusals worth knowing before you schedule it:

* **An unknown jurisdiction or document class fails the run.** The vocabulary is
  read out of the fetched document's own `sources` inventory, so it cannot drift
  from the feed — and a document that carries no inventory is reported as
  exactly that, rather than as "every jurisdiction you asked for is unknown".
* **A fetch that did not produce a changes document is a failure, not an empty
  feed.** An HTTP error page parsed as content, or a JSON body with no
  `changes` array, would otherwise read as "nothing changed this week", which is
  the one thing silence must never mean here.
* **Gate 6 is re-applied at your edge.** A dismissed record, or a substantive
  one lacking its independent approval, is never surfaced — even if a document
  you point `--changes` at contains one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CANONICAL_CHANGES_URL = "https://chelseakr.github.io/id-churn-sentinel/changes.json"

MAP_SCHEMA_VERSION = "1.0"
STATE_SCHEMA_VERSION = "1.0"

#: Written into every issue body so a human (or a later run of this script) can
#: tie an issue back to the record it reproduces without parsing the title.
MARKER = "sentinel-change-id"

EXIT_OK = 0
#: A configuration, mapping or feed problem. NOTHING was opened or commented.
EXIT_REFUSED = 1
#: The GitHub API refused a write. Some issues may already have been opened.
EXIT_API = 2

PLAN_OPEN = "open"
PLAN_COMMENT = "comment"
PLAN_UNCHANGED = "unchanged"
#: Closed vocabulary. Every in-scope, surfaceable change gets exactly one.
PLAN_ACTIONS = (PLAN_OPEN, PLAN_COMMENT, PLAN_UNCHANGED)

#: Every key a mapping file may carry. Hoisted out of `load_map` so the published
#: schema can be checked against it as a SET — "every key the loader accepts is
#: described, and every key described is accepted" — rather than against a count
#: or a list retyped in a test. A set check survives two changes landing in the
#: same merge; a count does not.
MAP_KEYS = frozenset(
    {"schema_version", "consumer", "jurisdictions", "document_classes", "source_ids", "labels"}
)

#: The published payload carries exactly these three independent-review fields.
#: An `editorial` record must carry none of them; see `is_surfaceable`.
_INDEPENDENT_KEYS = (
    "independent_review_status",
    "independent_reviewer",
    "independent_reviewed_at",
)

#: Lifecycle states that are news about a change already reported, rather than a
#: new change. A record first seen in one of these is recorded and NOT opened —
#: opening an issue whose first sentence is "this was withdrawn" is noise.
_INACTIVE_LIFECYCLE = frozenset({"withdrawn", "superseded", "corrected"})

_TIMEOUT_SECONDS = 30.0


class ActionError(Exception):
    """The run was refused. Nothing was opened."""


Fetcher = Callable[[str], bytes]
GitHubCall = Callable[[str, str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Planned:
    """One decision about one change. `action` is always from `PLAN_ACTIONS`."""

    change_id: str
    action: str
    title: str
    body: str
    reason: str


@dataclass(frozen=True)
class Scope:
    """The consumer's mapping file, validated."""

    jurisdictions: frozenset[str]
    document_classes: frozenset[str]
    source_ids: frozenset[str]
    labels: tuple[str, ...]

    def covers(self, change: Mapping[str, Any]) -> bool:
        """Whether this change is in the consumer's scope.

        A source id names a specific page the consumer cites and always matches.
        Otherwise the jurisdiction must be asked for, and the document class must
        be asked for unless the map named no classes at all (which means "every
        class in these jurisdictions" and has to be written deliberately).
        """
        if str(change.get("source_id", "")) in self.source_ids:
            return True
        if str(change.get("jurisdiction", "")) not in self.jurisdictions:
            return False
        if not self.document_classes:
            return True
        return str(change.get("document_class", "")) in self.document_classes


def is_surfaceable(change: Mapping[str, Any]) -> bool:
    """Gate 6, re-asserted on the wire format.

    `docs/changes.json` can never carry an unpublishable record — `publish()`
    refuses — but `--changes` accepts any conforming file, including one a
    consumer assembled themselves, and an unreviewed hash change surfaced as an
    issue in a legal-aid tracker is precisely the claim this project refuses to
    make. `tests/test_consumer_action.py` pins this against
    `ChangeRecord.publishable` over a matrix, so the two cannot drift.
    """
    if change.get("review_status") != "confirmed":
        return False
    significance = change.get("significance")
    if significance == "editorial":
        return all(not change.get(key) for key in _INDEPENDENT_KEYS)
    if significance == "substantive":
        return change.get("independent_review_status") == "confirmed"
    return False


def _lifecycle_fingerprint(change: Mapping[str, Any]) -> str:
    """What must change for an already-reported change to be worth commenting on."""
    return "|".join(
        str(change.get(key) or "")
        for key in ("publication_status", "superseded_by", "lifecycle_reason", "lifecycle_at")
    )


# ---------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------


def load_map(path: Path) -> Scope:
    """Read and validate the consumer's mapping file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ActionError(f"mapping file not found: {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ActionError(f"mapping file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ActionError(f"mapping file is not a JSON object: {path}")
    if raw.get("schema_version") != MAP_SCHEMA_VERSION:
        raise ActionError(
            f"{path}: schema_version must be {MAP_SCHEMA_VERSION!r}, got "
            f"{raw.get('schema_version')!r}"
        )
    unknown = sorted(set(raw) - MAP_KEYS)
    if unknown:
        raise ActionError(f"{path}: unknown key(s): {', '.join(unknown)}")
    jurisdictions = _string_list(raw.get("jurisdictions"), path, "jurisdictions")
    document_classes = _string_list(raw.get("document_classes"), path, "document_classes")
    source_ids = _string_list(raw.get("source_ids"), path, "source_ids")
    labels = _string_list(raw.get("labels"), path, "labels")
    if not jurisdictions and not source_ids:
        raise ActionError(
            f"{path}: name at least one jurisdiction or source_id. A map with neither is a "
            f"subscription to every change in the country, which has to be written out rather "
            f"than arrived at by omission."
        )
    return Scope(
        jurisdictions=frozenset(jurisdictions),
        document_classes=frozenset(document_classes),
        source_ids=frozenset(source_ids),
        labels=tuple(labels),
    )


def _string_list(value: object, path: Path, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ActionError(f"{path}: {key} must be a list of non-empty strings")
    return [str(item) for item in value]


def fetch_changes(source: str, fetch: Fetcher) -> dict[str, Any]:
    """Read the published changes document from a URL or a local path.

    A body that is not a changes document is an ERROR, never an empty feed. An
    HTTP error page, a redirect to a login screen, or a truncated JSON file all
    parse into "no changes this week" if you let them, and silence is the one
    thing this feed's consumers must not be handed by accident.
    """
    if source.startswith(("http://", "https://")):
        raw = fetch(source)
    else:
        try:
            raw = Path(source).read_bytes()
        except OSError as exc:
            raise ActionError(f"changes document could not be read: {source}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionError(
            f"{source} did not return a changes document (unparseable): {exc}. This is a "
            f"failure, not an empty feed."
        ) from exc
    if not isinstance(document, dict) or not isinstance(document.get("changes"), list):
        raise ActionError(
            f"{source} did not return a changes document (no `changes` array). This is a "
            f"failure, not an empty feed."
        )
    return document


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(  # noqa: S310 — scheme checked by the caller
        url,
        headers={"User-Agent": "id-churn-sentinel-consumer-action", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            if response.status != 200:
                raise ActionError(f"{url} answered HTTP {response.status}")
            body: bytes = response.read()
    except urllib.error.URLError as exc:
        raise ActionError(f"{url} could not be fetched: {exc}") from exc
    return body


def check_vocabulary(scope: Scope, document: Mapping[str, Any]) -> None:
    """Refuse a map naming a jurisdiction or document class the feed does not have.

    The vocabulary is read out of the document's own source inventory rather than
    hard-coded here, so it cannot drift from the feed. A document carrying no
    inventory is reported as *that*, because "every jurisdiction you asked for is
    unknown" would send the reader to fix the wrong file.
    """
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ActionError(
            "the changes document carries no `sources` inventory, so the mapping file cannot "
            "be checked against it. Point --changes at a document from `sentinel publish`."
        )
    known_jurisdictions = {str(item.get("jurisdiction", "")) for item in sources}
    known_classes = {str(item.get("document_class", "")) for item in sources}
    unknown_jurisdictions = sorted(scope.jurisdictions - known_jurisdictions)
    unknown_classes = sorted(scope.document_classes - known_classes)
    problems = []
    if unknown_jurisdictions:
        problems.append(
            f"unknown jurisdiction(s) {unknown_jurisdictions} — the feed carries "
            f"{sorted(known_jurisdictions)}"
        )
    if unknown_classes:
        problems.append(
            f"unknown document class(es) {unknown_classes} — the feed carries "
            f"{sorted(known_classes)}"
        )
    if problems:
        raise ActionError("mapping file does not match the feed: " + "; ".join(problems))


# ---------------------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------------------


def _issue_title(change: Mapping[str, Any]) -> str:
    return (
        f"[{change.get('jurisdiction')}] {change.get('document_class')} changed — "
        f"{change.get('change_id') or change.get('id')}"
    )


def _issue_body(change: Mapping[str, Any], *, feed_url: str) -> str:
    """The published record, reproduced. Nothing here is written by this script."""
    verification = change.get("source_verification") or {}
    lines = [
        f"<!-- {MARKER}: {change.get('id')} -->",
        "A source this repository watches was observed to change, and a named human "
        "confirmed the observation. **This is not a statement about what the law is.**",
        "",
        f"- **source:** {change.get('url')}",
        f"- **source id:** `{change.get('source_id')}`",
        f"- **observed at:** {change.get('observed_at')}",
        f"- **kind:** {change.get('kind')}",
        f"- **significance:** {change.get('significance')} "
        f"(reviewed by {change.get('reviewer')} at {change.get('reviewed_at')})",
    ]
    if change.get("independent_review_status"):
        lines.append(
            f"- **independent approval:** {change.get('independent_review_status')} by "
            f"{change.get('independent_reviewer')} at {change.get('independent_reviewed_at')}"
        )
    lines += [
        f"- **source verification:** {verification.get('status', 'unknown')}"
        + (f" (by {verification['verifier']})" if verification.get("verifier") else ""),
        f"- **previous hash:** `{change.get('previous_hash')}`",
        f"- **new hash:** `{change.get('new_hash')}`",
        f"- **publication status:** {change.get('publication_status')}",
        "",
        "**Reviewer's public note**",
        "",
        "> " + str(change.get("review_note") or "(none)").replace("\n", "\n> "),
        "",
        "**Changed passages, as published**",
        "",
        "```diff",
        str(change.get("diff_excerpt") or ""),
        "```",
        "",
        f"Published record: {feed_url}",
    ]
    return "\n".join(lines)


def _lifecycle_comment(change: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            f"<!-- {MARKER}: {change.get('id')} -->",
            "The published record for this change moved to "
            f"**{change.get('publication_status')}**.",
            "",
            f"- **reason:** {change.get('lifecycle_reason') or '(none published)'}",
            f"- **by:** {change.get('lifecycle_actor') or '(none published)'}",
            f"- **at:** {change.get('lifecycle_at') or '(none published)'}",
            f"- **superseded by:** {change.get('superseded_by') or '(not superseded)'}",
        ]
    )


def plan(
    document: Mapping[str, Any],
    scope: Scope,
    state: Mapping[str, Any],
) -> list[Planned]:
    """Decide, for every in-scope surfaceable change, exactly one action."""
    seen = state.get("changes") or {}
    feed_url = str(document.get("feed_url") or CANONICAL_CHANGES_URL)
    planned: list[Planned] = []
    for change in document["changes"]:
        if not isinstance(change, dict):
            continue
        if not is_surfaceable(change) or not scope.covers(change):
            continue
        change_id = str(change.get("id"))
        fingerprint = _lifecycle_fingerprint(change)
        record = seen.get(change_id)
        if record is None:
            inactive = str(change.get("publication_status")) in _INACTIVE_LIFECYCLE
            planned.append(
                Planned(
                    change_id=change_id,
                    action=PLAN_UNCHANGED if inactive else PLAN_OPEN,
                    title=_issue_title(change),
                    body=_issue_body(change, feed_url=feed_url),
                    reason=(
                        "first seen already in a terminal lifecycle state; recorded, not opened"
                        if inactive
                        else "newly confirmed and in scope"
                    ),
                )
            )
        elif record.get("lifecycle") != fingerprint:
            planned.append(
                Planned(
                    change_id=change_id,
                    action=PLAN_COMMENT,
                    title=_issue_title(change),
                    body=_lifecycle_comment(change),
                    reason="lifecycle moved since the last run",
                )
            )
        else:
            planned.append(
                Planned(
                    change_id=change_id,
                    action=PLAN_UNCHANGED,
                    title=_issue_title(change),
                    body="",
                    reason="already reported and unchanged",
                )
            )
    return planned


def next_state(
    document: Mapping[str, Any], scope: Scope, state: Mapping[str, Any], planned: Sequence[Planned]
) -> dict[str, Any]:
    """The state file to commit: every change this run accounted for."""
    changes: dict[str, Any] = dict(state.get("changes") or {})
    by_id = {
        str(change.get("id")): change for change in document["changes"] if isinstance(change, dict)
    }
    for item in planned:
        change = by_id[item.change_id]
        existing = changes.get(item.change_id) or {}
        changes[item.change_id] = {
            "lifecycle": _lifecycle_fingerprint(change),
            "issue": existing.get("issue"),
        }
    return {"schema_version": STATE_SCHEMA_VERSION, "changes": changes}


def load_state(path: Path) -> dict[str, Any]:
    """Read the committed state file, or start empty. A missing file is a first run."""
    if not path.exists():
        return {"schema_version": STATE_SCHEMA_VERSION, "changes": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ActionError(
            f"state file is unreadable: {path}: {exc}. Refusing to treat it as a first run — "
            f"that would re-open every issue this repository has already filed."
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("changes"), dict):
        raise ActionError(f"state file is not a state document: {path}")
    return raw


# ---------------------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------------------


def _github_call(method: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ActionError("GITHUB_TOKEN is not set; pass `token:` to the action")
    request = urllib.request.Request(  # noqa: S310 — api.github.com, built below
        url,
        method=method,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "id-churn-sentinel-consumer-action",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            body: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never echo the response body: an API error can quote request headers.
        raise ActionError(f"GitHub refused {method} {url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ActionError(f"GitHub could not be reached for {method} {url}: {exc.reason}") from exc
    return body


def apply_plan(
    planned: Sequence[Planned],
    state: dict[str, Any],
    *,
    repository: str,
    scope: Scope,
    github: GitHubCall,
) -> dict[str, int]:
    """Open and comment. Returns the counts this run actually performed."""
    counts = dict.fromkeys(PLAN_ACTIONS, 0)
    api = f"https://api.github.com/repos/{repository}/issues"
    for item in planned:
        counts[item.action] += 1
        record = state["changes"].setdefault(item.change_id, {})
        if item.action == PLAN_OPEN:
            payload: dict[str, Any] = {"title": item.title, "body": item.body}
            if scope.labels:
                payload["labels"] = list(scope.labels)
            created = github("POST", api, payload)
            record["issue"] = created.get("number")
        elif item.action == PLAN_COMMENT:
            number = record.get("issue")
            if number is None:
                # The lifecycle moved on a change whose issue this repository
                # never recorded. Say so instead of opening a second issue whose
                # first sentence is about a withdrawal.
                record["issue"] = None
                continue
            github("POST", f"{api}/{number}/comments", {"body": item.body})
    return counts


# ---------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="raise_issues.py",
        description=(
            "Open one issue per newly confirmed change to a source your organization "
            "cares about. Reads a public file; sends nothing anywhere."
        ),
    )
    parser.add_argument("--map", required=True, type=Path, help="your mapping file")
    parser.add_argument(
        "--changes",
        default=CANONICAL_CHANGES_URL,
        help="published changes.json URL, or a local path (offline testing)",
    )
    parser.add_argument("--state", required=True, type=Path, help="state file to read and rewrite")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and write no state; opens and comments on nothing",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetcher | None = None,
    github: GitHubCall | None = None,
    out: Any = None,
) -> int:
    """Run one pass. Returns 0 success, 1 refused (nothing written), 2 API failure."""
    args = build_parser().parse_args(argv)
    stream = out if out is not None else sys.stdout
    try:
        scope = load_map(args.map)
        document = fetch_changes(args.changes, fetch or _http_get)
        check_vocabulary(scope, document)
        state = load_state(args.state)
        planned = plan(document, scope, state)
    except ActionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    for item in planned:
        print(f"{item.action:<9} {item.change_id}  {item.title}  ({item.reason})", file=stream)
    if args.dry_run:
        counts = {action: sum(1 for i in planned if i.action == action) for action in PLAN_ACTIONS}
        print(f"dry run: {counts} — nothing was opened and no state was written", file=stream)
        return EXIT_OK
    if not args.repository:
        print("error: --repository (or $GITHUB_REPOSITORY) is required", file=sys.stderr)
        return EXIT_REFUSED

    updated = next_state(document, scope, state, planned)
    try:
        counts = apply_plan(
            planned,
            updated,
            repository=args.repository,
            scope=scope,
            github=github or _github_call,
        )
    except ActionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        # Persist what did land: an aborted run that forgot the issues it just
        # opened would open them all again on the next schedule.
        args.state.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return EXIT_API
    args.state.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"consumer action: {counts}", file=stream)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover — exercised as a subprocess in the tests
    sys.exit(run())
