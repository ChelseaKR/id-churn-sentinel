"""MERGE-BLOCKING GATE: unreviewed drift can never reach the published feed.

`make no-unreviewed-in-feed` runs exactly this file.

The published feed is the product. A4TE, Trans Lifeline, Namesake, and legal-aid orgs are
meant to consume it and update their own guidance from it — which means an item in this feed
propagates outward into the advice real people act on. An unreviewed item is a *machine's
observation that some bytes moved*. If that reaches the feed, a hash comparison becomes,
three hops downstream, a sentence on a website telling someone what documents they need.

So the feed contains only records a named human confirmed and classified. Not unreviewed
ones. Not dismissed ones (reviewed noise is still noise). Only what a person decided was
worth someone's attention.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.changes import ChangeKind, ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.publish import FEED_SCHEMA_VERSION, publish
from id_churn_sentinel.core.registry import Source
from id_churn_sentinel.core.store import SnapshotStore

from .conftest import StubFetcher

pytestmark = pytest.mark.feed_integrity


def test_unreviewed_drift_is_not_published(tmp_path: Path, observed_change: ChangeRecord) -> None:
    """THE GATE. A freshly-detected change — the state every change starts in — is
    withheld from both feed artifacts."""
    assert not observed_change.publishable

    result = publish([observed_change], tmp_path)

    assert result.published == 0
    assert observed_change.id not in (tmp_path / "feed.xml").read_text()
    payload = json.loads((tmp_path / "changes.json").read_text())
    assert payload["changes"] == []


def test_an_unreviewed_removal_escalation_is_not_published(tmp_path: Path, source: Source) -> None:
    """THE GATE, on the M3 escalation path. "A federal page about passport sex markers has
    disappeared" is the single most explosive sentence this feed could emit, and it is
    exactly the sentence a machine must not emit on its own. A source going quiet for three
    weeks is an observation about our sockets; whether it means the page was scrubbed, or a
    WAF started hating us, or the host is down, is a human's call — and until a human makes
    it, it does not leave the building."""
    escalation = ChangeRecord.possibly_removed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        last_known_hash="a" * 64,
        consecutive_failures=3,
        last_error="HTTP 404",
    )
    assert escalation.kind is ChangeKind.POSSIBLY_REMOVED
    assert not escalation.publishable

    result = publish([escalation], tmp_path)

    assert result.published == 0
    assert escalation.id not in (tmp_path / "feed.xml").read_text()
    assert json.loads((tmp_path / "changes.json").read_text())["changes"] == []


def test_a_published_escalation_does_not_read_as_a_content_change(
    tmp_path: Path, source: Source
) -> None:
    """Once a human HAS confirmed it, it publishes — and it must say what it actually is.
    "[TX] birth_certificate — substantive change at <url>" would be a lie for a source that
    stopped answering: a consumer would reasonably reword it as "Texas changed its page",
    when what the human confirmed is that Texas's page can no longer be reached. Different
    observation, different sentence."""
    confirmed = ChangeRecord.possibly_removed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        last_known_hash="a" * 64,
        consecutive_failures=4,
        last_error="HTTP 404",
    ).reviewed_by(
        reviewer="Chelsea Kelly-Reif",
        significance=Significance.SUBSTANTIVE,
        status=ReviewStatus.CONFIRMED,
        note="Confirmed by hand: the page 404s in a browser. TX removed it.",
    )

    assert publish([confirmed], tmp_path).published == 1

    feed = (tmp_path / "feed.xml").read_text()
    assert "SOURCE UNREACHABLE (possibly removed)" in feed
    assert "possibly_removed" in feed

    payload = json.loads((tmp_path / "changes.json").read_text())
    item = payload["changes"][0]
    assert item["kind"] == "possibly_removed"
    assert item["new_hash"] == ""  # no content was ever fetched; none is claimed
    assert item["reviewer"] == "Chelsea Kelly-Reif"


def test_dismissed_changes_are_not_published(tmp_path: Path, observed_change: ChangeRecord) -> None:
    """Reviewed noise is still noise. A consumer polling this feed should see only changes
    a person decided were worth someone's attention."""
    dismissed = observed_change.reviewed_by(
        reviewer="A Human",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.DISMISSED,
        note="Nav menu reshuffle, no substantive text change.",
    )
    assert not dismissed.publishable

    result = publish([dismissed], tmp_path)

    assert result.published == 0
    assert json.loads((tmp_path / "changes.json").read_text())["changes"] == []


def test_a_confirmed_but_unclassified_record_cannot_be_published(
    tmp_path: Path, observed_change: ChangeRecord
) -> None:
    """Defence in depth. The types and the schema both refuse to *create* this record; if
    one were smuggled in anyway (a hand-edited DB, a bad migration), the publisher still
    refuses to emit it. `publishable` checks all three properties, not just the status."""
    smuggled = replace(
        observed_change,
        review_status=ReviewStatus.CONFIRMED,
        significance=Significance.UNCLASSIFIED,
        reviewer="A Human",
    )
    assert not smuggled.publishable
    assert publish([smuggled], tmp_path).published == 0


def test_a_confirmed_record_with_no_reviewer_cannot_be_published(
    tmp_path: Path, observed_change: ChangeRecord
) -> None:
    smuggled = replace(
        observed_change,
        review_status=ReviewStatus.CONFIRMED,
        significance=Significance.SUBSTANTIVE,
        reviewer=None,
    )
    assert not smuggled.publishable
    assert publish([smuggled], tmp_path).published == 0


def test_only_the_reviewed_record_survives_a_mixed_batch(
    tmp_path: Path, observed_change: ChangeRecord, confirmed_change: ChangeRecord
) -> None:
    dismissed = replace(
        observed_change,
        id="dismissed01",
        review_status=ReviewStatus.DISMISSED,
        significance=Significance.EDITORIAL,
        reviewer="A Human",
    )
    unreviewed = replace(observed_change, id="unreviewed1")

    result = publish([unreviewed, dismissed, confirmed_change], tmp_path)

    assert result.published == 1
    payload = json.loads((tmp_path / "changes.json").read_text())
    assert [c["id"] for c in payload["changes"]] == [confirmed_change.id]
    feed = (tmp_path / "feed.xml").read_text()
    assert confirmed_change.id in feed
    assert "unreviewed1" not in feed
    assert "dismissed01" not in feed


def test_the_end_to_end_cli_flow_withholds_until_a_human_reviews(
    tmp_path: Path, source: Source, fixture_before: bytes, fixture_after: bytes
) -> None:
    """The whole pipeline through the real CLI: watch → drift → publish (empty) → review →
    publish (one item). The feed stays empty until a named human acts."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
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
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    db = tmp_path / "cli.db"
    out = tmp_path / "dist"
    base = ["--registry", str(registry_path), "--db", str(db)]
    stub = StubFetcher({source.url: (fixture_before, "text/html")})

    assert main([*base, "watch"], fetcher=stub) == 0
    stub.set(source.url, fixture_after)
    assert main([*base, "watch"], fetcher=stub) == 0

    # Drift exists...
    with SnapshotStore(db) as store:
        pending = store.changes(review_status=ReviewStatus.UNREVIEWED)
        assert len(pending) == 1
        change_id = pending[0].id

    # ...and the feed is still empty.
    assert main([*base, "publish", "--out", str(out)]) == 0
    assert json.loads((out / "changes.json").read_text())["changes"] == []

    # A human reviews it.
    assert (
        main(
            [
                *base,
                "review",
                change_id,
                "--reviewer",
                "Chelsea Kelly-Reif",
                "--significance",
                "substantive",
                "--status",
                "confirmed",
                "--note",
                "TX now requires a court order for the sex field.",
            ]
        )
        == 0
    )

    # Now, and only now, it publishes.
    assert main([*base, "publish", "--out", str(out)]) == 0
    payload = json.loads((out / "changes.json").read_text())
    assert len(payload["changes"]) == 1
    item = payload["changes"][0]
    assert item["id"] == change_id
    assert item["significance"] == "substantive"
    assert item["review_status"] == "confirmed"
    assert item["reviewer"] == "Chelsea Kelly-Reif"
    assert "+a court order is required to change the sex field" in item["diff_excerpt"]


def test_the_feed_requires_no_account_and_carries_no_tracking(
    tmp_path: Path, confirmed_change: ChangeRecord
) -> None:
    """A subscriber list for a trans-ID-law feed is a list of trans people. The safest way
    to protect that list is to never create one. So: no auth, no email capture, no analytics
    beacon, no tracking pixel, no third-party host in the published artifacts.

    See docs/RESPONSIBLE-TECH-AUDITS.md §C.
    """
    publish([confirmed_change], tmp_path)
    artifacts = (tmp_path / "feed.xml").read_text() + (tmp_path / "changes.json").read_text()
    lowered = artifacts.lower()

    for tracker in (
        "google-analytics",
        "googletagmanager",
        "doubleclick",
        "facebook.com",
        "segment.io",
        "mixpanel",
        "utm_source",
        "utm_medium",
        "<img",
        "subscribe",
        "sign up",
        "api_key",
        "token=",
    ):
        assert tracker not in lowered, f"the feed must not carry {tracker!r}"


def test_the_json_feed_is_versioned_and_carries_its_disclaimer(
    tmp_path: Path, confirmed_change: ChangeRecord
) -> None:
    """Consumers pin against `schema_version`. And every payload restates, in-band, that a
    change to a page is not a claim about the law — because the disclaimer has to travel
    with the data, not live only on a README nobody re-reads."""
    publish([confirmed_change], tmp_path)
    payload = json.loads((tmp_path / "changes.json").read_text())

    assert payload["schema_version"] == FEED_SCHEMA_VERSION
    assert "does not assert what the law is" in payload["disclaimer"]
    assert "reviewed by a named human" in payload["disclaimer"]
