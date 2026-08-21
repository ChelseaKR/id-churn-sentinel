"""MERGE-BLOCKING GATE: a source cannot reach a consumer stripped of its verification status.

`make no-unlabelled-source` runs exactly this file, and stage 6 of `make verify` runs it
alongside `test_feed_integrity.py`. The two gates are the same discipline pointed at two
different **implicit claims**, and neither claim is one this tool has earned:

* *"a machine noticed this, so it must matter"* — held by `no-unreviewed-in-feed`.
* *"this URL is in your list, so it must be the right page"* — held here.

The second one is easy to miss precisely because nobody ever says it out loud. The published
site lists one official-looking URL per (jurisdiction, document class). A caseworker reads a
row saying *"OH · Birth certificate · Ohio Department of Health · <url>"* as **"this is Ohio's
official birth-certificate page"** — which is a completely reasonable reading of a table like
that, and **nobody has checked that it is true**. Today `0 of 152 sources are human-verified`.
If that entry is wrong, the person acting on it is sent to the wrong office on a day they took
off work, and a wrong *citation* is worse than a wrong "no change": it does not merely fail to
warn someone, it actively directs them.

Machine-checking cannot close the gap — `courts.oregon.gov` serves a soft 404 with HTTP 200
and `ecfr.gov` serves a bot-wall titled "Request Access" with HTTP 200 — so the honest move is
to **carry the status with the source, everywhere it goes**, as a machine-readable field and as
a word, and to make it structurally impossible to publish one without the other. `publish()`
requires the registry; these tests assert the result on the published bytes.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from id_churn_sentinel.core.changes import (
    ChangeRecord,
    IndependentReviewStatus,
    ReviewStatus,
    Significance,
)
from id_churn_sentinel.core.publish import publish
from id_churn_sentinel.core.registry import (
    REJECTED,
    UNVERIFIED,
    VERIFIED,
    Registry,
    Source,
    Verification,
    load_registry,
)
from id_churn_sentinel.core.site import feed_slug
from id_churn_sentinel.errors import PublishError

from .conftest import eligible_source

pytestmark = pytest.mark.source_labelling

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

# The words a reader sees. Status is never a colour and never an icon: the caseworker most
# likely to be reading this with a screen reader is exactly the one a red dot fails silently.
STATUS_WORDS = ("UNVERIFIED", "VERIFIED", "REJECTED", "WITHDRAWN")


@pytest.fixture
def real_registry() -> Registry:
    """The committed registry — the actual product, all 152 of it."""
    return load_registry()


@pytest.fixture
def federal_change(federal_source: Source) -> ChangeRecord:
    """A published change citing a source that really is in the committed registry."""
    observed = ChangeRecord.observed(
        source_id=federal_source.id,
        jurisdiction=federal_source.jurisdiction,
        document_class=federal_source.document_class,
        url=federal_source.url,
        previous_hash="c" * 64,
        new_hash="d" * 64,
        diff_excerpt="-old federal passage\n+new federal passage",
        observed_at=NOW - timedelta(hours=3),
    )
    first = observed.reviewed_by(
        reviewer="First Federal Reviewer",
        significance=Significance.SUBSTANTIVE,
        status=ReviewStatus.CONFIRMED,
        reviewed_at=NOW - timedelta(hours=2),
    )
    return first.independently_reviewed_by(
        reviewer="Independent Federal Reviewer",
        status=IndependentReviewStatus.CONFIRMED,
        qualification_ref="tests/evidence/synthetic-independent-qualification.json",
        conflict_attestation_ref="tests/evidence/synthetic-independent-conflict.json",
        reviewed_at=NOW - timedelta(hours=1),
    )


@pytest.fixture
def published(tmp_path: Path, real_registry: Registry) -> Path:
    publish([], tmp_path, registry=real_registry, now=NOW)
    return tmp_path


# ---- THE GATE ----------------------------------------------------------------------------


def test_no_source_id_appears_in_any_published_artifact_without_its_status(
    published: Path, real_registry: Registry
) -> None:
    """THE GATE, stated as literally as it can be: take every byte we publish, find every
    source we mention in it, and prove we said what is behind that source in the same
    document. Not on the front page. Not in a linked disclaimer. In the document a consumer
    actually opened.
    """
    ids = {source.id for source in real_registry.sources}
    checked = 0

    for path in sorted(published.iterdir()):
        text = path.read_text(encoding="utf-8")
        mentioned = {source_id for source_id in ids if source_id in text}
        if not mentioned:
            continue
        checked += 1
        lowered = text.lower()
        assert "unverified" in lowered, (
            f"{path.name} names {len(mentioned)} source(s) and never says whether a human has "
            f"confirmed any of them. A source that travels without its status is an implicit "
            f"claim of authority nobody made."
        )
        # ...and it is *countable*, not a single blanket sentence at the top: every mentioned
        # source is individually labelled.
        labels = lowered.count("unverified") + lowered.count("verified by")
        assert labels >= len(mentioned), (
            f"{path.name} mentions {len(mentioned)} sources but carries only {labels} status "
            f"labels — some source in it is unlabelled."
        )

    assert checked >= 3, "expected the site, the inventory and the change feeds to name sources"


def test_every_source_in_sources_json_carries_a_machine_readable_status(published: Path) -> None:
    """The inventory is the document an integrator maps their own pages against. Every entry
    in it says, in a field, whether a human has confirmed it."""
    payload = json.loads((published / "sources.json").read_text())

    assert payload["schema_version"] == "2.0"
    assert payload["coverage"]["human_verified"] == 0  # derived, not typed
    assert payload["coverage"]["unverified"] == payload["coverage"]["registered_candidates"]
    assert payload["coverage"]["attempt_eligible"] == 0
    assert payload["coverage"]["ineligible"] == payload["coverage"]["registered_candidates"]
    assert payload["coverage"]["ineligibility_reasons"] == {
        "fetch-policy-unreviewed": payload["coverage"]["registered_candidates"],
        "unverified": payload["coverage"]["registered_candidates"],
    }
    assert "NOT HUMAN-VERIFIED" in payload["disclaimer"]

    for source in payload["sources"]:
        assert source["verification_status"] in {UNVERIFIED, VERIFIED, REJECTED}
        assert source["human_verified"] is False
        assert source["attempt_eligible"] is False
        assert source["fetch_policy_outcome"] == "unreviewed"
        assert source["ineligibility_reasons"] == ["unverified", "fetch-policy-unreviewed"]
        assert (
            "UNVERIFIED — machine-checked, not human-confirmed"
            in (source["verification_statement"])
        )
        assert "NO HUMAN has confirmed" in source["verification_statement"]


def test_every_per_jurisdiction_feed_carries_the_status_of_its_own_sources(
    published: Path, real_registry: Registry
) -> None:
    """The per-jurisdiction feeds are the artifact a legal-aid clinic actually subscribes to,
    and they are the artifact where this is easiest to get wrong: the change array is EMPTY,
    so without the `sources` block a Texas clinic polling `changes-us-tx.json` would learn
    nothing at all about the registry behind it — and would go on assuming the URLs in it had
    been confirmed by somebody."""
    for jurisdiction in sorted(real_registry.jurisdictions):
        slug = feed_slug(jurisdiction)
        payload = json.loads((published / f"changes-{slug}.json").read_text())
        expected = real_registry.for_jurisdiction(jurisdiction)

        assert payload["jurisdiction"] == jurisdiction
        assert payload["registry_verification"]["scope"] == jurisdiction
        assert payload["registry_verification"]["sources"] == len(expected)
        assert payload["registry_verification"]["human_verified"] == 0
        assert {s["source_id"] for s in payload["sources"]} == {s.id for s in expected}
        for source in payload["sources"]:
            assert source["verification_status"] == UNVERIFIED

        channel = _channel(published / f"feed-{slug}.xml")
        description = channel.findtext("description") or ""
        assert f"0 of {len(expected)} sources in {jurisdiction} are human-verified" in description
        assert "UNVERIFIED" in description


def test_a_change_from_an_unverified_source_is_refused_before_any_artifact_is_written(
    tmp_path: Path, real_registry: Registry, federal_change: ChangeRecord
) -> None:
    """A reviewed diff cannot make an unverified source authoritative by publication."""
    with pytest.raises(PublishError, match=r"not publication-eligible.*unverified"):
        publish([federal_change], tmp_path, registry=real_registry, now=NOW)
    assert not (tmp_path / "changes.json").exists()


def test_the_site_says_it_above_the_fold_and_before_the_numbers(published: Path) -> None:
    """A footnote under a long page is not a disclosure. The reader has to be told what this
    list is *before* they read the list — and before the coverage numbers, which are the part
    that makes it look authoritative."""
    page = (published / "index.html").read_text()

    notice = page.index("Read this first")
    coverage = page.index('<h2 id="coverage">')
    sources = page.index('<h2 id="sources">')
    assert notice < coverage < sources

    for sentence in (
        "candidate</em> official URL",
        "No human has confirmed that each one is the official page it claims to be</strong>",
        "Do not rely on this list as authoritative guidance",
        "What this tool does claim",
        "What this tool never claims",
        "<strong>For a published observation: this URL changed</strong>",
        "<strong>What the law is</strong>",
    ):
        assert sentence in page, f"the front door must say: {sentence!r}"


def test_every_source_row_on_the_site_carries_a_status_word(published: Path) -> None:
    """WCAG 2.2 AA, 1.4.1: status is a WORD. Not a colour, not an icon, not a tick. Every row
    of every source table says what is behind that source."""
    page = (published / "index.html").read_text()
    rows = re.findall(r"<tr><th scope=\"row\">.*?</tr>", page, re.DOTALL)
    source_rows = [row for row in rows if "<code>" in row]

    assert len(source_rows) == 152
    for row in source_rows:
        assert any(word in row for word in STATUS_WORDS), row[:120]

    for colour_only in ('class="ok"', 'class="bad"', ".status-red", ".status-green", "🔴", "✅"):
        assert colour_only not in page


def test_publish_cannot_be_called_without_the_registry(
    tmp_path: Path, confirmed_change: ChangeRecord
) -> None:
    """The structural half of the gate, and the reason the tests above can never be
    circumvented by a new artifact someone forgets to label: there is no way to write anything
    at all from this module without the registry that knows each source's status. It used to
    default to `None`."""
    with pytest.raises(TypeError):
        publish([confirmed_change], tmp_path)  # type: ignore[call-arg]


# ---- the status is derived, so it moves the day a human moves it --------------------------


def test_a_verified_source_is_published_as_verified_with_the_humans_name(
    tmp_path: Path, source: Source, confirmed_change: ChangeRecord
) -> None:
    """The other half of honesty: when the work IS done, the artifacts say so — by themselves,
    with the name of the person who did it and the date they did it. Nothing here is typed by
    hand, so nothing here can lag behind the registry."""
    verified = replace(
        eligible_source(source),
        verification=replace(
            eligible_source(source).verification,
            verifier="Chelsea Kelly-Reif",
            at="2026-07-13",
        ),
    )
    registry = Registry(version="1.0", sources=(verified,))

    publish([confirmed_change], tmp_path, registry=registry, now=NOW)

    payload = json.loads((tmp_path / "sources.json").read_text())
    assert payload["coverage"]["human_verified"] == 1
    assert payload["coverage"]["unverified"] == 0
    entry = payload["sources"][0]
    assert entry["verification_status"] == VERIFIED
    assert entry["verified_by"] == "Chelsea Kelly-Reif"
    assert entry["verified_at"] == "2026-07-13"

    page = (tmp_path / "index.html").read_text()
    assert "VERIFIED — confirmed by Chelsea Kelly-Reif on 2026-07-13" in page
    assert "All 1 sources are human-verified" in page

    channel = _channel(tmp_path / "feed.xml")
    assert "All 1 sources in the registry are HUMAN-VERIFIED" in (
        channel.findtext("description") or ""
    )


def test_a_half_finished_burn_down_is_reported_as_half_finished(
    tmp_path: Path, source: Source, arizona_source: Source
) -> None:
    """The state this project will actually be in for most of its life: some verified, some
    not. The page must not round that up. "Only 1 of 2 sources are human-verified" is the
    headline, and the unverified row still says UNVERIFIED next to it — a partial burn-down
    that renders as a clean bill of health is the failure this whole gate exists to prevent."""
    verified = replace(
        source,
        verified=True,
        verification=Verification(status=VERIFIED, verifier="A Human", at="2026-07-14"),
    )
    registry = Registry(version="1.0", sources=(verified, arizona_source))

    publish([], tmp_path, registry=registry, now=NOW)
    page = (tmp_path / "index.html").read_text()

    assert "only 1 of 2 sources are human-verified" in page
    assert "VERIFIED — confirmed by A Human on 2026-07-14" in page
    assert "UNVERIFIED — machine-checked, not human-confirmed" in page

    coverage = json.loads((tmp_path / "sources.json").read_text())["coverage"]
    assert (coverage["human_verified"], coverage["unverified"]) == (1, 1)


def test_a_rejected_source_is_published_as_rejected_rather_than_quietly_dropped(
    tmp_path: Path, source: Source
) -> None:
    """A human found the URL is wrong. It stays, labelled REJECTED, until it is repaired —
    because a consumer who picked it up last week needs to be told, and because deleting it
    silently would take the finding with it."""
    rejected = replace(
        source,
        verification=Verification(
            status=REJECTED,
            verifier="Chelsea Kelly-Reif",
            at="2026-07-14",
            note="This is the county clerk's page, not the state's.",
        ),
    )
    registry = Registry(version="1.0", sources=(rejected,))

    publish([], tmp_path, registry=registry, now=NOW)

    entry = json.loads((tmp_path / "sources.json").read_text())["sources"][0]
    assert entry["verification_status"] == REJECTED
    assert entry["human_verified"] is False

    page = (tmp_path / "index.html").read_text()
    assert "REJECTED — Chelsea Kelly-Reif found this is not the official page" in page
    assert "must not be relied on" in page


def test_a_change_citing_a_source_that_left_the_registry_is_refused(
    tmp_path: Path, registry: Registry, confirmed_change: ChangeRecord
) -> None:
    """A withdrawn source cannot support a newly published observation."""
    orphan = ChangeRecord.observed(
        source_id="gone-from-registry",
        jurisdiction=confirmed_change.jurisdiction,
        document_class=confirmed_change.document_class,
        url=confirmed_change.url,
        previous_hash="e" * 64,
        new_hash="f" * 64,
        diff_excerpt="-old orphan passage\n+new orphan passage",
        observed_at=NOW - timedelta(hours=2),
    ).reviewed_by(
        reviewer="Orphan Source Reviewer",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.CONFIRMED,
        reviewed_at=NOW - timedelta(hours=1),
    )

    with pytest.raises(PublishError, match="withdrawn or absent"):
        publish([orphan], tmp_path, registry=registry, now=NOW)


def _channel(path: Path) -> ET.Element:
    root = ET.fromstring(path.read_text())  # noqa: S314 — our own output
    channel = root.find("channel")
    assert channel is not None
    return channel
