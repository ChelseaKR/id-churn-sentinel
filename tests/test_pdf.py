"""Tests for :mod:`id_churn_sentinel.core.pdf` — the bounded PDF extractor (`PDF-01`).

The load-bearing tests in this file are the *refusals*. Extraction that works is worth
having; extraction that quietly works **partially** is worse than none, because it hands a
reviewer a confident text diff with the changed half missing and they dismiss a page that
did change. So most of what is asserted here is that a document with one construct this
subset cannot read comes back with nothing and a reason, rather than with the rest of the
page.

Every fixture is built in-process. The suite stays air-gapped, and — more usefully — each
document differs from the readable one by exactly the construct under test, which a
directory of near-identical binary blobs would hide.
"""

from __future__ import annotations

import zlib

import pytest

import id_churn_sentinel.core.pdf as pdf_module
from id_churn_sentinel.core.pdf import PDF_EXTRACTOR_VERSION, extract_pdf_text, looks_like_pdf

from .conftest import _pdf_document, _pdf_object, _pdf_stream, content_stream_for, simple_pdf

_WINANSI_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"


def test_a_readable_pdf_yields_its_page_text_in_order() -> None:
    text = extract_pdf_text(simple_pdf("Section 1. Applicant\nSection 2. Amendment"))
    assert text.extracted
    assert text.text.splitlines() == ["Section 1. Applicant", "Section 2. Amendment"]
    assert text.refusal == ""


def test_extraction_is_a_pure_function_of_the_bytes() -> None:
    """Determinism is not a nicety here: a diff is only evidence if re-running the extractor
    on the retained bytes reproduces the left-hand side exactly. Anything order-dependent —
    a set iteration, a dict of pages — would make last week's diff unreproducible six months
    later, which is precisely the property `sentinel diff` promises."""
    body = simple_pdf("Bring a certified court order.\nFee: $11.")
    assert extract_pdf_text(body) == extract_pdf_text(body)


def test_a_re_render_and_a_real_edit_are_distinguishable() -> None:
    """The whole point of the issue, at the extractor level. Two documents that differ only
    in a producer string extract identically; one whose sentence changed does not."""
    original = extract_pdf_text(simple_pdf("A court order is required."))
    re_render = extract_pdf_text(simple_pdf("A court order is required.", producer="Acrobat 9"))
    edited = extract_pdf_text(simple_pdf("A certified court order is required."))

    assert original.text == re_render.text
    assert original.text != edited.text


def test_pages_come_out_in_page_tree_order_not_object_order() -> None:
    """A generator is free to write object 7 before object 4. Page order is what the `/Kids`
    array says, and a reviewer reading a diff of a two-page form needs it to be the order the
    form is printed in."""
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [5 0 R 3 0 R] /Count 2 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 9 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content_stream_for(["second page"])),
            _pdf_object(
                5,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 9 0 R >> >>"
                b" /Contents 6 0 R >>",
            ),
            _pdf_stream(6, b"", content_stream_for(["first page"])),
            _pdf_object(
                9,
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                b" /Encoding /WinAnsiEncoding >>",
            ),
        ]
    )
    assert extract_pdf_text(body).text.split() == ["first", "page", "second", "page"]


def test_resources_are_inherited_from_the_page_tree() -> None:
    """A page with no `/Resources` of its own inherits its parent's. Refusing it as
    `unknown-font-resource` would refuse a document that is entirely well-formed."""
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(
                2,
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << /Font << /F1 5 0 R >> >> >>",
            ),
            _pdf_object(3, b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>"),
            _pdf_stream(4, b"", content_stream_for(["inherited resources"])),
            _pdf_object(
                5,
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                b" /Encoding /WinAnsiEncoding >>",
            ),
        ]
    )
    assert extract_pdf_text(body).text.strip() == "inherited resources"


def test_a_flate_compressed_content_stream_is_read() -> None:
    """Every PDF a modern generator emits compresses its content streams. An extractor that
    only read uncompressed ones would refuse essentially the whole real corpus."""
    payload = zlib.compress(content_stream_for(["compressed page text"]))
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"/Filter /FlateDecode", payload),
            _pdf_object(
                5,
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                b" /Encoding /WinAnsiEncoding >>",
            ),
        ]
    )
    assert extract_pdf_text(body).text.strip() == "compressed page text"


def test_text_inside_a_form_xobject_is_read_and_not_skipped() -> None:
    """The one omission that would be invisible and catastrophic.

    Generators routinely put a page's body inside a Form XObject and call it with `Do`. An
    extractor that ignored `Do` would return the page furniture, miss the content entirely,
    and present the result as the page — a complete extraction, by its own account, of half
    a form. So the walker recurses, and this test is what says it still does."""
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >>"
                b" /XObject << /X1 6 0 R >> >> /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content_stream_for(["page heading"]) + b"\n/X1 Do"),
            _pdf_object(
                5,
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                b" /Encoding /WinAnsiEncoding >>",
            ),
            _pdf_stream(
                6,
                b"/Type /XObject /Subtype /Form /Resources << /Font << /F1 5 0 R >> >>",
                content_stream_for(["a court order is required"]),
            ),
        ]
    )
    extraction = extract_pdf_text(body)
    assert "page heading" in extraction.text
    assert "a court order is required" in extraction.text


def test_a_tounicode_cmap_is_used_in_preference_to_any_guess() -> None:
    """A Type0/Identity-H font — what Acrobat emits for a subset font — has codes that mean
    nothing without the document's own `/ToUnicode` table. Reading that table is the only
    honest way to decode them."""
    cmap = b"""/CIDInit /ProcSet findresource begin
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
2 beginbfchar
<0003> <0048>
<0004> <0069>
endbfchar
1 beginbfrange
<0010> <0012> <0041>
endbfrange
end"""
    content = b"BT /F1 12 Tf 72 720 Td <0003000400100011> Tj ET"
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content),
            _pdf_object(
                5,
                b"<< /Type /Font /Subtype /Type0 /BaseFont /AAAAAA+Helvetica"
                b" /Encoding /Identity-H /ToUnicode 6 0 R >>",
            ),
            _pdf_stream(6, b"", cmap),
        ]
    )
    assert extract_pdf_text(body).text.strip() == "HiAB"


def test_a_tj_array_gap_becomes_a_space_and_never_drops_a_character() -> None:
    content = b"BT /F1 12 Tf 72 720 Td [(court) -400 (order)] TJ ET"
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content),
            _pdf_object(
                5,
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                b" /Encoding /WinAnsiEncoding >>",
            ),
        ]
    )
    assert extract_pdf_text(body).text.strip() == "court order"


# ---------------------------------------------------------------------------------------
# Refusals: the half of this module that keeps a wrong diff from ever being written
# ---------------------------------------------------------------------------------------


def test_a_document_that_is_not_a_pdf_is_refused_rather_than_parsed() -> None:
    assert extract_pdf_text(b"<html><p>hello</p></html>").refusal == "not-a-pdf"
    assert not looks_like_pdf(b"PK\x03\x04")


def test_an_encrypted_document_is_refused() -> None:
    """Encryption reaches strings and streams, so every character on the page is ciphertext
    to this parser. There is no partial answer available and none is invented."""
    extraction = extract_pdf_text(simple_pdf(encrypted=True))
    assert extraction.refusal == "encrypted"
    assert extraction.text == ""


def test_a_duplicated_object_definition_is_refused_rather_than_guessed() -> None:
    """An incrementally-updated PDF defines an object twice and only its cross-reference
    table says which one is current. Taking "the last one" is right most of the time, and
    the times it is wrong produce a diff against a superseded revision of the form — a
    confident, plausible, entirely fabricated finding. Refused instead, by name, so the
    population it costs us is countable."""
    assert extract_pdf_text(simple_pdf(duplicate_object=True)).refusal == (
        "duplicate-object-definition"
    )


def test_an_unsupported_filter_is_refused_by_name() -> None:
    extraction = extract_pdf_text(simple_pdf(unsupported_filter=True))
    assert extraction.refusal == "unsupported-filter/LZWDecode"


def test_a_type3_font_is_refused() -> None:
    """A Type 3 font draws each glyph with its own content stream, so a code has no
    character until somebody decides what the drawing depicts. Nobody here is qualified."""
    assert extract_pdf_text(simple_pdf(subtype=b"/Type3")).refusal == "unsupported-font/Type3"


def test_an_encoding_with_differences_is_refused() -> None:
    """`/Differences` remaps codes to glyph *names*, and a name becomes a character only
    through the Adobe Glyph List. That table is not carried, so it is not guessed at."""
    extraction = extract_pdf_text(simple_pdf(encoding=b"<< /Differences [65 /eacute] >>"))
    assert extraction.refusal == "unsupported-encoding/Differences"


def test_a_font_with_only_its_builtin_encoding_is_refused() -> None:
    """No `/Encoding` means "whatever is inside the embedded font program". For a
    standard-14 text face that is very probably StandardEncoding, and for a symbolic face it
    is very probably not. "Very probably" is not a basis for publishing a diff."""
    body = simple_pdf().replace(b" /Encoding /WinAnsiEncoding", b"")
    assert extract_pdf_text(body).refusal == "unsupported-encoding/builtin"


def test_a_composite_font_without_a_tounicode_map_is_refused() -> None:
    body = simple_pdf(subtype=b"/Type0", encoding=b"/Identity-H")
    assert extract_pdf_text(body).refusal == "composite-font-without-tounicode"


def test_a_code_the_font_map_does_not_cover_refuses_the_whole_document() -> None:
    """The single most important refusal in the file.

    A `/ToUnicode` map that covers three of the four codes on the page would let a
    best-effort extractor emit three characters and drop one — the exact partial extraction
    this module exists to never produce. One unmappable code refuses the document, and the
    reviewer keeps the honest "the bytes changed, go and look"."""
    cmap = b"""1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfchar
<0003> <0048>
endbfchar"""
    content = b"BT /F1 12 Tf 72 720 Td <00030099> Tj ET"
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content),
            _pdf_object(
                5,
                b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H"
                b" /ToUnicode 6 0 R >>",
            ),
            _pdf_stream(6, b"", cmap),
        ]
    )
    extraction = extract_pdf_text(body)
    assert extraction.refusal == "unmappable-character-code"
    assert extraction.text == ""  # nothing partial escapes


def test_text_shown_in_a_font_the_page_never_declared_is_refused() -> None:
    """Not a hypothetical tidiness check: the codes are meaningless without a map, so
    emitting them decoded as anything at all would be inventing page text."""
    body = simple_pdf().replace(b"/F1 12 Tf", b"/F9 12 Tf")
    assert extract_pdf_text(body).refusal == "unknown-font-resource"


def test_a_document_with_no_readable_text_is_refused_rather_than_called_empty() -> None:
    """A scanned form extracts to nothing. Returning "" would let the caller diff empty
    against empty next week and print "no text difference" about a document nobody read."""
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(3, b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>"),
            _pdf_stream(4, b"", b"q 612 0 0 792 0 0 cm /Im1 Do Q"),
        ]
    )
    assert extract_pdf_text(body).refusal in {"no-extractable-text", "unknown-xobject-resource"}


def test_a_document_with_no_catalog_is_refused() -> None:
    assert extract_pdf_text(b"%PDF-1.4\ntrailer\n<< >>\n%%EOF").refusal == "no-catalog"


def test_a_page_tree_cycle_is_refused_rather_than_walked_forever() -> None:
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(3, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        ]
    )
    assert extract_pdf_text(body).refusal == "page-tree-cycle"


def test_a_truncated_document_is_refused_and_never_crashes() -> None:
    """A government server that answers a range request badly, or a body cut off at the
    fetcher's size bound, must degrade to a refusal — never to a traceback that fails a
    whole weekly run for every other source in the pass."""
    full = simple_pdf("Bring a court order.")
    for cut in range(8, len(full), 37):
        extraction = extract_pdf_text(full[:cut])
        assert extraction.text == "" or extraction.extracted


def test_the_extractor_version_is_the_one_persisted_with_every_hash() -> None:
    """The version is half of the representation contract, so it may not drift from what the
    normalizer records — a hash whose contract names an extractor that never produced it is
    a hash nobody can re-derive."""
    from id_churn_sentinel.core.normalize import EXTRACTOR_VERSION

    assert EXTRACTOR_VERSION == PDF_EXTRACTOR_VERSION == "pdf-text-v1"


# ---------------------------------------------------------------------------------------
# Object syntax, page structure, and the rest of the refusal surface
# ---------------------------------------------------------------------------------------


def _one_page(content: bytes, *, font: bytes = _WINANSI_FONT, extra: bytes = b"") -> bytes:
    """A minimal one-page document wrapping `content`, so a test differs by one construct."""
    return _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >>"
                + extra
                + b" >> /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content),
            _pdf_object(5, font),
        ]
    )


def _cmap(body: bytes, *, codespace: bytes = b"<0000> <FFFF>") -> bytes:
    return b"1 begincodespacerange\n" + codespace + b"\nendcodespacerange\n" + body


def _type0_with_cmap(content: bytes, cmap: bytes) -> bytes:
    return _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content),
            _pdf_object(
                5,
                b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H"
                b" /ToUnicode 6 0 R >>",
            ),
            _pdf_stream(6, b"", cmap),
        ]
    )


def test_escapes_and_octal_and_line_continuations() -> None:
    """Every escape form a generator emits, because each one that is mishandled is a
    character that comes out wrong in a diff a reviewer trusts."""
    content = b"BT /F1 12 Tf 72 720 Td (par\\(en\\) back\\\\slash oct\\101 cont\\\ninued) Tj ET"
    assert extract_pdf_text(_one_page(content)).text.strip() == (
        "par(en) back\\slash octA continued"
    )


def test_a_comment_inside_object_syntax_is_skipped() -> None:
    body = _one_page(b"BT /F1 12 Tf 72 720 Td (text) Tj ET").replace(
        b"<< /Type /Catalog", b"% a generator comment\n<< /Type /Catalog"
    )
    assert extract_pdf_text(body).text.strip() == "text"


def test_a_name_with_a_hex_escape_resolves_to_the_same_name() -> None:
    """`/F#31` is `/F1`. A parser that read them as different names would refuse a document
    whose font resource is perfectly well declared."""
    body = _one_page(b"BT /F#31 12 Tf 72 720 Td (text) Tj ET")
    assert extract_pdf_text(body).text.strip() == "text"


def test_a_contents_array_is_concatenated_and_a_page_with_none_contributes_nothing() -> None:
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R 7 0 R] /Count 2 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents [4 0 R 6 0 R] >>",
            ),
            _pdf_stream(4, b"", b"BT /F1 12 Tf 72 720 Td (first half) Tj"),
            _pdf_object(5, _WINANSI_FONT),
            _pdf_stream(6, b"", b"0 -14 Td (second half) Tj ET"),
            _pdf_object(7, b"<< /Type /Page /Parent 2 0 R >>"),
        ]
    )
    extracted = extract_pdf_text(body).text
    assert "first half" in extracted
    assert "second half" in extracted


def test_a_stream_introduced_with_crlf_is_read() -> None:
    body = _one_page(b"BT /F1 12 Tf 72 720 Td (text) Tj ET").replace(b"\nstream\n", b"\nstream\r\n")
    assert extract_pdf_text(body).text.strip() == "text"


def test_an_inline_image_is_stepped_over_rather_than_tokenized() -> None:
    """`BI … ID <arbitrary bytes> EI` embeds raw image data in the middle of a content
    stream. Tokenizing it would read pixel bytes as operators."""
    content = (
        b"BT /F1 12 Tf 72 720 Td (before) Tj ET\n"
        b"BI /W 2 /H 2 /BPC 8 /CS /G ID \x00\xff(\\ Tj EI\n"
        b"BT /F1 12 Tf 72 700 Td (after) Tj ET"
    )
    extracted = extract_pdf_text(_one_page(content)).text
    assert "before" in extracted
    assert "after" in extracted


def test_the_line_operators_place_text_the_way_a_generator_writes_it() -> None:
    content = (
        b"BT /F1 12 Tf 14 TL 72 720 Td (line one) Tj T* (line two) ' 0 -14 TD (line three) Tj "
        b"0 0 Td (still three) Tj ET"
    )
    extracted = extract_pdf_text(_one_page(content)).text
    assert "line one" in extracted
    assert "line two" in extracted
    assert "line three" in extracted


def test_a_pdf_declaring_macroman_is_read_with_the_pdf_tables_one_divergence() -> None:
    """MacRomanEncoding is what every PDF produced through the macOS print pipeline
    declares, so refusing it would refuse a large slice of real government forms. The PDF
    table differs from Mac OS Roman in exactly one cell, and that cell is overridden rather
    than tolerated."""
    content = b"BT /F1 12 Tf 72 720 Td (fee \xdb11) Tj ET"
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /MacRomanEncoding >>"
    assert extract_pdf_text(_one_page(content, font=font)).text.strip() == "fee ¤11"


def test_objects_inside_a_compressed_object_stream_are_registered() -> None:
    """PDF 1.5 generators put the catalog and page tree inside a `/Type /ObjStm`. Refusing
    those would refuse most modern forms, which is a useless refusal rather than a safe one."""
    inner = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    ]
    numbers = [1, 2, 3]
    offsets: list[bytes] = []
    payload = b""
    for number, obj in zip(numbers, inner, strict=True):
        offsets.append(b"%d %d" % (number, len(payload)))
        payload += obj + b" "
    header = b" ".join(offsets) + b" "
    packed = zlib.compress(header + payload)
    body = _pdf_document(
        [
            _pdf_stream(
                9,
                b"/Type /ObjStm /N 3 /First %d /Filter /FlateDecode" % len(header),
                packed,
            ),
            _pdf_stream(4, b"", content_stream_for(["object stream page"])),
            _pdf_object(5, _WINANSI_FONT),
        ]
    )
    assert extract_pdf_text(body).text.strip() == "object stream page"


def test_a_tounicode_bfrange_array_form_is_read() -> None:
    cmap = _cmap(b"1 beginbfrange\n<0001> <0003> [<0041> <0042> <0043>]\nendbfrange")
    body = _type0_with_cmap(b"BT /F1 12 Tf 72 720 Td <000100020003> Tj ET", cmap)
    assert extract_pdf_text(body).text.strip() == "ABC"


def test_an_image_xobject_is_ignored_and_an_unknown_subtype_is_refused() -> None:
    image = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >>"
                b" /XObject << /X1 6 0 R >> >> /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", content_stream_for(["heading"]) + b"\n/X1 Do"),
            _pdf_object(5, _WINANSI_FONT),
            _pdf_stream(6, b"/Type /XObject /Subtype /Image /Width 1 /Height 1", b"\x00"),
        ]
    )
    assert extract_pdf_text(image).text.strip() == "heading"

    unknown = image.replace(b"/Subtype /Image /Width 1 /Height 1", b"/Subtype /PS")
    assert extract_pdf_text(unknown).refusal == "unsupported-xobject"


def test_bounds_refuse_rather_than_truncate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every bound in this module refuses. A bound that truncated would produce exactly the
    partial extraction the module exists to never produce, and would do it silently."""
    body = simple_pdf("Bring a court order.")

    monkeypatch.setattr(pdf_module, "MAX_OBJECTS", 1)
    assert extract_pdf_text(body).refusal == "too-many-objects"
    monkeypatch.undo()

    monkeypatch.setattr(pdf_module, "MAX_PAGES", 0)
    assert extract_pdf_text(body).refusal == "too-many-pages"
    monkeypatch.undo()

    monkeypatch.setattr(pdf_module, "MAX_TREE_DEPTH", 0)
    assert extract_pdf_text(body).refusal in {"page-tree-too-deep", "object-nesting-too-deep"}
    monkeypatch.undo()

    packed = zlib.compress(content_stream_for(["compressed"]) * 200)
    compressed = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"/Filter /FlateDecode", packed),
            _pdf_object(5, _WINANSI_FONT),
        ]
    )
    monkeypatch.setattr(pdf_module, "MAX_DECOMPRESSED_BYTES", 32)
    assert extract_pdf_text(compressed).refusal == "decompressed-stream-too-large"


def test_an_unexpected_exception_becomes_a_refusal_and_never_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A weekly run walks a hundred sources. A parser bug on one government form must cost
    that form's diff, not the whole pass — and it must never be reported as an extraction."""

    def explode(self: object) -> str:
        raise ValueError("synthetic parser defect")

    monkeypatch.setattr(pdf_module._Document, "page_text", explode)
    extraction = extract_pdf_text(simple_pdf("Bring a court order."))
    assert extraction.refusal == "malformed-pdf/ValueError"
    assert extraction.text == ""


def test_an_extraction_may_not_claim_both_text_and_a_refusal() -> None:
    """The type says "text or a refusal, never both", and the constructor enforces it, so a
    future caller cannot build the one object that would let a partial read look complete."""
    with pytest.raises(ValueError, match="either text or a refusal"):
        pdf_module.PdfExtraction(text="half a form", refusal="encrypted")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"%PDF-1.4\n" + _pdf_object(1, b"<< /Type /Pages /Kids [] >>"), "no-catalog"),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_object(2, b"<< /Type /Catalog /Pages 2 0 R >>"),
                ]
            ),
            "ambiguous-catalog",
        ),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_object(2, b"<< /Type /Pages /Kids 7 0 R /Count 1 >>"),
                    _pdf_object(7, b"<< /Type /Page >>"),
                ]
            ),
            "malformed-page-tree",
        ),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_object(2, b"<< /Type /Pages /Kids [9 0 R] /Count 1 >>"),
                ]
            ),
            "dangling-reference",
        ),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_object(2, b"[1 2 3]"),
                ]
            ),
            "expected-dictionary",
        ),
    ],
)
def test_structural_defects_are_refused_by_name(body: bytes, expected: str) -> None:
    assert extract_pdf_text(body).refusal == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"BT 72 720 Td (text) Tj ET", "text-shown-before-a-font-was-selected"),
        (b"BT /F1 12 Tf 72 720 Td <00GG> Tj ET", "malformed-hex-string"),
        (b"BT /F1 12 Tf 72 720 Td (unterminated Tj ET", "truncated-string"),
        (b"BT /F1 12 Tf 72 720 Td [(a) 1", "truncated-array"),
        (b"BT /F1 12 Tf 72 720 Td <0041 Tj ET", "truncated-hex-string"),
        (b"BT 12 Tf 72 720 Td (text) Tj ET", "malformed-font-selection"),
        (b"BT /F1 12 Tf 72 720 Td (text) Tj ET\n/X9 Do", "unknown-xobject-resource"),
    ],
)
def test_content_stream_defects_are_refused_by_name(content: bytes, expected: str) -> None:
    assert extract_pdf_text(_one_page(content)).refusal == expected


@pytest.mark.parametrize(
    ("cmap", "expected"),
    [
        (_cmap(b"", codespace=b"<00> <FF> <0000> <FFFF>"), "mixed-width-cmap"),
        (_cmap(b"", codespace=b"<000000> <FFFFFF>"), "unsupported-cmap-width/3"),
        (_cmap(b"1 beginbfchar\n<0001>\nendbfchar"), "malformed-bfchar"),
        (_cmap(b""), "empty-cmap"),
        (_cmap(b"1 beginbfrange\n<0003> <0001> <0041>\nendbfrange"), "malformed-bfrange"),
        (_cmap(b"1 beginbfrange\n<0001>\nendbfrange"), "malformed-bfrange"),
        (
            _cmap(b"1 beginbfrange\n<0001> <0003> [<0041>]\nendbfrange"),
            "malformed-bfrange",
        ),
    ],
)
def test_a_malformed_tounicode_cmap_is_refused_rather_than_partially_believed(
    cmap: bytes, expected: str
) -> None:
    """A CMap read half-right decodes half the page into plausible mojibake, which is the
    worst possible input to a diff a person is meant to act on."""
    body = _type0_with_cmap(b"BT /F1 12 Tf 72 720 Td <0001> Tj ET", cmap)
    assert extract_pdf_text(body).refusal == expected


@pytest.mark.parametrize(
    ("stream_dictionary", "expected"),
    [
        (b"/Filter [/FlateDecode /LZWDecode]", "unsupported-filter/LZWDecode"),
        (b"/Filter [42]", "malformed-filter"),
        (b"/Filter 42", "malformed-filter"),
        (
            b"/Filter /FlateDecode /DecodeParms << /Predictor 12 /Columns 4 >>",
            "unsupported-decode-predictor",
        ),
    ],
)
def test_stream_filter_defects_are_refused_by_name(stream_dictionary: bytes, expected: str) -> None:
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
            ),
            _pdf_stream(4, stream_dictionary, zlib.compress(content_stream_for(["x"]))),
            _pdf_object(5, _WINANSI_FONT),
        ]
    )
    assert extract_pdf_text(body).refusal == expected


def test_a_page_with_nothing_on_it_is_refused_rather_than_reported_as_empty() -> None:
    assert extract_pdf_text(_one_page(b"q Q")).refusal == "no-extractable-text"


def test_the_text_matrix_places_lines_the_way_a_real_generator_does() -> None:
    """`Tm` is how most generators position every line, so a line break that only worked for
    `Td` would read a real form as one run-on passage — and a passage-level diff of one
    40,000-character line is the thing this project's normalizer exists to avoid."""
    content = (
        b"BT /F1 12 Tf 1 0 0 1 72 720 Tm (line one) Tj "
        b"1 0 0 1 72 700 Tm (line two) Tj "
        b"1 0 0 1 200 700 Tm (same line) Tj ET"
    )
    extracted = extract_pdf_text(_one_page(content)).text
    assert "line one" in extracted.splitlines()[0]
    assert "line two same line" in extracted


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        # A two-byte CMap fed an odd number of bytes: the codes are misaligned, so every
        # character after the first would be read from the wrong byte pair.
        (b"BT /F1 12 Tf 72 720 Td <000101> Tj ET", "misaligned-character-code"),
    ],
)
def test_a_misaligned_character_code_is_refused(content: bytes, expected: str) -> None:
    cmap = _cmap(b"1 beginbfchar\n<0001> <0041>\nendbfchar")
    assert extract_pdf_text(_type0_with_cmap(content, cmap)).refusal == expected


def test_a_named_encoding_we_do_not_carry_is_refused_by_its_own_name() -> None:
    """Named, so that a real corpus tells the maintainer which table would be worth adding."""
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /X /Encoding /PDFDocEncoding >>"
    body = _one_page(b"BT /F1 12 Tf 72 720 Td (text) Tj ET", font=font)
    assert extract_pdf_text(body).refusal == "unsupported-encoding/PDFDocEncoding"


def test_a_base_encoding_inside_an_encoding_dictionary_is_honoured() -> None:
    font = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /X"
        b" /Encoding << /BaseEncoding /WinAnsiEncoding >> >>"
    )
    body = _one_page(b"BT /F1 12 Tf 72 720 Td (text) Tj ET", font=font)
    assert extract_pdf_text(body).text.strip() == "text"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_object(2, b"2 0 R"),
                ]
            ),
            "reference-cycle",
        ),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_object(2, b"<< /Type /Pages /Kids [] /Count 0 >>"),
                ]
            ),
            "no-pages",
        ),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_stream(2, b"/Type /ObjStm /First 4", b"data"),
                ]
            ),
            "malformed-object-stream",
        ),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_stream(2, b"/Type /ObjStm /N 2 /First 4", b"1 0 x"),
                ]
            ),
            "malformed-object-stream",
        ),
        (
            _pdf_document(
                [
                    _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                    _pdf_stream(2, b"/Type /ObjStm /N 1 /First 4", b"1 0 << >>"),
                ]
            ),
            "duplicate-object-definition",
        ),
    ],
)
def test_object_layer_defects_are_refused_by_name(body: bytes, expected: str) -> None:
    assert extract_pdf_text(body).refusal == expected


def test_an_unterminated_stream_or_inline_image_is_refused() -> None:
    truncated_stream = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nstream\nnever closed\n%%EOF"
    assert extract_pdf_text(truncated_stream).refusal == "unterminated-stream"

    unterminated_image = _one_page(b"BI /W 1 /H 1 ID \x00\x00 never closed")
    assert extract_pdf_text(unterminated_image).refusal == "unterminated-inline-image"


def test_deeply_nested_form_xobjects_are_refused_rather_than_recursed_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >>"
                b" /XObject << /X1 6 0 R >> >> /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", b"/X1 Do"),
            _pdf_object(5, _WINANSI_FONT),
            _pdf_stream(
                6,
                b"/Type /XObject /Subtype /Form"
                b" /Resources << /Font << /F1 5 0 R >> /XObject << /X1 6 0 R >> >>",
                b"/X1 Do",
            ),
        ]
    )
    monkeypatch.setattr(pdf_module, "MAX_FORM_DEPTH", 4)
    assert extract_pdf_text(body).refusal == "form-xobject-too-deep"


def test_an_xobject_resource_that_is_not_a_stream_is_refused() -> None:
    body = _pdf_document(
        [
            _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            _pdf_object(
                3,
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >>"
                b" /XObject << /X1 6 0 R >> >> /Contents 4 0 R >>",
            ),
            _pdf_stream(4, b"", b"/X1 Do"),
            _pdf_object(5, _WINANSI_FONT),
            _pdf_object(6, b"<< /Type /XObject /Subtype /Form >>"),
        ]
    )
    assert extract_pdf_text(body).refusal == "expected-stream"
