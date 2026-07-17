"""Content normalization and hashing — the layer that decides what counts as "changed".

Ported from the prior art in `trans-docs-navigator/scripts/source-watch.ts`, which
established the approach: government pages churn *markup* far more than they churn
*text* (a rotating CSRF token, a "last reviewed" widget, a re-minified stylesheet),
so hashing the raw bytes of an HTML page produces drift alerts that mean nothing.
Strip script/style/comments/tags/entities, collapse whitespace, lowercase, then hash.

**One deliberate divergence from the TypeScript original.** That script collapses the
page to a *single* line, because all it ever needed was a hash — it answers "did
something change?" and stops there. This tool has to answer "*what* changed?", and a
unified diff of one 40,000-character line is not an answer. So normalization here
preserves *passage boundaries*: block-level tags become newlines, and only intra-line
whitespace is collapsed. The result is a list of passages that `difflib` can diff and a
human can read. The hash is taken over that same passage-segmented text, so the hash and
the diff are always derived from exactly the same bytes — a hash change can never be
reported without a diff being computable from the same normalization.

Non-text content (PDFs — many states publish the operative instructions only as a PDF)
is hashed as *raw bytes*, without lossy normalization, and carries no text diff. Saying
"this PDF changed, go look" is honest; pretending to diff bytes we cannot read is not.
"""

from __future__ import annotations

import hashlib
import html
import re

__all__ = [
    "EXTRACTOR_VERSION",
    "NORMALIZER_VERSION",
    "ContentKind",
    "content_hash",
    "excerpt",
    "kind_for_content_type",
    "normalize_html",
    "normalize_text",
    "page_title",
    "passages",
]

# These values are persisted with every new snapshot. Changing normalization without
# changing the version would make two identical-looking hashes mean different things, so
# version bumps are part of the evidence contract rather than package-release bookkeeping.
NORMALIZER_VERSION = "passage-text-v1"

# Binary extraction is intentionally not implemented in the alpha. Persisting that fact is
# still provenance: a future PDF extractor must never make an old raw-byte hash look as if it
# came from extracted text.
EXTRACTOR_VERSION = "none-v1"

# Block-level elements whose boundaries are real passage boundaries in the rendered page.
# Everything else (span, a, em, b, ...) is inline and collapses to a space, exactly as in
# the TS original.
_BLOCK_TAGS = (
    "address|article|aside|blockquote|br|dd|div|dl|dt|fieldset|figcaption|figure|footer"
    "|form|h1|h2|h3|h4|h5|h6|header|hr|legend|li|main|nav|ol|p|pre|section|table|tbody"
    "|td|tfoot|th|thead|tr|ul"
)

_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_BLOCK_RE = re.compile(rf"</?(?:{_BLOCK_TAGS})\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[^\S\n]+")  # whitespace that is not a newline
_BLANK_LINES_RE = re.compile(r"\n{2,}")


class ContentKind:
    """The three ways content can be hashed. A plain namespace, not an enum: these are
    routing hints derived from a `Content-Type` header, not a domain concept."""

    HTML = "html"
    TEXT = "text"
    BINARY = "binary"


def kind_for_content_type(content_type: str | None) -> str:
    """Route a `Content-Type` header to a normalization strategy.

    Unknown or absent types are treated as binary. That is the safe default: a bad guess
    toward binary produces a coarse, honest "the bytes changed"; a bad guess toward HTML
    would silently strip real content out of the hash and could hide a change.
    """
    ct = (content_type or "").lower()
    if "html" in ct or "xml" in ct:
        return ContentKind.HTML
    if ct.startswith("text/") or "json" in ct:
        return ContentKind.TEXT
    return ContentKind.BINARY


def normalize_html(source: str) -> str:
    """Strip markup down to readable passages, one per line, lowercased.

    Order matters: script/style/comment bodies go first (their *contents* are not page
    text), then block tags become newlines, then the remaining inline tags become spaces,
    then entities are resolved.
    """
    text = _SCRIPT_RE.sub(" ", source)
    text = _STYLE_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    # Resolve entities rather than blanking them (the TS original replaced them with a
    # space, which was fine for a hash but mangles the excerpt a human has to read:
    # "don&rsquo;t" should diff as "don't", not "don t").
    text = html.unescape(text)
    return normalize_text(text)


def normalize_text(source: str) -> str:
    """Collapse intra-line whitespace, drop blank lines, lowercase, strip.

    Non-breaking spaces and other unicode whitespace are folded into ordinary spaces by
    ``\\s``-class matching, so a page that swaps `&nbsp;` for a space does not read as
    a content change.
    """
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    text = _INLINE_WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES_RE.sub("\n", text)
    return text.strip().lower()


def passages(normalized: str) -> list[str]:
    """The diffable unit: one passage per line. Empty input yields no passages."""
    return [line for line in normalized.split("\n") if line]


_TITLE_RE = re.compile(r"<title[^>]*>([\s\S]*?)</title>", re.IGNORECASE)


def page_title(body: bytes) -> str:
    """The page's own `<title>`, as the *page* states it — not as we hope it reads.

    This is the single most useful string for a human verifying a registry entry: a page
    titled "Office of Vital Statistics | Kansas Department of Health and Environment" is
    evidence; a page titled "404 Page Not Found" served with HTTP 200 (which is what
    `courts.oregon.gov` does) or "Request Access" (which is what `ecfr.gov` does) is the
    trap this string exists to expose. Returned verbatim apart from whitespace collapsing —
    we do not "clean it up", because the mess IS the signal.
    """
    match = _TITLE_RE.search(body.decode("utf-8", errors="replace"))
    if not match:
        return ""
    title = html.unescape(_TAG_RE.sub(" ", match.group(1)))
    return re.sub(r"\s+", " ", title).strip()


def excerpt(normalized: str, *, max_passages: int = 12, max_chars: int = 900) -> str:
    """The first few passages of normalized text — what a verifier actually reads.

    Bounded twice (passages *and* characters) because the whole point is that a human can
    read it in seconds; an "excerpt" that fills a terminal is one a tired reviewer scrolls
    past, and a reviewer who scrolls past the evidence is rubber-stamping.
    """
    kept: list[str] = []
    budget = max_chars
    for line in passages(normalized)[:max_passages]:
        if budget <= 0:
            break
        kept.append(line[:budget])
        budget -= len(line)
    return "\n".join(kept)


def content_hash(body: bytes, content_type: str | None) -> tuple[str, str]:
    """Return ``(sha256_hex, normalized_text)`` for a fetched body.

    For binary content the normalized text is empty and the hash covers the raw bytes —
    lossless, but undiffable. For HTML/text the hash covers the *normalized* text, which
    is the whole point: the hash and the diff cannot disagree about what the content was.
    """
    kind = kind_for_content_type(content_type)
    if kind == ContentKind.BINARY:
        return hashlib.sha256(body).hexdigest(), ""

    decoded = body.decode("utf-8", errors="replace")
    normalized = normalize_html(decoded) if kind == ContentKind.HTML else normalize_text(decoded)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized
