"""Shared fixtures — and the reason this suite never touches the network.

`StubFetcher` implements the `Fetcher` protocol over a dict. Every test that exercises
the detector or watcher, and every test that exercises the CLI, injects one. There is no test in this
repo that resolves a hostname, opens a socket, or depends on a state website being up,
which is what makes the suite runnable on a plane, in a locked-down CI runner, and on the
day Texas's DPS site is down.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from id_churn_sentinel.core.changes import (
    ChangeRecord,
    IndependentReviewStatus,
    ReviewStatus,
    Significance,
)
from id_churn_sentinel.core.fetch import FetchResult
from id_churn_sentinel.core.registry import (
    FETCH_POLICY_ALLOW,
    VERIFIED,
    FetchPolicyDecision,
    Registry,
    Source,
    Verification,
)
from id_churn_sentinel.core.store import SnapshotStore

FIXTURES = Path(__file__).parent / "fixtures"


def eligible_source(source: Source) -> Source:
    """Synthetic, fully evidenced source for watcher/publisher unit tests.

    These identities are fixtures only; they never modify the committed registry or pretend
    its real verification queue has been completed.
    """

    return replace(
        source,
        verified=True,
        verification=Verification(
            status=VERIFIED,
            verifier="Synthetic Source Reviewer",
            at="2026-01-01",
            evidence="tests/evidence/synthetic-source-review.json",
            expires_at="2099-12-31",
        ),
        fetch_policy=FetchPolicyDecision(
            outcome=FETCH_POLICY_ALLOW,
            reviewer="Synthetic Policy Reviewer",
            at="2026-01-01",
            expires_at="2099-12-31",
            evidence="tests/evidence/synthetic-fetch-policy-review.json",
            reason="synthetic fixture authorizes the injected offline fetcher",
        ),
    )


def eligible_source_entry(source: Source) -> dict[str, object]:
    eligible = eligible_source(source)
    return {
        "id": eligible.id,
        "jurisdiction": eligible.jurisdiction,
        "document_class": eligible.document_class,
        "url": eligible.url,
        "authority": eligible.authority,
        "verified": True,
        "verification": {
            "status": eligible.verification.status,
            "verifier": eligible.verification.verifier,
            "at": eligible.verification.at,
            "evidence": eligible.verification.evidence,
            "expires_at": eligible.verification.expires_at,
        },
        "fetch_policy": eligible.fetch_policy.to_dict(),
        "notes": "synthetic test fixture",
    }


class StubFetcher:
    """A `Fetcher` backed by a dict of URL → (body, content_type). Anything not in the
    dict fetches as a failure, which lets a test model an outage by simply omitting a URL."""

    def __init__(self, responses: dict[str, tuple[bytes, str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        if url not in self.responses:
            return FetchResult.failure(url, "stubbed outage: no response configured")
        body, content_type = self.responses[url]
        return FetchResult(
            url=url,
            ok=True,
            status=200,
            content_type=content_type,
            body=body,
            fetched_at=datetime.now(UTC),
        )

    def set(self, url: str, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.responses[url] = (body, content_type)


@pytest.fixture
def fixture_before() -> bytes:
    return (FIXTURES / "state-page-before.html").read_bytes()


@pytest.fixture
def fixture_after() -> bytes:
    return (FIXTURES / "state-page-after.html").read_bytes()


@pytest.fixture
def fixture_cosmetic() -> bytes:
    """Same text as `before`, different markup — the case the normalizer must NOT report."""
    return (FIXTURES / "state-page-cosmetic.html").read_bytes()


@pytest.fixture
def source() -> Source:
    return Source(
        id="tx-dps-change-dl-id",
        jurisdiction="TX",
        document_class="drivers_license",
        url="https://www.dps.texas.gov/section/driver-license/how-change-information-your-driver-license-or-id-card",
        authority="Texas Department of Public Safety",
        verified=False,
        notes="test fixture",
    )


@pytest.fixture
def arizona_source() -> Source:
    return Source(
        id="az-mvd-driver-services",
        jurisdiction="AZ",
        document_class="drivers_license",
        url="https://azdot.gov/mvd/services/driver-services",
        authority="Arizona Department of Transportation, MVD",
        verified=False,
        notes="test fixture",
    )


@pytest.fixture
def federal_source() -> Source:
    return Source(
        id="us-passport-sex-markers",
        jurisdiction="US",
        document_class="passport",
        url="https://travel.state.gov/en/passports/apply/unique-needs/sex-markers.html",
        authority="U.S. Department of State, Bureau of Consular Affairs",
        verified=False,
        notes="test fixture",
    )


@pytest.fixture
def registry(source: Source, arizona_source: Source, federal_source: Source) -> Registry:
    """The registry every `publish()` in the suite is given.

    It is a required argument to `publish()`, not an optional one, and that is the point: a
    published artifact cannot exist without the registry that knows each source's verification
    status, so there is no code path — and therefore no test — in which a source reaches a
    consumer with its status silently omitted.
    """
    return Registry(
        version="1.0",
        sources=tuple(eligible_source(item) for item in (source, arizona_source, federal_source)),
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SnapshotStore]:
    with SnapshotStore(tmp_path / "sentinel.db") as opened:
        yield opened


@pytest.fixture
def fetcher(source: Source, fixture_before: bytes) -> StubFetcher:
    return StubFetcher({source.url: (fixture_before, "text/html; charset=utf-8")})


@pytest.fixture
def observed_change(source: Source) -> ChangeRecord:
    """A freshly-detected change: unclassified, unreviewed. The state everything starts in."""
    return ChangeRecord.observed(
        source_id=source.id,
        jurisdiction=source.jurisdiction,
        document_class=source.document_class,
        url=source.url,
        previous_hash="a" * 64,
        new_hash="b" * 64,
        diff_excerpt="-old passage\n+new passage",
    )


@pytest.fixture
def confirmed_change(observed_change: ChangeRecord) -> ChangeRecord:
    first = observed_change.reviewed_by(
        reviewer="Chelsea Kelly-Reif",
        significance=Significance.SUBSTANTIVE,
        status=ReviewStatus.CONFIRMED,
        note="TX added a court-order requirement to the DL change page.",
    )
    return first.independently_reviewed_by(
        reviewer="Synthetic Independent Reviewer",
        status=IndependentReviewStatus.CONFIRMED,
        qualification_ref="tests/evidence/synthetic-independent-qualification.json",
        conflict_attestation_ref="tests/evidence/synthetic-independent-conflict.json",
    )


@pytest.fixture
def fixture_loose_end_tag() -> bytes:
    """A page closing its `<script>` with `</script >` — the spelling `passage-text-v1`
    failed to strip, so the same bytes normalize differently under v1 and v2."""
    return (FIXTURES / "state-page-loose-end-tag.html").read_bytes()


# ---------------------------------------------------------------------------------------
# PDF builders (`PDF-01`)
# ---------------------------------------------------------------------------------------
#
# The suite has to exercise a *real* extractor, so it needs real PDFs — and it must stay
# air-gapped and deterministic, so it cannot download a government form or shell out to a
# generator. These builders emit byte-exact minimal PDFs whose every construct is chosen on
# purpose: `simple_pdf` is a plain WinAnsi document, and the keyword arguments turn on each
# construct the extractor is supposed to refuse or to read.
#
# They are builders and not fixture files because the interesting tests differ by ONE
# construct — a filter, an encryption dictionary, a second definition of object 3 — and a
# directory of near-identical binary blobs hides exactly the difference under test.


def _pdf_object(number: int, body: bytes) -> bytes:
    return b"%d 0 obj\n" % number + body + b"\nendobj\n"


def _pdf_stream(number: int, dictionary: bytes, payload: bytes) -> bytes:
    head = b"<< " + dictionary + b" /Length %d >>" % len(payload)
    return _pdf_object(number, head + b"\nstream\n" + payload + b"\nendstream")


def _pdf_document(objects: list[bytes]) -> bytes:
    """Assemble a PDF with a trailer. The cross-reference table is deliberately omitted:
    `core/pdf.py` does not read one (see its `_Document` docstring), so writing a fake one
    here would test a code path that does not exist."""
    return b"%PDF-1.4\n" + b"".join(objects) + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"


def _escape_pdf_text(text: str) -> bytes:
    raw = text.encode("cp1252")
    for special in (b"\\", b"(", b")"):
        raw = raw.replace(special, b"\\" + special)
    return raw


def content_stream_for(lines: list[str]) -> bytes:
    """A `BT … ET` block placing one line per `Td`, the way every generator writes a page."""
    out = [b"BT /F1 12 Tf 72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            out.append(b"0 -14 Td")
        out.append(b"(" + _escape_pdf_text(line) + b") Tj")
    out.append(b"ET")
    return b"\n".join(out)


def simple_pdf(
    text: str = "Bring a court order.",
    *,
    producer: str = "",
    encoding: bytes = b"/WinAnsiEncoding",
    subtype: bytes = b"/Type1",
    duplicate_object: bool = False,
    encrypted: bool = False,
    unsupported_filter: bool = False,
) -> bytes:
    """One deterministic single-page PDF.

    `producer` changes the bytes without changing a character of page text, which is exactly
    the re-render case `PDF-01` exists to tell apart from a real edit.
    """
    payload = content_stream_for(text.split("\n"))
    stream_dictionary = b"/Filter /LZWDecode" if unsupported_filter else b""
    objects = [
        _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        _pdf_object(
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        ),
        _pdf_stream(4, stream_dictionary, payload),
        _pdf_object(
            5,
            b"<< /Type /Font /Subtype " + subtype + b" /BaseFont /Helvetica"
            b" /Encoding " + encoding + b" >>",
        ),
    ]
    if producer:
        objects.append(_pdf_object(6, b"<< /Producer (" + _escape_pdf_text(producer) + b") >>"))
    if duplicate_object:
        objects.append(_pdf_object(3, b"<< /Type /Page /Parent 2 0 R >>"))
    if encrypted:
        objects.append(_pdf_object(7, b"<< /Encrypt 8 0 R >>"))
    return _pdf_document(objects)
