"""Content normalization and hashing — the layer that decides what counts as "changed".

Adapted from an earlier content-hash watcher, which established the approach:
government pages churn *markup* far more than they churn
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

Non-text content is hashed as *raw bytes*, without lossy normalization. **Detection stays
there even for a PDF we can read.** Many states publish the operative instructions only as
a PDF, and `core/pdf.py` now extracts the page text of the subset it can read completely —
but that text is a *diff surface*, not a detection surface. The hash still covers the whole
file, so a change in bytes the extractor does not read (an annotation, an image, a font
subset) still produces a change record. Hashing the extracted text instead would have made
every such change invisible, which is the wrong "no change" the risk register puts first.
A PDF the extractor refuses is exactly where it was before: hashed losslessly, carrying no
text, and honest about it. Saying "this PDF changed, go look" is honest; pretending to diff
bytes we could not read is not.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass

from id_churn_sentinel.core.pdf import (
    PDF_EXTRACTOR_VERSION,
    extract_pdf_text,
    looks_like_pdf,
)

__all__ = [
    "CURRENT_CONTRACT",
    "EXTRACTION_OUTCOMES",
    "EXTRACTION_OUTCOME_BINARY_OPAQUE",
    "EXTRACTION_OUTCOME_PDF_REFUSED",
    "EXTRACTION_OUTCOME_PDF_TEXT",
    "EXTRACTION_OUTCOME_TEXT",
    "EXTRACTOR_VERSION",
    "NORMALIZER_VERSION",
    "UNRECORDED_CONTRACT",
    "ContentEvidence",
    "ContentKind",
    "content_evidence",
    "content_hash",
    "excerpt",
    "kind_for_content_type",
    "normalize_html",
    "normalize_text",
    "page_title",
    "passages",
    "representation_contract",
]

# These values are persisted with every new snapshot. Changing normalization without
# changing the version would make two identical-looking hashes mean different things, so
# version bumps are part of the evidence contract rather than package-release bookkeeping.
#
# v2 (2026-08-01): script/style/title end tags now match the loose spellings HTML allows —
# trailing whitespace and even attributes before the `>` (`</script >`, `</script foo="1">`).
# A page using any of those had its element bodies hashed as page text under v1, so the same
# bytes hash differently under v2. That is exactly the "two identical-looking hashes mean
# different things" case this version string exists to prevent, hence the bump rather than a
# silent fix. Operator note: the first v2 pass over a v1 corpus does NOT report drift. The
# detector re-derives each v1 baseline from its retained bytes before comparing (see
# `core/detect.py`), so both sides of every comparison come from this normalizer — a version
# bump cannot manufacture a change record, and cannot suppress one either. `sentinel baseline
# check`, which holds hashes and no bytes, cannot re-derive and instead labels the affected
# comparisons; refresh it with `sentinel watch && sentinel baseline write`.
NORMALIZER_VERSION = "passage-text-v2"

# Which extractor produced the text a hash was taken over. `none-v1` (the alpha) meant "no
# extractor exists, so a non-text body is opaque bytes"; `pdf-text-v1` means "the bounded PDF
# extractor in `core/pdf.py` was available, and either read a PDF completely or refused it by
# name". The value is persisted, and it is the reason an old raw-byte hash can never be
# mistaken for one taken over extracted text. Widening the PDF subset changes what the same
# bytes extract to, so it is a version bump, not an edit.
EXTRACTOR_VERSION = PDF_EXTRACTOR_VERSION


def representation_contract(normalizer_version: str, extractor_version: str) -> str:
    """The `(normalizer, extractor)` pair as one operator-readable token.

    The pair travels together everywhere because it *is* one fact: what a stored hash was
    computed over. The store's `representation_contracts` table keys on both columns, and a
    change to either one changes the bytes hashed, so neither half means anything alone.
    """
    return f"{normalizer_version}/{extractor_version}"


# What every hash this build computes is computed under, and the only contract it *can*
# compute one under: exactly one normalizer exists in the tree at a time, by design. Old
# normalizers are not kept around and re-runnable, which is why retained *bytes* — not an
# old code path — are what make an old baseline recoverable.
CURRENT_CONTRACT = representation_contract(NORMALIZER_VERSION, EXTRACTOR_VERSION)

# A hash whose contract was never written down. Not the same as a known-old contract: with a
# named contract we know the comparison is invalid, and with this one we only know we cannot
# tell. Both are refusals to assume, and the second is the weaker claim, so it is named
# separately rather than folded in.
UNRECORDED_CONTRACT = "unrecorded"

# Block-level elements whose boundaries are real passage boundaries in the rendered page.
# Everything else (span, a, em, b, ...) is inline and collapses to a space, exactly as in
# the TS original.
_BLOCK_TAGS = (
    "address|article|aside|blockquote|br|dd|div|dl|dt|fieldset|figcaption|figure|footer"
    "|form|h1|h2|h3|h4|h5|h6|header|hr|legend|li|main|nav|ol|p|pre|section|table|tbody"
    "|td|tfoot|th|thead|tr|ul"
)

# The `\b[^>]*` in each end tag is load-bearing, not defensive noise. HTML's end-tag grammar
# allows trailing whitespace and even attributes before the `>` — `</script >`, `</style\n>`,
# `</script foo="bar">` — and while attributes on an end tag are a parse error, every browser
# still closes the element. Real pages ship all three spellings. Matching only the tight
# `</script>` means the element never matches, so the *body* survives into the passage text,
# and a page's minified JavaScript — cache-busting build ids, CSRF tokens, timestamps, all of
# which re-roll on every request — lands in the content hash. That is precisely the
# permanent-false-alarm failure this module exists to prevent, and it fails silently: the page
# reads fine, the hash just never settles. `\b` keeps this honest in the other direction too —
# it stops `</scriptfoo>`, a different tag entirely, from closing a `<script>`.
_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script\b[^>]*>", re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[\s\S]*?</style\b[^>]*>", re.IGNORECASE)
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


_TITLE_RE = re.compile(r"<title[^>]*>([\s\S]*?)</title\b[^>]*>", re.IGNORECASE)


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

    For HTML/text the hash covers the *normalized* text, which is the whole point: the hash
    and the diff cannot disagree about what the content was.

    For binary content the hash covers the **raw bytes** — lossless — and the text is
    whatever `core/pdf.py` could read completely, or empty when it read nothing. The hash
    deliberately does not follow the text here, and the asymmetry is the safety property:
    the bytes strictly contain the extracted text, so text cannot change without the hash
    changing, while a change the extractor cannot see still moves the hash. Hashing the
    extracted text would have inverted that and made every unreadable change a silent
    "no change".
    """
    kind = kind_for_content_type(content_type)
    if kind == ContentKind.BINARY:
        return hashlib.sha256(body).hexdigest(), _extracted_text(body)[0]

    decoded = body.decode("utf-8", errors="replace")
    normalized = normalize_html(decoded) if kind == ContentKind.HTML else normalize_text(decoded)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized


def _extracted_text(body: bytes) -> tuple[str, str]:
    """``(normalized_text, refusal_reason)`` for opaque bytes. One of the two is always empty.

    Non-PDF binaries are not attempted at all — there is genuinely no extractor for a `.docx`
    — and are reported with an empty reason so they are never confused with a PDF this build
    tried to read and would not stand behind.
    """
    if not looks_like_pdf(body):
        return "", ""
    extraction = extract_pdf_text(body)
    if not extraction.extracted:
        return "", extraction.refusal
    return normalize_text(extraction.text), ""


# What extraction actually happened to a body, as a closed vocabulary (`DATA-04`).
#
# The first two are the alpha's, and their meanings are frozen: `text-normalized` is
# text/HTML normalized to passages, and `binary-no-extractor` is a body hashed as opaque
# bytes because no extractor for it exists — which is still true of every non-PDF binary.
#
# `PDF-01` adds two rather than reusing either, because "we did not try" and "we tried and
# would not stand behind the result" are different facts about the evidence, and a reader six
# months from now cannot recover the difference from a value that covers both. Nor may either
# new value be folded into `binary-no-extractor`: a store holding rows written before this
# build must keep meaning what it meant when it was written.
EXTRACTION_OUTCOME_TEXT = "text-normalized"
EXTRACTION_OUTCOME_BINARY_OPAQUE = "binary-no-extractor"
EXTRACTION_OUTCOME_PDF_TEXT = "pdf-text-extracted"
EXTRACTION_OUTCOME_PDF_REFUSED = "pdf-extraction-refused"
EXTRACTION_OUTCOMES = frozenset(
    {
        EXTRACTION_OUTCOME_TEXT,
        EXTRACTION_OUTCOME_BINARY_OPAQUE,
        EXTRACTION_OUTCOME_PDF_TEXT,
        EXTRACTION_OUTCOME_PDF_REFUSED,
    }
)


@dataclass(frozen=True, slots=True)
class ContentEvidence:
    """The complete evidentiary description of one fetched body (`DATA-04`/`DET-01`).

    ``detection_sha256`` is byte-for-byte the hash :func:`content_hash` returns — the one
    the detector compares against baselines. It is carried here *by equality, tested*, so
    evidence and detection can never quietly diverge.

    Three shapes, and the third is the new one:

    * **text/HTML** — ``detection_sha256 == normalized_sha256``; the hash is over the text.
    * **binary this build did not read** — ``detection_sha256 == raw_sha256`` and
      ``normalized_sha256`` is empty, because claiming a "normalized-text hash" for bytes
      that produced no text would be fabricated provenance.
    * **a PDF this build read completely** — ``detection_sha256 == raw_sha256`` *and*
      ``normalized_sha256`` is a real hash of the extracted text. Both are true at once
      and neither is redundant: detection is over the whole file, and the normalized hash
      records what the diff was computed from. They are different questions, so they get
      different fields rather than one field with a footnote.

    ``extraction_detail`` names *why* a PDF was not read (`encrypted`,
    `unsupported-filter/LZWDecode`, …) and is empty otherwise. It is evidence, not a log
    line: the population of documents this extractor refuses is what would justify widening
    it, and an unrecorded refusal is an unmeasurable one.
    """

    raw_sha256: str
    normalized_sha256: str
    detection_sha256: str
    normalized_text: str
    extraction_outcome: str
    extraction_detail: str = ""


def content_evidence(body: bytes, content_type: str | None) -> ContentEvidence:
    """Distinct raw/normalized hashes plus the extraction outcome for one fetched body."""
    raw_sha256 = hashlib.sha256(body).hexdigest()
    if kind_for_content_type(content_type) == ContentKind.BINARY:
        # Extracted once, here, and the detection hash restated rather than recomputed
        # through `content_hash` — running a whole PDF parse twice per fetch to reach the
        # same answer is a cost an unattended weekly job should not pay.
        normalized_text, refusal = _extracted_text(body)
        detection_sha256 = raw_sha256
        if not normalized_text:
            return ContentEvidence(
                raw_sha256=raw_sha256,
                normalized_sha256="",
                detection_sha256=detection_sha256,
                normalized_text="",
                extraction_outcome=(
                    EXTRACTION_OUTCOME_PDF_REFUSED if refusal else EXTRACTION_OUTCOME_BINARY_OPAQUE
                ),
                extraction_detail=refusal,
            )
        return ContentEvidence(
            raw_sha256=raw_sha256,
            normalized_sha256=hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
            detection_sha256=detection_sha256,
            normalized_text=normalized_text,
            extraction_outcome=EXTRACTION_OUTCOME_PDF_TEXT,
        )
    text_detection_sha256, text = content_hash(body, content_type)
    return ContentEvidence(
        raw_sha256=raw_sha256,
        normalized_sha256=text_detection_sha256,
        detection_sha256=text_detection_sha256,
        normalized_text=text,
        extraction_outcome=EXTRACTION_OUTCOME_TEXT,
    )
