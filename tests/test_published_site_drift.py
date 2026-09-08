"""MERGE-BLOCKING GATE: the committed site is what this code produces from this registry.

`make sources-validate` runs exactly this file, alongside the registry and coverage-drift
checks, because "is the registry well-formed?", "does the prose tell the truth about the
registry?" and "do the *published bytes* still say what the registry says?" are the same
question asked of the same file.

**Why it has to exist.** GitHub Pages serves `docs/` off the branch. There is no build step,
no deploy job, and no CI stage between the commit and the consumer — that is deliberate (see
`core/publish.py` and `.github/workflows/ci.yml`: an Actions-driven Pages deploy would never
run under this account's spending limit). The consequence is that **the committed bytes are
the product**, and every guarantee this repository makes about them has to be checked on the
bytes in the commit rather than on freshly generated ones.

`tests/test_feed_integrity.py` and `tests/test_source_labelling.py` already do that for the
two safety properties: no unreviewed record is served, no source is served without its
verification status. Both hold on the committed bytes today. Neither one can notice that the
committed bytes are simply **stale** — that a source was added to `sources/registry.json`, or
a renderer changed, and `make publish` was never run. Every assertion in those files would
still pass over a `sources.json` describing last month's registry, because everything they
check is a property of the artifact rather than a relationship between the artifact and its
input. A stale inventory is not a malformed one.

So this file asks the one question nothing else asks: regenerate the whole published surface
from the committed registry, into a temporary directory, and compare it byte for byte with
what is committed. Any difference is either a registry edit nobody republished or a hand-edit
of a generated file, and both reach a consumer exactly as written.

**Nothing here writes into the working tree.** `publish()` is called with a `tmp_path`
destination. A gate that regenerates in place would repair the drift it exists to find.

**What is pinned, and what is excluded.**

* The clock is pinned to the `generated_at` the committed artifacts carry, and pinning it is
  what makes the comparison possible at all: `publish` stamps `generated_at` and derives its
  eligibility policy date from it, and it deliberately refuses an operator-supplied `--as-of`
  (`tests/test_cli.py::test_operational_commands_reject_an_operator_selected_policy_date`), so
  the published bytes change every day on their own. Reading the clock back out of the
  artifact under test neutralizes exactly that and nothing else: the question becomes "given
  the same instant, does today's code and today's registry still produce these exact bytes?"

* `status.json`, and the one `run-health` section of `index.html` rendered from it, are
  excluded from the byte comparison and are the only exclusions. They are watch-run health,
  read from the SQLite snapshot store in `var/`, which is not committed (it holds megabytes of
  retained government HTML). A clean checkout has no memory of the run they record and cannot
  reproduce them. They are not left unchecked: `test_the_excluded_run_health_section_agrees_
  with_the_committed_status_json` cross-checks the two committed artifacts against each other,
  so a hand-edit of either one still has to survive the other.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from id_churn_sentinel.core.baseline import default_baseline_path
from id_churn_sentinel.core.coverage import repo_root
from id_churn_sentinel.core.jurisdiction_status import (
    COVERAGE_STORE_UNAVAILABLE,
    statement_for,
)
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import load_registry
from id_churn_sentinel.core.site import PAGES_URL

#: The published site as committed. Not a build directory: these are the exact bytes Pages
#: serves, with nothing in between.
PUBLISHED = repo_root() / "docs"

#: The two artifacts that are watch-run health rather than registry projection. See the
#: module docstring: the store they come from is not committed.
STORE_DERIVED = ("status.json",)

#: The per-jurisdiction watch receipts (issue #76) are the same kind of thing, one file per
#: jurisdiction. Matched by prefix rather than listed, because the list is the registry's
#: jurisdictions and would go stale the first time one is added -- and it would go stale by
#: silently pulling a file INTO a byte comparison it cannot pass, which reads as drift.
STORE_DERIVED_PREFIX = "status-us"


def _is_store_derived(name: str) -> bool:
    return name in STORE_DERIVED or name.startswith(STORE_DERIVED_PREFIX)


#: The `index.html` block rendered from that same run health, delimited exactly as
#: `core/site.py` emits it.
RUN_HEALTH_OPEN = '<section class="notice" aria-labelledby="run-health">'
RUN_HEALTH_CLOSE = "</section>"


def _committed_generated_at() -> datetime:
    """The instant the committed artifacts were stamped with, read back out of them."""

    payload = json.loads((PUBLISHED / "sources.json").read_text(encoding="utf-8"))
    return datetime.fromisoformat(payload["generated_at"])


def _served_change_count() -> int:
    """How many reviewed change records the committed JSON feeds actually serve."""

    return len(json.loads((PUBLISHED / "changes.json").read_text(encoding="utf-8"))["changes"])


def _regenerate(destination: Path) -> list[str]:
    """Rebuild the whole published surface into `destination`; return the filenames written.

    The record list is empty, and `test_the_gate_is_comparing_the_records_the_commit_serves`
    is what keeps that honest rather than convenient: a reviewed record lives only in the
    uncommitted store, so if the commit ever serves one, this gate has to be taught to
    reconstruct it instead of quietly comparing an empty feed against a full one.
    """

    publish(
        [],
        destination,
        registry=load_registry(),
        feed_url=PAGES_URL,
        now=_committed_generated_at(),
    )
    return sorted(path.name for path in destination.iterdir())


def _without_run_health(html: str) -> str:
    """`index.html` with the store-derived run-health section replaced by a marker."""

    start = html.index(RUN_HEALTH_OPEN)
    end = html.index(RUN_HEALTH_CLOSE, start) + len(RUN_HEALTH_CLOSE)
    return (
        html[:start] + "<!-- run health: excluded, see test_published_site_drift -->" + html[end:]
    )


def test_the_gate_is_comparing_the_records_the_commit_serves() -> None:
    """The one assumption the byte comparison rests on, asserted instead of assumed.

    Regeneration publishes no change records, because a reviewed record exists only in the
    uncommitted snapshot store and no published artifact carries enough of it to rebuild one
    (there is no `ChangeRecord.from_dict`). That is a faithful reproduction *only while the
    commit serves none either* — which is the correct state today: 0 of the registry's sources
    are human-verified, so nothing is eligible to be watched, reviewed, or published.

    The day that changes, this fails, loudly, with the remedy in the message. It does not
    quietly narrow itself to the artifacts that still happen to match, because a gate that
    stops comparing the feed on the day the feed first has something in it is not a gate.
    """
    served = _served_change_count()
    assert served == 0, (
        f"the committed feeds now serve {served} reviewed change record(s), which this gate "
        f"cannot rebuild: a record lives only in the uncommitted store in var/. Teach "
        f"`_regenerate` to load the reviewed records (e.g. from a store passed in by the "
        f"operator) before trusting the byte comparisons below — do not delete or weaken "
        f"them, and do not skip them."
    )


def test_every_registry_derived_artifact_regenerates_byte_for_byte(tmp_path: Path) -> None:
    """THE GATE. Every published file, rebuilt from the committed registry, byte-compared.

    A failure here means one of two things, and both are served to consumers as written:
    `sources/registry.json` (or a renderer) changed and `make publish` was never run, or a
    generated file under `docs/` was hand-edited.
    """
    written = _regenerate(tmp_path)
    compared = [name for name in written if not _is_store_derived(name) and name != "index.html"]
    # 52 jurisdictions x (feed + changes), plus feed.xml, changes.json, sources.json and
    # .nojekyll. Asserted as a floor so the loop below can never be vacuous.
    assert len(compared) > 100, (
        f"only {len(compared)} artifact(s) regenerated — publish() wrote too little"
    )

    drifted: list[str] = []
    for name in compared:
        committed = PUBLISHED / name
        assert committed.exists(), f"docs/{name} is published by `publish()` but is not committed"
        if committed.read_bytes() != (tmp_path / name).read_bytes():
            drifted.append(name)

    assert not drifted, (
        f"{len(drifted)} committed artifact(s) are not what `sentinel publish` now produces "
        f"from the committed registry: {drifted}. These are the exact bytes GitHub Pages "
        f"serves off the branch. Run `make publish` and commit the result; never hand-edit a "
        f"generated file under docs/."
    )


def test_the_front_page_regenerates_byte_for_byte_outside_the_run_health_section(
    tmp_path: Path,
) -> None:
    """`index.html` is the human-readable front door and it is generated too.

    Everything except the store-derived run-health section is compared in full — the coverage
    table, every source row, every gap, every verification label.
    """
    _regenerate(tmp_path)
    committed = (PUBLISHED / "index.html").read_text(encoding="utf-8")
    regenerated = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert RUN_HEALTH_OPEN in committed, "the run-health section markers moved; re-scope this gate"
    assert _without_run_health(committed) == _without_run_health(regenerated), (
        "docs/index.html is not what `sentinel publish` now renders from the committed "
        "registry. Run `make publish` and commit the result."
    )


def test_the_excluded_run_health_section_agrees_with_the_committed_status_json() -> None:
    """The one excluded region, checked against the other committed artifact instead.

    `status.json` and the run-health section of `index.html` are both rendered from the same
    `PublicRunStatus` in a single `publish()` call, so they cannot legitimately disagree. Since
    neither can be regenerated from committed inputs, this is what stands in for the byte
    comparison: a hand-edit of one still has to survive the other.
    """
    status = json.loads((PUBLISHED / "status.json").read_text(encoding="utf-8"))
    html = (PUBLISHED / "index.html").read_text(encoding="utf-8")
    start = html.index(RUN_HEALTH_OPEN)
    section = html[start : html.index(RUN_HEALTH_CLOSE, start)]

    assert f"Run health: {status['state'].upper()}" in section, (
        f"docs/index.html reports a different run state than docs/status.json "
        f"({status['state']!r}); one of the two was hand-edited or published separately"
    )
    attempted = status["last_attempted_run"]
    if attempted is None:
        assert "No watch run receipt exists." in section
    else:
        assert attempted["run_id"] in section, (
            "docs/index.html names a different latest attempt than docs/status.json"
        )


def test_the_committed_jurisdiction_receipts_agree_with_the_committed_status_json() -> None:
    """The other excluded artifacts, checked against the same two committed documents.

    `status-us-xx.json` is store-derived, so it is out of the byte comparison for the reason
    `status.json` is. That exclusion has to buy something back, so: every receipt covers
    exactly the registry's sources for its jurisdiction, and any receipt that claims to have
    read the store has to name the same latest run `status.json` does. A receipt regenerated
    on a different day, from a different store, or by hand fails one of the two.

    The `store_unavailable` case carries no run and is exempt from the second half by
    construction — that is what the word means, and it is why it exists as a separate value
    rather than being folded into `never_covered`.
    """
    registry = load_registry()
    status = json.loads((PUBLISHED / "status.json").read_text(encoding="utf-8"))
    attempted = status["last_attempted_run"]

    receipts = sorted(PUBLISHED.glob("status-us*.json"))
    assert len(receipts) == len(registry.jurisdictions), (
        f"{len(receipts)} committed receipt(s) for {len(registry.jurisdictions)} "
        f"jurisdiction(s). Run `make publish` and commit the result."
    )

    for path in receipts:
        document = json.loads(path.read_text(encoding="utf-8"))
        jurisdiction = document["jurisdiction"]
        expected = sorted(
            source.id for source in registry.sources if source.jurisdiction == jurisdiction
        )
        assert [entry["source_id"] for entry in document["sources"]] == expected, (
            f"{path.name} does not list the registry's sources for {jurisdiction}"
        )
        assert sum(document["counts"].values()) == len(expected)
        if document["coverage"] == COVERAGE_STORE_UNAVAILABLE:
            assert document["run"] is None and document["latest_run"] is None
            continue
        assert attempted is not None, (
            f"{path.name} reports a run, but docs/status.json records none; one of the two "
            f"was published separately"
        )
        assert document["latest_run"]["run_id"] == attempted["run_id"], (
            f"{path.name} names a different latest run than docs/status.json"
        )


def test_every_committed_receipts_sentence_re_derives_from_its_own_published_fields() -> None:
    """The prose on the excluded artifacts, held to the data printed beside it.

    The receipts are out of the byte comparison because a clean checkout has no store to
    rebuild them from. Their **sentence** needs no store: `statement_for` takes only fields
    the document itself publishes -- `jurisdiction`, `coverage`, `run.run_id`, `run.state`
    and each source's `outcome` -- so it can be recomputed here and compared exactly.

    That closes the half of the exclusion nothing else reaches. The sibling check above
    proves a receipt lists the right sources and names the right run; it says nothing about
    the one field a human actually reads. A statement carried forward from an older publish,
    hand-edited, or left behind by a renderer change fails here and nowhere else.

    It is also the check that would have caught the defect this test arrived with: issue #99
    found the sentence claiming a *comparison* over a count of *readings*, and the wrong verb
    was published on all 52 receipts with nothing holding the prose to anything.
    """
    receipts = sorted(PUBLISHED.glob("status-us*.json"))
    assert len(receipts) > 50, (
        f"only {len(receipts)} receipt(s) found -- this check stopped finding its subject"
    )

    wrong: list[str] = []
    for path in receipts:
        document = json.loads(path.read_text(encoding="utf-8"))
        run = document["run"]
        expected = statement_for(
            jurisdiction=document["jurisdiction"],
            coverage=document["coverage"],
            run_id=None if run is None else run["run_id"],
            run_state=None if run is None else run["state"],
            outcomes=[entry["outcome"] for entry in document["sources"]],
        )
        if document["statement"] != expected:
            wrong.append(
                f"{path.name}\n  committed: {document['statement']}\n  derives to: {expected}"
            )

    assert not wrong, (
        f"{len(wrong)} committed receipt(s) carry a sentence their own published fields do "
        f"not produce. Run `make publish` against the operator's store and commit the "
        f"result; never hand-edit a generated file under docs/.\n" + "\n".join(wrong)
    )


def test_no_orphan_published_artifact_survives_in_the_commit(tmp_path: Path) -> None:
    """A feed that outlived its jurisdiction is still served, and still looks current.

    Drop a jurisdiction from the registry and `publish()` simply stops writing its feed; the
    committed `feed-us-xx.xml` and `changes-us-xx.json` stay in `docs/`, stay served, and stay
    frozen at whatever they last said — with a `generated_at` that makes them look maintained.
    Nothing but this notices, because every other check reads the files that exist rather than
    asking which ones should.
    """
    written = set(_regenerate(tmp_path))
    committed_feeds = {
        path.name
        for path in PUBLISHED.iterdir()
        if path.is_file()
        and (path.name.startswith(("feed", "changes", "sources.")) or path.name == "status.json")
    }

    orphans = sorted(committed_feeds - written)
    assert not orphans, (
        f"docs/ still serves {len(orphans)} artifact(s) that `sentinel publish` no longer "
        f"writes: {orphans}. A consumer subscribed to one receives a frozen feed that reports "
        f"itself as freshly generated. Delete them in the same commit that removes their "
        f"jurisdiction from the registry."
    )


def test_the_committed_baseline_names_only_live_registry_sources() -> None:
    """`sources/baseline-hashes.json` is checked against the registry it claims to baseline.

    The hashes themselves are observations of live pages and cannot be re-derived offline, so
    they are not byte-comparable here. What is checkable from committed inputs alone is that
    the file has not outlived its subject: a source removed from the registry leaves an entry
    behind, and `sentinel baseline check` on a clean clone would go on fetching that URL and
    reporting drift for a page this project no longer claims to watch.
    """
    payload = json.loads(default_baseline_path().read_text(encoding="utf-8"))
    registered = {source.id for source in load_registry().sources}

    named = set(payload["baselines"]) | set(payload["unreachable"])
    assert named, "the committed baseline names no sources at all — it cannot check anything"

    stale = sorted(named - registered)
    assert not stale, (
        f"sources/baseline-hashes.json still names {len(stale)} source(s) that are no longer "
        f"in sources/registry.json: {stale}. Re-run `sentinel watch && sentinel baseline "
        f"write`; never hand-edit a hash."
    )
