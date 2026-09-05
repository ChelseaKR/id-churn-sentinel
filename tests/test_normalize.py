"""Tests for :mod:`id_churn_sentinel.core.normalize`.

The load-bearing test in this file is `test_cosmetic_markup_churn_is_not_a_content_change`.
Everything else is detail. If normalization reports a re-minified stylesheet or a rotated
CSRF token as drift, every reviewer learns within two weeks that the feed is noise, and the
tool is dead — not because it missed a change, but because nobody reads it any more.
"""

from __future__ import annotations

import hashlib

from id_churn_sentinel.core.normalize import (
    EXTRACTION_OUTCOME_BINARY_OPAQUE,
    EXTRACTION_OUTCOME_PDF_REFUSED,
    EXTRACTION_OUTCOME_PDF_TEXT,
    EXTRACTION_OUTCOME_TEXT,
    ContentKind,
    content_evidence,
    content_hash,
    kind_for_content_type,
    normalize_html,
    normalize_text,
    passages,
)

from .conftest import simple_pdf


def test_cosmetic_markup_churn_is_not_a_content_change(
    fixture_before: bytes, fixture_cosmetic: bytes
) -> None:
    """A rotated CSRF token, a re-minified stylesheet, new class attributes, an `&nbsp;`,
    a changed HTML comment, and extra whitespace — same page, same text, SAME HASH."""
    before_hash, before_text = content_hash(fixture_before, "text/html")
    cosmetic_hash, cosmetic_text = content_hash(fixture_cosmetic, "text/html")

    assert before_text == cosmetic_text
    assert before_hash == cosmetic_hash


def test_real_text_change_is_a_content_change(fixture_before: bytes, fixture_after: bytes) -> None:
    before_hash, _ = content_hash(fixture_before, "text/html")
    after_hash, after_text = content_hash(fixture_after, "text/html")

    assert before_hash != after_hash
    assert "a court order is required to change the sex field" in after_text


def test_script_and_style_bodies_are_stripped() -> None:
    normalized = normalize_html("<p>text</p><script>var x = 'SECRET';</script><style>p{}</style>")
    assert "secret" not in normalized
    assert "text" in normalized


def test_script_and_style_bodies_are_stripped_when_the_end_tag_is_loosely_spelled() -> None:
    """`</script >` and even `</script foo="bar">` close a script in every browser. The
    end-tag regex used to require `</script>` exactly, so the element never matched and its
    *body* survived as page text — which is how a cache-busting build id or a CSRF token
    inside minified JavaScript gets into the content hash and makes a page look like it
    changes on every single fetch. This is the permanent-false-alarm failure the normalizer
    exists to prevent, and it failed silently."""
    normalized = normalize_html(
        "<p>text</p><script>var token = 'SECRET';</script >"
        "<script>var other = 'ATTRSECRET';</script data-x=\"1\">"
        "<style>p{color:red}</style\t>"
        "<style>p{content:'STYLESECRET'}</style\n>"
    )

    assert "secret" not in normalized
    assert "attrsecret" not in normalized
    assert "stylesecret" not in normalized
    assert "color" not in normalized
    assert normalized == "text"


def test_a_lookalike_end_tag_does_not_close_a_script() -> None:
    """Loosening the end tag must not loosen it into `</scriptfoo>`, which is a different tag
    and does not close a script in any browser — the tokenizer leaves script-data state only
    on `</script` followed by whitespace, `/`, or `>`. Matching it would end the element early
    and leak the rest of the real script body into the hashed page text."""
    normalized = normalize_html("<script>var x = 1;</scriptfoo>SECRET</script><p>REAL TEXT</p>")

    assert "secret" not in normalized
    assert "real text" in normalized


def test_a_whitespace_end_tag_hashes_the_same_as_the_tight_spelling() -> None:
    """The property that matters downstream: two pages whose only difference is how they
    spell the closing tag are the same *content*, so they must produce the same hash."""
    tight, _ = content_hash(b"<p>hello</p><script>var x = 1;</script>", "text/html")
    spaced, _ = content_hash(b"<p>hello</p><script>var x = 1;</script >", "text/html")

    assert tight == spaced


def test_comments_are_stripped() -> None:
    assert "build" not in normalize_html("<!-- build 12345 --><p>hello</p>")


def test_entities_are_resolved_not_blanked() -> None:
    """The TS original blanked entities to a space, which was fine for a hash. A human has
    to read the diff excerpt, so `don&rsquo;t` must diff as `don't`, not as `don t`."""
    # U+2019 is a real right-single-quote: the entity is RESOLVED, not blanked to a space.
    assert normalize_html("<p>you don&rsquo;t need a court order</p>") == (
        "you don\u2019t need a court order"
    )


def test_block_tags_become_passage_boundaries() -> None:
    """The divergence from the TS prior art: a single-line normalization cannot be diffed."""
    assert passages(normalize_html("<li>one</li><li>two</li><li>three</li>")) == [
        "one",
        "two",
        "three",
    ]


def test_inline_tags_do_not_split_a_passage() -> None:
    assert passages(normalize_html("<p>a <em>court</em> order</p>")) == ["a court order"]


def test_normalization_lowercases() -> None:
    assert normalize_text("Court ORDER") == "court order"


def test_passages_drops_blank_lines() -> None:
    assert passages("a\n\n\nb") == ["a", "b"]
    assert passages("") == []


def test_binary_content_is_hashed_as_raw_bytes() -> None:
    """Binary detection is over the raw bytes, whether or not any text came out.

    Unreadable bytes yield no text, and pretending otherwise would be a lie a reviewer would
    act on. A readable PDF yields text — and the digest is *still* the digest of the whole
    file, not of the text, which is what keeps a change outside the page text visible."""
    digest, text = content_hash(b"%PDF-1.7 binary\x00\xff", "application/pdf")
    assert text == ""
    assert digest == hashlib.sha256(b"%PDF-1.7 binary\x00\xff").hexdigest()

    readable = simple_pdf("Bring a court order.")
    digest, text = content_hash(readable, "application/pdf")
    assert text == "bring a court order."
    assert digest == hashlib.sha256(readable).hexdigest()


def test_content_type_routing() -> None:
    assert kind_for_content_type("text/html; charset=utf-8") == ContentKind.HTML
    assert kind_for_content_type("application/xhtml+xml") == ContentKind.HTML
    assert kind_for_content_type("text/plain") == ContentKind.TEXT
    assert kind_for_content_type("application/json") == ContentKind.TEXT
    assert kind_for_content_type("application/pdf") == ContentKind.BINARY
    # Unknown/absent must fail toward binary: a wrong guess toward HTML would strip real
    # content out of the hash and could hide a change.
    assert kind_for_content_type(None) == ContentKind.BINARY
    assert kind_for_content_type("") == ContentKind.BINARY


def test_plain_text_is_normalized_but_not_de_tagged() -> None:
    digest, text = content_hash(b"Line One\n\n  Line   Two  ", "text/plain")
    assert text == "line one\nline two"
    assert len(digest) == 64


def test_undecodable_bytes_do_not_crash_html_normalization() -> None:
    digest, text = content_hash(b"<p>caf\xe9</p>", "text/html")
    assert len(digest) == 64
    assert "caf" in text


def test_content_evidence_for_html_distinguishes_raw_and_normalized_hashes() -> None:
    """DATA-04/DET-01 wants *distinct* raw-byte and normalized-text hashes — and the
    detection hash must be byte-for-byte the one `content_hash` computes, so evidence and
    detection can never quietly diverge."""
    body = b"<html><body><p>Bring a court order.</p></body></html>"
    evidence = content_evidence(body, "text/html")
    detection_hash, normalized_text = content_hash(body, "text/html")

    assert evidence.raw_sha256 == hashlib.sha256(body).hexdigest()
    assert evidence.normalized_sha256 == detection_hash
    assert evidence.detection_sha256 == detection_hash
    assert evidence.raw_sha256 != evidence.normalized_sha256
    assert evidence.normalized_text == normalized_text
    assert evidence.extraction_outcome == EXTRACTION_OUTCOME_TEXT


def test_content_evidence_for_unreadable_binary_claims_no_normalized_hash() -> None:
    """Bytes nobody read produced no text, so their evidence says so: the raw hash is the
    detection hash, and the normalized hash is empty rather than a hash of emptiness —
    claiming a normalized-text hash for bytes that yielded no text would be fabricated
    provenance.

    The two ways to get here are named apart. A `.docx` gets `binary-no-extractor` (nothing
    tried); a PDF the extractor would not stand behind gets `pdf-extraction-refused` plus the
    reason. Folding them together would lose the only fact that says whether widening the
    extractor would help."""
    body = b"PK\x03\x04 not a pdf at all"
    evidence = content_evidence(body, "application/octet-stream")

    assert evidence.raw_sha256 == hashlib.sha256(body).hexdigest()
    assert evidence.detection_sha256 == evidence.raw_sha256
    assert evidence.normalized_sha256 == ""
    assert evidence.normalized_text == ""
    assert evidence.extraction_outcome == EXTRACTION_OUTCOME_BINARY_OPAQUE
    assert evidence.extraction_detail == ""

    junk_pdf = b"%PDF-1.7 binary\x00\xff"
    refused = content_evidence(junk_pdf, "application/pdf")
    assert refused.detection_sha256 == refused.raw_sha256
    assert refused.normalized_sha256 == ""
    assert refused.normalized_text == ""
    assert refused.extraction_outcome == EXTRACTION_OUTCOME_PDF_REFUSED
    assert refused.extraction_detail  # the reason is recorded, not swallowed


def test_content_evidence_for_a_readable_pdf_keeps_detection_on_the_whole_file() -> None:
    """The safety property `PDF-01` turns on, asserted rather than described.

    A PDF this build can read carries BOTH hashes and they are different things: detection
    stays over the raw bytes, and the normalized hash records what the diff was computed
    from. If detection ever followed the extracted text, a change in bytes the extractor does
    not read — an annotation, an image, embedded metadata — would stop producing a change
    record at all, which is the wrong "no change" this project's risk register puts first."""
    body = simple_pdf("Bring a certified court order.")
    evidence = content_evidence(body, "application/pdf")

    assert evidence.extraction_outcome == EXTRACTION_OUTCOME_PDF_TEXT
    assert evidence.detection_sha256 == hashlib.sha256(body).hexdigest()
    assert evidence.normalized_sha256 != evidence.detection_sha256
    assert (
        evidence.normalized_sha256
        == hashlib.sha256(evidence.normalized_text.encode("utf-8")).hexdigest()
    )
    assert evidence.normalized_text == "bring a certified court order."
    assert evidence.extraction_detail == ""


def test_a_pdf_whose_text_is_unchanged_still_moves_the_detection_hash() -> None:
    """The same property from the other side, and the reason it is not merely tidy.

    Two PDFs with identical page text and different bytes — a re-render — must still be
    *detected*, so that a human decides whether the difference matters. What extraction buys
    is that the reviewer can now see the text did not move; what it must never buy is silence.
    """
    first = simple_pdf("Bring a court order.", producer="Alpha 1.0")
    second = simple_pdf("Bring a court order.", producer="Beta 9.9")

    first_hash, first_text = content_hash(first, "application/pdf")
    second_hash, second_text = content_hash(second, "application/pdf")

    assert first_text == second_text == "bring a court order."
    assert first_hash != second_hash
