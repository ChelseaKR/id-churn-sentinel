"""Tests for :mod:`id_churn_sentinel.core.normalize`.

The load-bearing test in this file is `test_cosmetic_markup_churn_is_not_a_content_change`.
Everything else is detail. If normalization reports a re-minified stylesheet or a rotated
CSRF token as drift, every reviewer learns within two weeks that the feed is noise, and the
tool is dead — not because it missed a change, but because nobody reads it any more.
"""

from __future__ import annotations

from id_churn_sentinel.core.normalize import (
    ContentKind,
    content_hash,
    kind_for_content_type,
    normalize_html,
    normalize_text,
    passages,
)


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


def test_binary_content_is_hashed_as_raw_bytes_with_no_text() -> None:
    """A PDF cannot be diffed, and pretending otherwise would be a lie a reviewer would act
    on. Hash the bytes losslessly; return no text."""
    digest, text = content_hash(b"%PDF-1.7 binary\x00\xff", "application/pdf")
    assert text == ""
    assert len(digest) == 64


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
