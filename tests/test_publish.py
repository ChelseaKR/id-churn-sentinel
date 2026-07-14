"""Tests for :mod:`id_churn_sentinel.core.publish` — feed shape and escaping.

The "unreviewed never reaches the feed" gate lives in `test_feed_integrity.py`. This file
covers the artifacts themselves: RSS well-formedness, JSON shape, ordering, escaping.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from id_churn_sentinel.core.changes import ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.publish import FEED_SCHEMA_VERSION, changes_json, feed_xml, publish
from id_churn_sentinel.core.registry import Registry

GENERATED = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _reviewed_variant(
    base: ChangeRecord,
    *,
    source_id: str | None = None,
    jurisdiction: str | None = None,
    document_class: str | None = None,
    url: str | None = None,
    observed_at: datetime,
    previous_hash: str,
    new_hash: str,
) -> ChangeRecord:
    observed = ChangeRecord.observed(
        source_id=source_id or base.source_id,
        jurisdiction=jurisdiction or base.jurisdiction,
        document_class=document_class or base.document_class,
        url=url or base.url,
        observed_at=observed_at,
        previous_hash=previous_hash,
        new_hash=new_hash,
        diff_excerpt=base.diff_excerpt,
    )
    return observed.reviewed_by(
        reviewer="Synthetic Editorial Reviewer",
        significance=Significance.EDITORIAL,
        status=ReviewStatus.CONFIRMED,
        reviewed_at=GENERATED,
    )


def test_feed_xml_is_well_formed_rss(confirmed_change: ChangeRecord, registry: Registry) -> None:
    root = ET.fromstring(  # noqa: S314 — our own output
        feed_xml(
            [confirmed_change],
            feed_url="https://example.org",
            generated_at=GENERATED,
            registry=registry,
        )
    )

    assert root.tag == "rss"
    assert root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert len(items) == 1

    item = items[0]
    assert item.findtext("link") == confirmed_change.url
    assert item.findtext("guid") == confirmed_change.id
    guid = item.find("guid")
    assert guid is not None and guid.get("isPermaLink") == "false"
    # `kind` is a category too, so a consumer can filter "possibly_removed" escalations out
    # of (or into) their pipeline without parsing the description — and so is the verification
    # status of the SOURCE the item cites, so a pipeline can enforce that the URL was confirmed
    # without reading prose.
    assert {c.text for c in item.findall("category")} == {
        "TX",
        "drivers_license",
        "content_drift",
        "publication-status:active",
        "source-verification:verified",
    }

    title = item.findtext("title") or ""
    assert "[TX]" in title
    assert "substantive" in title

    description = item.findtext("description") or ""
    assert "Reviewed by: Chelsea Kelly-Reif" in description
    assert "Changed passages" in description
    assert confirmed_change.diff_excerpt in description
    # The two humans are not the same human, and the item says which is which. `Reviewed by`
    # read the diff; the synthetic source reviewer verified the fixture URL itself.
    assert "Source verification: VERIFIED — confirmed by Synthetic Source Reviewer" in description


def test_feed_channel_metadata_names_the_disclaimer(
    confirmed_change: ChangeRecord, registry: Registry
) -> None:
    root = ET.fromstring(  # noqa: S314 — our own output
        feed_xml(
            [confirmed_change],
            feed_url="https://example.org",
            generated_at=GENERATED,
            registry=registry,
        )
    )
    channel = root.find("channel")
    assert channel is not None
    description = channel.findtext("description") or ""
    assert "does not assert what the law is" in description
    assert FEED_SCHEMA_VERSION in (channel.findtext("generator") or "")


def test_lifecycle_rss_event_has_distinct_guid_time_and_review_receipt(
    confirmed_change: ChangeRecord, registry: Registry
) -> None:
    review_completed = confirmed_change.independent_reviewed_at or confirmed_change.reviewed_at
    assert review_completed is not None
    assert confirmed_change.reviewed_at is not None
    lifecycle_at = review_completed + timedelta(seconds=1)
    withdrawn = confirmed_change.withdrawn_by(
        actor="Lifecycle Decision Maker",
        reason="other_governed_reason",
        decided_at=lifecycle_at,
    )
    generated_at = lifecycle_at + timedelta(seconds=1)

    xml = feed_xml(
        [withdrawn],
        feed_url="https://example.org",
        generated_at=generated_at,
        registry=registry,
    )
    root = ET.fromstring(xml)  # noqa: S314 — our own output
    channel = root.find("channel")
    assert channel is not None
    item = channel.find("item")
    assert item is not None
    guid = item.findtext("guid") or ""
    assert guid.startswith(f"{withdrawn.id}-lifecycle-")
    assert guid != withdrawn.id
    assert parsedate_to_datetime(item.findtext("pubDate") or "") == lifecycle_at.replace(
        microsecond=0
    )
    description = item.findtext("description") or ""
    assert f"Reviewed at: {confirmed_change.reviewed_at.isoformat()}" in description
    assert "Independent review: confirmed by Synthetic Independent Reviewer" in description
    assert "Lifecycle actor: Lifecycle Decision Maker" in description
    assert f"Lifecycle at: {lifecycle_at.isoformat()}" in description

    payload = json.loads(
        changes_json(
            [withdrawn],
            feed_url="https://example.org",
            generated_at=generated_at,
            registry=registry,
        )
    )["changes"][0]
    assert payload["lifecycle_actor"] == "Lifecycle Decision Maker"
    assert payload["lifecycle_at"] == lifecycle_at.isoformat()
    assert payload["independent_reviewer"] == "Synthetic Independent Reviewer"


def test_xml_special_characters_are_escaped(
    confirmed_change: ChangeRecord, registry: Registry
) -> None:
    """A state page containing `<script>` or an `&` in a diff must not break the feed for
    every consumer downstream."""
    nasty = replace(
        confirmed_change,
        diff_excerpt="+<script>alert(\"x\")</script> & 'quotes'",
        review_note="R&D <b>note</b>",
    )
    xml = feed_xml(
        [nasty], feed_url="https://example.org", generated_at=GENERATED, registry=registry
    )

    assert "<script>" not in xml
    assert "&lt;script&gt;" in xml
    assert "R&amp;D" in xml

    root = ET.fromstring(xml)  # noqa: S314 — our own output; parses, so it is well-formed
    channel = root.find("channel")
    assert channel is not None
    description = (channel.findall("item")[0].findtext("description")) or ""
    assert '+<script>alert("x")</script>' in description  # round-trips to the original text


def test_publish_writes_both_artifacts(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    result = publish([confirmed_change], tmp_path / "published", registry=registry, now=GENERATED)

    assert result.feed_path.exists()
    assert result.changes_path.exists()
    assert result.feed_path.name == "feed.xml"
    assert result.changes_path.name == "changes.json"
    assert result.status_path is not None and result.status_path.name == "status.json"
    assert result.status_path.exists()
    assert result.published == 1


def test_publish_creates_the_output_directory(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    out = tmp_path / "a" / "b" / "published"
    publish([confirmed_change], out, registry=registry, now=GENERATED)
    assert (out / "feed.xml").exists()


def test_changes_json_shape(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    publish(
        [confirmed_change],
        tmp_path,
        registry=registry,
        feed_url="https://example.org",
        now=GENERATED,
    )
    payload = json.loads((tmp_path / "changes.json").read_text())

    assert payload["schema_version"] == FEED_SCHEMA_VERSION
    assert payload["feed_url"] == "https://example.org"
    assert payload["generated_at"] == GENERATED.isoformat()
    assert len(payload["changes"]) == 1
    assert payload["changes"][0]["reviewer"] == "Chelsea Kelly-Reif"


def test_items_are_newest_first_and_stable(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    """Stable ordering means a consumer diffing two fetches sees only real movement."""
    older = _reviewed_variant(
        confirmed_change,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        previous_hash="1" * 64,
        new_hash="2" * 64,
    )
    newer = _reviewed_variant(
        confirmed_change,
        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        previous_hash="3" * 64,
        new_hash="4" * 64,
    )

    publish([older, newer], tmp_path, registry=registry, now=GENERATED)
    payload = json.loads((tmp_path / "changes.json").read_text())

    assert [c["id"] for c in payload["changes"]] == [newer.id, older.id]


def test_an_empty_feed_is_still_valid(tmp_path: Path, registry: Registry) -> None:
    """Day one: nothing reviewed yet. The feed must still be a well-formed, publishable
    document — a consumer polling an empty feed should get an empty feed, not a 500."""
    result = publish([], tmp_path, registry=registry, now=GENERATED)

    assert result.published == 0
    root = ET.fromstring((tmp_path / "feed.xml").read_text())  # noqa: S314 — our own output
    channel = root.find("channel")
    assert channel is not None
    assert channel.findall("item") == []
    assert json.loads((tmp_path / "changes.json").read_text())["changes"] == []


def test_an_empty_feed_is_valid_rss_and_says_it_is_empty_rather_than_broken(
    registry: Registry,
) -> None:
    """With no reviewed changes the feed is legitimately empty. It must still parse as RSS
    2.0 — and it must not look like a build that failed halfway, because a consumer who
    concludes the feed is broken stops reading it, and a consumer who reads the silence as
    "nothing changed" has been told a thing we never said."""
    xml = feed_xml([], feed_url="https://example.org", generated_at=GENERATED, registry=registry)

    root = ET.fromstring(xml)  # noqa: S314 — our own output
    channel = root.find("channel")
    assert channel is not None
    assert channel.findall("item") == []
    assert channel.find("title") is not None  # the required RSS channel elements survive
    assert channel.find("link") is not None
    assert channel.find("description") is not None
    assert "No reviewed changes yet" in xml
    assert "EMPTY, not broken" in xml
    assert "not a claim" in xml  # an empty feed never claims nothing changed


def test_publishing_nothing_writes_both_artifacts(tmp_path: Path, registry: Registry) -> None:
    """`publish` with an empty queue is a success, not a no-op: the artifacts are written,
    so a consumer polling the URL gets a valid empty feed rather than a 404."""
    result = publish([], tmp_path, registry=registry, now=GENERATED)

    assert result.published == 0
    assert result.feed_path.exists()
    assert result.changes_path.exists()
    payload = json.loads(result.changes_path.read_text())
    assert payload["changes"] == []
    assert payload["schema_version"] == FEED_SCHEMA_VERSION
    assert "does not assert what the law is" in payload["disclaimer"]


# ---- per-jurisdiction feeds --------------------------------------------------------------
#
# An org that serves one state should not have to consume all 52, and "just filter
# changes.json" is a way of telling a legal-aid clinic to write code before it can read its
# own state. So every jurisdiction gets its own feed — and the isolation is a correctness
# property worth testing, because a TX feed carrying an AZ item would be read as a statement
# about Texas.


def test_each_jurisdiction_feed_carries_only_its_own_items(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    arizona = _reviewed_variant(
        confirmed_change,
        source_id="az-mvd-driver-services",
        jurisdiction="AZ",
        url="https://azdot.gov/mvd/services/driver-services",
        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        previous_hash="5" * 64,
        new_hash="6" * 64,
    )

    publish([confirmed_change, arizona], tmp_path, registry=registry)

    texas = (tmp_path / "feed-us-tx.xml").read_text()
    assert confirmed_change.id in texas
    assert arizona.id not in texas

    az_payload = json.loads((tmp_path / "changes-us-az.json").read_text())
    assert [c["id"] for c in az_payload["changes"]] == [arizona.id]
    assert az_payload["jurisdiction"] == "AZ"

    # ...and the all-jurisdictions feed still carries both. A per-jurisdiction feed is a
    # convenience, never a partition that could drop a record on the floor.
    everything = json.loads((tmp_path / "changes.json").read_text())
    assert {c["id"] for c in everything["changes"]} == {confirmed_change.id, arizona.id}


def test_a_scoped_feed_says_it_is_scoped(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    """The dangerous failure of a scoped feed is looking like an unscoped one: an empty
    `feed-us-tx.xml` must not be mistakable for a statement about the country."""
    publish([confirmed_change], tmp_path, registry=registry)

    feed = ET.fromstring((tmp_path / "feed-us-tx.xml").read_text())  # noqa: S314 — our own output
    channel = feed.find("channel")
    assert channel is not None
    title = channel.findtext("title") or ""
    description = channel.findtext("description") or ""

    assert title.endswith("— TX")
    assert "scoped to TX ONLY" in description
    assert "silence about TX is not evidence that nothing changed there" in description


def test_the_federal_bucket_gets_a_feed_that_is_not_called_us_us(
    tmp_path: Path, confirmed_change: ChangeRecord, registry: Registry
) -> None:
    federal = _reviewed_variant(
        confirmed_change,
        source_id="us-passport-sex-markers",
        jurisdiction="US",
        document_class="passport",
        url="https://travel.state.gov/en/passports/apply/unique-needs/sex-markers.html",
        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        previous_hash="7" * 64,
        new_hash="8" * 64,
    )

    publish([federal], tmp_path, registry=registry)

    assert (tmp_path / "feed-us.xml").exists()
    assert not (tmp_path / "feed-us-us.xml").exists()
    assert federal.id in (tmp_path / "feed-us.xml").read_text()


def test_an_unreviewed_record_reaches_no_jurisdiction_feed(
    tmp_path: Path, observed_change: ChangeRecord, registry: Registry
) -> None:
    """The safety gate holds per-jurisdiction too. Fifty-two new files are fifty-two new
    chances to leak an unreviewed record, and the guard runs once, before any of them."""
    publish([observed_change], tmp_path, registry=registry)


def test_exported_serializers_cannot_bypass_the_review_gate(
    observed_change: ChangeRecord, registry: Registry
) -> None:
    json_payload = json.loads(
        changes_json(
            [observed_change],
            feed_url="https://example.org",
            generated_at=GENERATED,
            registry=registry,
        )
    )
    xml = feed_xml(
        [observed_change],
        feed_url="https://example.org",
        generated_at=GENERATED,
        registry=registry,
    )
    root = ET.fromstring(xml)  # noqa: S314 — our own output
    channel = root.find("channel")

    assert json_payload["changes"] == []
    assert channel is not None and channel.findall("item") == []
    assert observed_change.id not in xml
