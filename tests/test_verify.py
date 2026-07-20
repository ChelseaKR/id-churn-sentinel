"""Tests for `sentinel verify` — the human verification queue.

The properties under test are not "does the prompt loop work". They are:

* **A verification cannot be recorded without a name.** Not in the interactive path, not in the
  scriptable one, not by hand-editing the registry — an entry claiming `verified: true` with no
  named verifier and no date does not even *load*. This is the same discipline as
  `no-auto-classification`, aimed at the other human judgment in this repo: `review` is a
  judgment about a *change*; `verify` is a judgment about a *source*. Neither is a machine's.
* **The tool never answers the question itself.** No scoring, no "looks right", no
  auto-confirm-when-the-title-matches. It fetches the page, shows the human what the page says
  about itself, and writes down what the human decided.
* **The work is resumable**, because 152 sources is several sittings and a tool that loses your
  place is a tool that does not get used.

Offline, like everything else here: the fetcher and the prompt are both injected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.normalize import excerpt, page_title
from id_churn_sentinel.core.registry import REJECTED, VERIFIED, load_registry
from id_churn_sentinel.core.verify import Candidate, confirm, pending, reject, review_card
from id_churn_sentinel.errors import RegistryError, VerificationError

from .conftest import StubFetcher

PAGE = b"""<!doctype html><html><head><title>Office of Vital Statistics | Kansas</title></head>
<body><h1>Office of Vital Statistics</h1>
<p>Order a certified copy of a Kansas birth certificate.</p>
<p>To amend the sex designation on a birth certificate, submit form VS-xx.</p>
</body></html>"""

SEED = {
    "registry_version": "1.0",
    "sources": [
        {
            "id": "ks-kdhe-vital-statistics",
            "jurisdiction": "KS",
            "document_class": "birth_certificate",
            "url": "https://www.kdhe.ks.gov/1165/Office-of-Vital-Statistics",
            "authority": "Kansas Department of Health and Environment",
            "verified": False,
            "checked": {"at": "2026-07-13", "status": 200, "reachable": True},
            "notes": "live-fetched.",
        },
        {
            "id": "us-passport-sex-markers",
            "jurisdiction": "US",
            "document_class": "passport",
            "url": "https://travel.state.gov/passports/sex-markers.html",
            "authority": "U.S. Department of State",
            "verified": False,
            "notes": "corpus-vetted.",
        },
    ],
}


@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(SEED, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def fetcher_for(registry_file: Path) -> StubFetcher:
    return StubFetcher(
        {source["url"]: (PAGE, "text/html") for source in SEED["sources"]}  # type: ignore[index]
    )


class Answers:
    """A scripted human. `input()`, injected."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "q"


# ---- the rule that matters ----------------------------------------------------------------


def test_a_verification_without_a_name_is_refused(registry_file: Path) -> None:
    """THE RULE. `verified: true` means "a person opened this URL and confirmed it is the
    official page". With no name, that sentence has no subject — and it is worse than
    `false`, because it will be believed."""
    with pytest.raises(VerificationError, match="requires the name of the human"):
        confirm(registry_file, "ks-kdhe-vital-statistics", verifier="   ")

    assert load_registry(registry_file).verified_sources == ()


def test_a_rejection_without_a_name_or_a_reason_is_refused(registry_file: Path) -> None:
    with pytest.raises(VerificationError, match="requires the name of the human"):
        reject(registry_file, "ks-kdhe-vital-statistics", verifier="", reason="wrong page")
    with pytest.raises(VerificationError, match="requires a reason"):
        reject(registry_file, "ks-kdhe-vital-statistics", verifier="A Human", reason="  ")


def test_a_hand_edited_verified_true_with_nobody_behind_it_does_not_load(
    registry_file: Path,
) -> None:
    """The way this project would actually become dishonest is not a lie. It is a bulk edit
    that makes the file *look finished* — by a maintainer in a hurry, a `sed`, or an agent
    asked to tidy up. So the registry refuses to load it at all."""
    raw = json.loads(registry_file.read_text())
    raw["sources"][0]["verified"] = True
    registry_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RegistryError, match="not a verification"):
        load_registry(registry_file)


def test_a_verification_block_with_no_date_does_not_load(registry_file: Path) -> None:
    """A verification with no date can never go stale, which means it can never be re-checked
    — and government URLs move."""
    raw = json.loads(registry_file.read_text())
    raw["sources"][0]["verified"] = True
    raw["sources"][0]["verification"] = {"status": "verified", "verifier": "A Human"}
    registry_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RegistryError, match="must be an ISO date"):
        load_registry(registry_file)


def test_the_verification_status_vocabulary_is_closed(registry_file: Path) -> None:
    """ "probably", "looks right", "seems fine" are not verification statuses. There are three,
    they are a closed set, and a free-text one would let this field decay into a shrug."""
    raw = json.loads(registry_file.read_text())
    raw["sources"][0]["verification"] = {
        "status": "looks-right",
        "verifier": "A",
        "at": "2026-07-14",
    }
    registry_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RegistryError, match="is not one of"):
        load_registry(registry_file)


def test_a_rejection_with_nobody_named_does_not_load_either(registry_file: Path) -> None:
    """A rejection is a human judgment too — it removes a source from the feed's coverage, and
    a coverage decision nobody signed is the same defect from the other direction."""
    raw = json.loads(registry_file.read_text())
    raw["sources"][0]["verification"] = {"status": "rejected", "verifier": "", "at": "2026-07-14"}
    registry_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RegistryError, match="no `verifier` is named"):
        load_registry(registry_file)


def test_a_verification_block_that_is_not_an_object_does_not_load(registry_file: Path) -> None:
    raw = json.loads(registry_file.read_text())
    raw["sources"][0]["verification"] = "yes, Chelsea said so"
    registry_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RegistryError, match="must be an object"):
        load_registry(registry_file)


def test_the_boolean_and_the_block_may_never_disagree(registry_file: Path) -> None:
    raw = json.loads(registry_file.read_text())
    raw["sources"][0]["verified"] = False
    raw["sources"][0]["verification"] = {
        "status": "verified",
        "verifier": "A Human",
        "at": "2026-07-14",
    }
    registry_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RegistryError, match="disagrees with"):
        load_registry(registry_file)


# ---- recording a decision ------------------------------------------------------------------


def test_confirming_records_the_name_and_the_date(registry_file: Path) -> None:
    recorded = confirm(
        registry_file, "ks-kdhe-vital-statistics", verifier="Chelsea Kelly-Reif", at="2026-07-14"
    )

    assert recorded.status == VERIFIED
    entry = json.loads(registry_file.read_text())["sources"][0]
    assert entry["verified"] is True
    assert entry["verification"] == {
        "status": "verified",
        "verifier": "Chelsea Kelly-Reif",
        "at": "2026-07-14",
    }

    reloaded = load_registry(registry_file)
    source = reloaded.by_id("ks-kdhe-vital-statistics")
    assert source.verification_status == VERIFIED
    assert source.verification.label == "VERIFIED — confirmed by Chelsea Kelly-Reif on 2026-07-14"


def test_rejecting_keeps_the_entry_flagged_for_repair_by_default(registry_file: Path) -> None:
    """A wrong entry that is KNOWN to be wrong is safer than one quietly deleted: the deletion
    takes the finding with it, and a consumer who picked the URL up last week is never told."""
    reject(
        registry_file,
        "ks-kdhe-vital-statistics",
        verifier="Chelsea Kelly-Reif",
        reason="This is the county page, not the state's.",
        at="2026-07-14",
    )

    source = load_registry(registry_file).by_id("ks-kdhe-vital-statistics")
    assert source.verification_status == REJECTED
    assert source.verified is False
    assert "NOT the official page" in source.verification.statement
    assert "county page" in source.verification.statement


def test_rejecting_with_gap_moves_it_out_of_the_registry_and_names_the_hole(
    registry_file: Path,
) -> None:
    """When there is no right page to substitute, the honest record is a NAMED GAP — the same
    structure that already carries "their robots.txt forbids us". `wrong-page` is the only
    reason in that closed vocabulary a machine cannot produce."""
    reject(
        registry_file,
        "ks-kdhe-vital-statistics",
        verifier="Chelsea Kelly-Reif",
        reason="Kansas publishes no statewide page for this.",
        at="2026-07-14",
        to_gap=True,
    )

    registry = load_registry(registry_file)
    assert "ks-kdhe-vital-statistics" not in {s.id for s in registry.sources}
    gap = next(g for g in registry.gaps if g.jurisdiction == "KS")
    assert gap.reason == "wrong-page"
    assert gap.document_class == "birth_certificate"
    assert "Chelsea Kelly-Reif" in gap.detail
    assert gap.hosts == ("www.kdhe.ks.gov",)


def test_a_gap_is_refused_when_the_pair_is_still_watched(registry_file: Path) -> None:
    """A gap that claims we are blind to something we can see is a FALSE CONFESSION — and the
    completeness gate would (correctly) fail the build for it."""
    raw = json.loads(registry_file.read_text())
    twin = dict(raw["sources"][0])
    twin["id"] = "ks-kdhe-second-surface"
    twin["url"] = "https://www.kdhe.ks.gov/other"
    raw["sources"].append(twin)
    registry_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(VerificationError, match="false confession"):
        reject(
            registry_file,
            "ks-kdhe-vital-statistics",
            verifier="A Human",
            reason="wrong page",
            to_gap=True,
        )


def test_an_unknown_source_id_is_an_error_not_a_silent_no_op(registry_file: Path) -> None:
    with pytest.raises(RegistryError, match="unknown source id"):
        confirm(registry_file, "no-such-source", verifier="A Human")


# ---- the queue ------------------------------------------------------------------------------


def test_the_queue_can_be_prioritised_and_is_resumable(registry_file: Path) -> None:
    registry = load_registry(registry_file)

    assert next(iter(pending(registry, federal_first=True))).id == "us-passport-sex-markers"
    assert [s.id for s in pending(registry, jurisdiction="KS")] == ["ks-kdhe-vital-statistics"]
    assert [s.id for s in pending(registry, document_class="passport")] == [
        "us-passport-sex-markers"
    ]
    assert len(pending(registry, limit=1)) == 1

    # ...and a decided source leaves the queue, which is the whole of "resumable": the state
    # lives in the committed registry, not in a lockfile somebody has to remember to clean up.
    confirm(registry_file, "us-passport-sex-markers", verifier="A Human", at="2026-07-14")
    assert [s.id for s in pending(load_registry(registry_file))] == ["ks-kdhe-vital-statistics"]


# ---- what the human is shown -----------------------------------------------------------------


def test_the_review_card_shows_the_pages_own_title_and_text(registry_file: Path) -> None:
    """The card exists so the easy ones take ten seconds. It shows what the PAGE says about
    itself — the title and its own opening text — because that is the evidence, and because a
    title reading "404 Page Not Found" or "Request Access" (both real, both served with HTTP
    200) is the trap only a human catches."""
    registry = load_registry(registry_file)
    source = registry.by_id("ks-kdhe-vital-statistics")
    fetcher = StubFetcher({source.url: (PAGE, "text/html")})

    card = review_card(Candidate.of(source, fetcher.fetch(source.url)), position=1, total=2)

    assert "KS · birth_certificate" in card
    assert source.url in card
    assert "Kansas Department of Health and Environment" in card
    assert "PAGE TITLE:  Office of Vital Statistics | Kansas" in card
    assert "order a certified copy of a kansas birth certificate" in card
    assert "official page for this document class" in card
    assert "You are NOT judging what the law says" in card


def test_an_unfetchable_source_says_so_rather_than_showing_an_empty_card(
    registry_file: Path,
) -> None:
    """`ssa.gov` 403s every client we own, and its URL is still correct. The card must not
    show a blank page and let a tired verifier infer the page is empty."""
    source = load_registry(registry_file).by_id("us-passport-sex-markers")

    card = review_card(Candidate.of(source, StubFetcher().fetch(source.url)), position=1, total=1)

    assert "FETCH FAILED" in card
    assert "does NOT mean the URL is wrong" in card
    assert "Open it in a browser before you answer" in card


def test_page_title_reads_the_page_rather_than_hoping(registry_file: Path) -> None:
    assert page_title(PAGE) == "Office of Vital Statistics | Kansas"
    assert page_title(b"<html><body>no title</body></html>") == ""
    # The trap this exists for: a status-code check blesses both of these.
    assert page_title(b"<title>404 Page Not Found</title>") == "404 Page Not Found"
    assert page_title(b"<title>Request Access</title>") == "Request Access"


def test_the_excerpt_is_bounded(registry_file: Path) -> None:
    """An excerpt that fills a terminal is one a tired reviewer scrolls past, and a reviewer
    who scrolls past the evidence is rubber-stamping."""
    long = "\n".join(f"passage {n} " + "x" * 200 for n in range(50))

    out = excerpt(long, max_passages=3, max_chars=100)

    assert len(out.split("\n")) <= 3
    assert len(out) <= 110


# ---- the CLI --------------------------------------------------------------------------------


def test_the_interactive_loop_records_confirm_reject_and_skip(
    registry_file: Path, fetcher_for: StubFetcher, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = Answers(
        "y",  # KS: yes, it is the official page
        "n",  # US: no, it is not
        "It is the wrong department.",  # why
        "n",  # ...and do not move it to the gap list
    )

    code = main(
        ["--registry", str(registry_file), "verify", "--verifier", "Chelsea Kelly-Reif"],
        fetcher=fetcher_for,
        ask=answers,
    )

    assert code == 0
    registry = load_registry(registry_file)
    assert registry.by_id("ks-kdhe-vital-statistics").verification_status == VERIFIED
    assert registry.by_id("us-passport-sex-markers").verification_status == REJECTED
    out = capsys.readouterr().out
    assert "VERIFIED — confirmed by Chelsea Kelly-Reif" in out
    assert "0 still unverified" in out


def test_the_loop_asks_for_a_name_when_none_was_given_and_refuses_an_empty_one(
    registry_file: Path, fetcher_for: StubFetcher, capsys: pytest.CaptureFixture[str]
) -> None:
    """The name is not a formality, so an empty answer is not a shortcut: the decision is
    refused, the source stays in the queue, and the tool says why."""
    answers = Answers("y", "", "q")  # confirm → asked for a name → give none

    assert main(["--registry", str(registry_file), "verify"], fetcher=fetcher_for, ask=answers) == 0

    assert load_registry(registry_file).verified_sources == ()
    assert "REFUSED" in capsys.readouterr().out
    assert any("your name" in prompt for prompt in answers.prompts)


def test_quitting_keeps_what_was_already_decided(
    registry_file: Path, fetcher_for: StubFetcher
) -> None:
    """Resumability, at the only moment it matters: the verifier stops after 90 of 152."""
    answers = Answers("y", "q")

    main(
        ["--registry", str(registry_file), "verify", "--verifier", "A Human"],
        fetcher=fetcher_for,
        ask=answers,
    )

    registry = load_registry(registry_file)
    assert len(registry.verified_sources) == 1
    assert len(registry.unverified) == 1


def test_the_scriptable_path_records_one_decision(
    registry_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--registry",
            str(registry_file),
            "verify",
            "--source-id",
            "ks-kdhe-vital-statistics",
            "--confirm",
            "--verifier",
            "Chelsea Kelly-Reif",
        ]
    )

    assert code == 0
    assert load_registry(registry_file).by_id("ks-kdhe-vital-statistics").verified is True
    assert "VERIFIED — confirmed by Chelsea Kelly-Reif" in capsys.readouterr().out


def test_the_scriptable_path_refuses_a_confirmation_with_no_verifier(
    registry_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--registry",
            str(registry_file),
            "verify",
            "--source-id",
            "ks-kdhe-vital-statistics",
            "--confirm",
        ]
    )

    assert code == 1
    assert "requires the name of the human" in capsys.readouterr().err
    assert load_registry(registry_file).verified_sources == ()


def test_the_scriptable_path_refuses_a_decision_it_was_not_given(
    registry_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--source-id` with neither `--confirm` nor `--reject` is not a request for the tool's
    opinion, because it does not have one."""
    code = main(
        ["--registry", str(registry_file), "verify", "--source-id", "ks-kdhe-vital-statistics"]
    )

    assert code == 1
    assert "does not have one of its own" in capsys.readouterr().err


def test_list_prints_the_queue_and_opens_no_sockets(
    registry_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No fetcher is passed. If `--list` touched the network this would raise, which is the
    point: a volunteer sizing up the job should not cost a government server a single byte."""
    assert main(["--registry", str(registry_file), "verify", "--list", "--federal-first"]) == 0

    out = capsys.readouterr().out
    assert "2 source(s) pending human verification" in out
    assert out.index("us-passport-sex-markers") < out.index("ks-kdhe-vital-statistics")


def test_verify_reports_when_the_selection_is_already_done(
    registry_file: Path, fetcher_for: StubFetcher, capsys: pytest.CaptureFixture[str]
) -> None:
    confirm(registry_file, "us-passport-sex-markers", verifier="A Human", at="2026-07-14")

    assert (
        main(
            ["--registry", str(registry_file), "verify", "--jurisdiction", "US"],
            fetcher=fetcher_for,
            ask=Answers(),
        )
        == 0
    )

    assert "nothing pending" in capsys.readouterr().out


def test_a_rejection_the_tool_refuses_leaves_the_source_in_the_queue(
    registry_file: Path, fetcher_for: StubFetcher, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal is not a silent no-op: the source stays pending, and the human is told why.
    Here the verifier gives no reason — so there is nothing for the next person to act on, and
    the tool will not pretend otherwise."""
    answers = Answers("n", "", "n", "q")  # reject → no reason given → do not gap it

    assert (
        main(
            ["--registry", str(registry_file), "verify", "--verifier", "A Human"],
            fetcher=fetcher_for,
            ask=answers,
        )
        == 0
    )

    assert load_registry(registry_file).rejected == ()
    assert "REFUSED" in capsys.readouterr().out


def test_an_unrecognised_answer_skips_rather_than_guessing(
    registry_file: Path, fetcher_for: StubFetcher, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing the tool must never do with an ambiguous human is pick an answer."""
    assert (
        main(
            ["--registry", str(registry_file), "verify", "--verifier", "A Human", "--limit", "1"],
            fetcher=fetcher_for,
            ask=Answers("maybe?"),
        )
        == 0
    )

    assert load_registry(registry_file).unverified != ()
    assert "unrecognised answer — skipped" in capsys.readouterr().out


def test_the_committed_registry_is_still_entirely_unverified(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A standing check on the real file, and it is NOT a prohibition on doing the work — it
    is a prohibition on the work being *claimed* without being done. When a human verifies an
    entry with `sentinel verify`, the count moves, this assertion's message moves with it, and
    the doc-drift gate forces every document to move too. What it catches is the other thing:
    a flag flipped by anyone who is not a named human, which is precisely what an AI agent
    tidying up a repo would do."""
    registry = load_registry()

    for source in registry.verified_sources:
        assert source.verification.verifier, f"{source.id} is verified by nobody"
        assert source.verification.at, f"{source.id} is verified on no date"

    assert len(registry.verified_sources) == 0, (
        "the committed registry now claims human-verified entries. If a person really did the "
        "work with `sentinel verify`, update this count and the gated docs; if a machine did "
        "it, revert it."
    )
    assert len(registry.unverified) == 152
