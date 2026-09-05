"""Bounded PDF text extraction (`PDF-01`) — deterministic, fail-closed, standard library only.

Many states publish the operative instructions only as a PDF, and until this module existed
a PDF source could report exactly one thing: *the bytes changed.* That is close to useless
to a reviewer, because the two cases they most need to tell apart are byte-identical in
their consequences and indistinguishable in the alert: a re-render that moved a build date
and a hairline, and a form that now demands a court order. Both arrive as "the bytes
changed. Open it yourself."

So this module extracts the page text, and the diff is computed over that. What it is
emphatically **not** allowed to do is guess.

**The safety rule, and every design decision below follows from it.** A normalizer that
hides real text can hide a real change (`docs/RESPONSIBLE-TECH-AUDITS.md` §A), and an
extractor is a normalizer with more moving parts. A partial extraction is the worst
possible artifact this code could produce: half a form, rendered as a confident text diff,
with the changed half silently absent — a reviewer reads "no change in the extracted text"
and dismisses a page that did change. So there is no partial extraction. Either every
glyph shown by every text operator on every page maps through a font map the document
itself supplies, or **the whole document is refused** and the caller falls back to the old,
honest "the bytes changed, go and look".

**Refusal is not failure.** It is the same answer the tool gave before this module existed,
and it costs a reviewer nothing they were not already paying. A wrong extraction costs them
the thing the whole project is for. Every refusal carries a machine-readable reason, so the
set of documents this subset cannot read is *measurable* rather than assumed — which is the
only honest basis for widening it later.

**Detection does not move.** The caller keeps hashing the raw bytes of a PDF (see
:func:`normalize.content_hash`). Extraction adds a *diff surface*, never a detection
surface. That ordering is deliberate and load-bearing: a change confined to bytes this
extractor does not read — an annotation appearance, an embedded image, a font subset — still
produces a change record, because the hash never stopped covering the whole file. Hashing
the extracted text instead would have made every such change invisible, which is the wrong
"no change" this project's risk register puts first.

**What this subset reads.** Unencrypted documents whose objects are each defined exactly
once; page content streams and Form XObjects, uncompressed or `FlateDecode`; text shown by
`Tj`, `TJ`, `'` and `"`; characters mapped either by the font's own `/ToUnicode` CMap or by
an explicitly declared `/WinAnsiEncoding` or `/MacRomanEncoding` with no `/Differences`.
Everything else — an
encrypted file, an incrementally-updated file, an unsupported filter, a Type 3 font, a font
with no usable map, a code the map does not cover — is refused by name.

**Determinism.** Extraction is a pure function of the bytes: no clock, no locale, no
network, no dictionary-ordering dependence (page order comes from the page tree, glyph order
from the content stream). Running it twice on one document yields the same string, which is
what makes an extracted-text diff evidence rather than an anecdote.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from typing import Final

__all__ = [
    "PDF_EXTRACTOR_VERSION",
    "PdfExtraction",
    "extract_pdf_text",
    "looks_like_pdf",
]

# The extractor half of the representation contract (see `normalize.representation_contract`).
# Frozen the moment it is persisted: if the subset below is ever widened, the same bytes may
# extract to a different string, and two hashes taken under two different meanings of
# "pdf-text-v1" would be silently incomparable. Widen the subset, bump the version.
PDF_EXTRACTOR_VERSION: Final = "pdf-text-v1"

# Bounds. An unattended weekly job must not be turnable into a decompression bomb or an
# unbounded walk by a government server that changed its PDF generator. Each bound refuses;
# none of them truncates, because a truncated extraction is exactly the partial artifact this
# module exists to never produce.
MAX_OBJECTS: Final = 100_000
MAX_DECOMPRESSED_BYTES: Final = 64 * 1024 * 1024
MAX_PAGES: Final = 5_000
MAX_TREE_DEPTH: Final = 64
MAX_FORM_DEPTH: Final = 16

# A `TJ` array's numbers move the pen without showing a glyph. A large enough negative move
# is how every PDF generator writes an inter-word space, so it is read as one. The threshold
# is in thousandths of an em and is a fixed constant rather than a measurement: it affects
# only *whitespace*, and `normalize.normalize_text` collapses whitespace before anything is
# hashed or diffed. Getting it wrong makes a passage uglier. It cannot drop a character.
TJ_WORD_GAP: Final = -100.0


@dataclass(frozen=True, slots=True)
class PdfExtraction:
    """The result of one extraction attempt: text, or a named refusal. Never both.

    `refusal` is a short machine-readable token (`encrypted`, `unsupported-filter/LZWDecode`,
    …). It is surfaced to the operator rather than swallowed, because the population of
    documents this subset cannot read is the evidence that decides whether widening the
    subset is worth doing — and which way.
    """

    text: str
    refusal: str

    @property
    def extracted(self) -> bool:
        """True when the document was read completely. False means nothing was read at all."""
        return not self.refusal

    def __post_init__(self) -> None:
        if bool(self.text) and bool(self.refusal):
            raise ValueError("an extraction is either text or a refusal, never both")


def _refuse(reason: str) -> PdfExtraction:
    return PdfExtraction(text="", refusal=reason)


class _Refused(Exception):  # noqa: N818 - a control-flow signal, not an error condition
    """Internal: abandon the whole document with a named reason.

    Raised anywhere in the walk and caught once at the top. That shape is the point — every
    unsupported construct aborts the *document*, so there is no code path on which a
    partially-read page can escape and be presented as the page.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def looks_like_pdf(body: bytes) -> bool:
    """True when the bytes announce themselves as a PDF.

    Deliberately a header check and not a content-type check: `Content-Type` is what a server
    claims, and government servers serve PDFs as `application/octet-stream`, as
    `text/plain`, and occasionally as nothing at all.
    """
    return body[:5] == b"%PDF-"


def extract_pdf_text(body: bytes) -> PdfExtraction:
    """Extract every character of page text, or refuse the document by name.

    There is no third outcome. See the module docstring for why a partial extraction is the
    one result this function may never return.
    """
    if not looks_like_pdf(body):
        return _refuse("not-a-pdf")
    try:
        document = _Document(body)
        text = document.page_text()
    except _Refused as refused:
        return _refuse(refused.reason)
    except (zlib.error, ValueError, IndexError, KeyError, RecursionError) as exc:
        # Any parse error at all is a refusal, never a partial read. The exception type is
        # not the interesting fact and is not reported as one: what the operator needs is
        # that this document was not read, and that nothing was inferred from it.
        return _refuse(f"malformed-pdf/{type(exc).__name__}")
    if not text.strip():
        # A scanned form, a wrapper around a single image, or a document whose text this
        # subset happens to reach none of. Claiming an empty extraction would let the caller
        # diff "" against "" and print "no text difference" about a document nobody read.
        return _refuse("no-extractable-text")
    return PdfExtraction(text=text, refusal="")


# --------------------------------------------------------------------------------------
# Object layer
# --------------------------------------------------------------------------------------

_OBJ_HEADER_RE = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")
_REF_RE = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+R(?![a-zA-Z0-9])")
_NAME_RE = re.compile(rb"/([^\s/<>\[\]()%]*)")
_NUMBER_RE = re.compile(rb"[-+]?(?:\d+\.\d*|\.\d+|\d+)")


@dataclass(frozen=True, slots=True)
class _Ref:
    number: int
    generation: int


@dataclass(frozen=True, slots=True)
class _Stream:
    """A stream object: its dictionary and its still-encoded bytes."""

    dictionary: dict[str, object]
    encoded: bytes


class _Document:
    """One PDF, parsed far enough to read its pages and no further.

    The cross-reference table is deliberately **not** parsed, and the reason is a refusal
    rather than a shortcut. An incrementally-updated PDF defines the same object number more
    than once, and only the xref decides which definition is current; a scan that takes "the
    last one" is right most of the time, which is the property this project trusts least. So
    the scan collects every definition and a second definition of any object number refuses
    the document. What we lose is incrementally-updated files, named as such and countable.
    What we would have lost is a diff computed against a superseded revision of a form.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body
        self._objects: dict[int, bytes] = {}
        self._streams: dict[int, _Stream] = {}
        self._scan_objects()
        self._expand_object_streams()
        if b"/Encrypt" in body:
            # Encryption reaches strings and streams, so every piece of text in the document
            # is ciphertext to this parser. There is no partial answer available and the
            # standard security handler is not implemented; the honest report is that we did
            # not read it.
            raise _Refused("encrypted")

    # -- scanning ----------------------------------------------------------------

    def _scan_objects(self) -> None:
        seen: set[int] = set()
        # A stream's payload is arbitrary bytes and can contain something that reads as an
        # object header. Skipping past each payload keeps those from being taken for real
        # definitions — which would otherwise refuse honest documents as "duplicated".
        skip_until = 0
        for match in _OBJ_HEADER_RE.finditer(self._body):
            if match.start() < skip_until:
                continue
            number = int(match.group(1))
            if number in seen:
                raise _Refused("duplicate-object-definition")
            seen.add(number)
            if len(seen) > MAX_OBJECTS:
                raise _Refused("too-many-objects")
            skip_until = self._record_object(number, match.end())

    def _record_object(self, number: int, start: int) -> int:
        """Record one object; return the offset the scan may safely resume from."""
        body = self._body
        stream_at = body.find(b"stream", start)
        endobj_at = body.find(b"endobj", start)
        if endobj_at == -1:
            endobj_at = len(body)
        if stream_at == -1 or stream_at > endobj_at:
            self._objects[number] = body[start:endobj_at]
            return endobj_at

        dictionary_bytes = body[start:stream_at]
        payload_start = stream_at + len(b"stream")
        # The spec allows CRLF or LF after the `stream` keyword, and nothing else.
        if body[payload_start : payload_start + 2] == b"\r\n":
            payload_start += 2
        elif body[payload_start : payload_start + 1] in (b"\n", b"\r"):
            payload_start += 1
        payload_end = self._stream_end(dictionary_bytes, payload_start)
        self._objects[number] = dictionary_bytes
        self._streams[number] = _Stream(
            dictionary=_parse_dictionary(dictionary_bytes),
            encoded=body[payload_start:payload_end],
        )
        return payload_end

    def _stream_end(self, dictionary_bytes: bytes, payload_start: int) -> int:
        """Where a stream's payload stops — by its declared `/Length` when that is checkable.

        A direct `/Length` is trusted only when `endstream` actually follows it, which is the
        cheap way to catch the generators that write a stale length. An indirect `/Length`
        (`/Length 12 0 R`) is not resolved, because resolving it needs objects this scan has
        not finished collecting; that case falls through to the search, which is exact
        whenever `endstream` does not appear inside the payload.
        """
        body = self._body
        declared = _parse_dictionary(dictionary_bytes).get("Length")
        if isinstance(declared, int) and declared >= 0:
            candidate = payload_start + declared
            trailing = body[candidate : candidate + 20].lstrip(b"\r\n \t")
            if trailing.startswith(b"endstream"):
                return candidate
        found = body.find(b"endstream", payload_start)
        if found == -1:
            raise _Refused("unterminated-stream")
        # The EOL that separates the payload from the `endstream` keyword belongs to the
        # syntax, not to the data.
        while found > payload_start and body[found - 1 : found] in (b"\n", b"\r"):
            found -= 1
        return found

    # -- object streams ----------------------------------------------------------

    def _expand_object_streams(self) -> None:
        """Register the objects held inside `/Type /ObjStm` compressed object streams.

        PDF 1.5 generators put the catalog and the page tree in here routinely, so refusing
        object streams would refuse most modern government forms — which is not a safe
        refusal, it is a useless one.
        """
        for number in sorted(self._streams):
            stream = self._streams[number]
            if stream.dictionary.get("Type") != _Name("ObjStm"):
                continue
            count = stream.dictionary.get("N")
            first = stream.dictionary.get("First")
            if not isinstance(count, int) or not isinstance(first, int):
                raise _Refused("malformed-object-stream")
            payload = self._decode_stream(stream)
            header = payload[:first].split()
            if len(header) != count * 2:
                raise _Refused("malformed-object-stream")
            offsets = [(int(header[i * 2]), int(header[i * 2 + 1])) for i in range(count)]
            for index, (contained, offset) in enumerate(offsets):
                if contained in self._objects:
                    raise _Refused("duplicate-object-definition")
                end = first + offsets[index + 1][1] if index + 1 < len(offsets) else len(payload)
                self._objects[contained] = payload[first + offset : end]

    # -- decoding ----------------------------------------------------------------

    def _decode_stream(self, stream: _Stream) -> bytes:
        """Decode one stream, or refuse the document by filter name.

        `FlateDecode` and "no filter" are the whole supported set. Every other filter is
        named in the refusal rather than folded into a generic failure, because the list of
        filters real government PDFs actually use is the evidence that would justify adding
        one.
        """
        filters = stream.dictionary.get("Filter")
        names: list[str] = []
        if isinstance(filters, _Name):
            names = [filters.value]
        elif isinstance(filters, list):
            for item in filters:
                if not isinstance(item, _Name):
                    raise _Refused("malformed-filter")
                names.append(item.value)
        elif filters is not None:
            raise _Refused("malformed-filter")

        if "DecodeParms" in stream.dictionary and names == ["FlateDecode"]:
            parms = stream.dictionary.get("DecodeParms")
            if isinstance(parms, dict) and parms.get("Predictor") not in (None, 1):
                # Predictors are a PNG/TIFF pre-filter over the decompressed bytes. Not
                # implemented, and a stream decoded without its predictor is scrambled rather
                # than merely imperfect, so this refuses rather than reads.
                raise _Refused("unsupported-decode-predictor")

        data = stream.encoded
        for name in names:
            if name != "FlateDecode":
                raise _Refused(f"unsupported-filter/{name}")
            data = _inflate(data)
        return data

    # -- resolution --------------------------------------------------------------

    def resolve(self, value: object) -> object:
        """Follow an indirect reference to the object it names. Non-references pass through."""
        seen = 0
        while isinstance(value, _Ref):
            seen += 1
            if seen > MAX_TREE_DEPTH:
                raise _Refused("reference-cycle")
            raw = self._objects.get(value.number)
            if raw is None:
                raise _Refused("dangling-reference")
            if value.number in self._streams:
                return self._streams[value.number]
            value = _parse_object(raw)
        return value

    def resolve_dict(self, value: object) -> dict[str, object]:
        resolved = self.resolve(value)
        if isinstance(resolved, _Stream):
            return resolved.dictionary
        if not isinstance(resolved, dict):
            raise _Refused("expected-dictionary")
        return resolved

    def stream_bytes(self, value: object) -> bytes:
        resolved = self.resolve(value)
        if not isinstance(resolved, _Stream):
            raise _Refused("expected-stream")
        return self._decode_stream(resolved)

    # -- the page tree -----------------------------------------------------------

    def _catalog(self) -> dict[str, object]:
        """The document catalog, found by its own `/Type` rather than through the trailer.

        Two catalogs means two candidate page trees and no basis in this parser for choosing
        between them, which is the same ambiguity a duplicate object definition raises and
        gets the same answer.
        """
        catalogs = [
            number
            for number, raw in self._objects.items()
            if b"/Catalog" in raw and self._is_catalog(raw)
        ]
        if not catalogs:
            raise _Refused("no-catalog")
        if len(catalogs) > 1:
            raise _Refused("ambiguous-catalog")
        return self.resolve_dict(_Ref(catalogs[0], 0))

    def _is_catalog(self, raw: bytes) -> bool:
        try:
            parsed = _parse_object(raw)
        except (_Refused, ValueError, IndexError):
            return False
        return isinstance(parsed, dict) and parsed.get("Type") == _Name("Catalog")

    def pages(self) -> list[dict[str, object]]:
        """Every page, in the order the page tree declares, with `/Resources` inherited."""
        root = self.resolve_dict(self._catalog().get("Pages"))
        collected: list[dict[str, object]] = []
        self._walk_page_tree(root, inherited={}, depth=0, visited=set(), out=collected)
        if not collected:
            raise _Refused("no-pages")
        return collected

    def _walk_page_tree(
        self,
        node: dict[str, object],
        *,
        inherited: dict[str, object],
        depth: int,
        visited: set[int],
        out: list[dict[str, object]],
    ) -> None:
        if depth > MAX_TREE_DEPTH:
            raise _Refused("page-tree-too-deep")
        merged = dict(inherited)
        for key in ("Resources", "MediaBox", "Rotate"):
            if key in node:
                merged[key] = node[key]

        kids = node.get("Kids")
        if kids is None:
            page = dict(merged)
            page.update(node)
            out.append(page)
            if len(out) > MAX_PAGES:
                raise _Refused("too-many-pages")
            return

        resolved_kids = self.resolve(kids)
        if not isinstance(resolved_kids, list):
            raise _Refused("malformed-page-tree")
        for kid in resolved_kids:
            if isinstance(kid, _Ref):
                if kid.number in visited:
                    raise _Refused("page-tree-cycle")
                visited.add(kid.number)
            self._walk_page_tree(
                self.resolve_dict(kid),
                inherited=merged,
                depth=depth + 1,
                visited=visited,
                out=out,
            )

    def page_text(self) -> str:
        """Every page's text, in page order, one page per block."""
        blocks: list[str] = []
        for page in self.pages():
            resources = self.resolve_dict(page.get("Resources", {}))
            blocks.append(_TextWalker(self, resources).run(self._page_content(page)))
        return "\n".join(blocks)

    def _page_content(self, page: dict[str, object]) -> bytes:
        contents = page.get("Contents")
        if contents is None:
            return b""
        resolved = self.resolve(contents)
        if isinstance(resolved, list):
            # The spec concatenates a `/Contents` array into one stream, and a token may not
            # straddle the join, so the newline between parts is required rather than tidy.
            return b"\n".join(self.stream_bytes(part) for part in resolved)
        return self.stream_bytes(contents)


def _inflate(data: bytes) -> bytes:
    """zlib-inflate with a hard output bound, so a malicious PDF cannot exhaust memory."""
    decompressor = zlib.decompressobj()
    out = decompressor.decompress(data, MAX_DECOMPRESSED_BYTES)
    if decompressor.unconsumed_tail:
        raise _Refused("decompressed-stream-too-large")
    return out


# --------------------------------------------------------------------------------------
# Object syntax
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Name:
    """A PDF name (`/Font`). Distinct from `str` so a name can never be confused with a
    string literal that happens to hold the same characters."""

    value: str


def _parse_dictionary(raw: bytes) -> dict[str, object]:
    """Parse the first `<< … >>` in `raw`, or return an empty dictionary if there is none."""
    start = raw.find(b"<<")
    if start == -1:
        return {}
    parsed = _parse_object(raw[start:])
    return parsed if isinstance(parsed, dict) else {}


def _parse_object(raw: bytes) -> object:
    value, _ = _Parser(raw).parse_value(0)
    return value


class _Parser:
    """A recursive-descent reader for PDF object syntax.

    Only what the page tree and the font dictionaries need: dictionaries, arrays, names,
    numbers, strings, booleans, null and indirect references. Anything unrecognised refuses,
    because a parser that skips what it does not understand is a parser that can silently
    drop a `/ToUnicode`.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data

    def parse_value(self, index: int, depth: int = 0) -> tuple[object, int]:
        if depth > MAX_TREE_DEPTH:
            raise _Refused("object-nesting-too-deep")
        index = self.skip_space(index)
        data = self._data
        if index >= len(data):
            raise _Refused("truncated-object")

        char = data[index : index + 1]
        if data.startswith(b"<<", index):
            return self._parse_dict(index + 2, depth)
        if char == b"[":
            return self._parse_array(index + 1, depth)
        if char == b"(":
            return self._parse_literal_string(index + 1)
        if char == b"<":
            return self._parse_hex_string(index + 1)
        return self._parse_atom(index)

    def _parse_atom(self, index: int) -> tuple[object, int]:
        """A name, keyword, indirect reference or number — everything with no sub-structure."""
        data = self._data
        if data[index : index + 1] == b"/":
            match = _NAME_RE.match(data, index)
            if match is None:
                raise _Refused("malformed-name")
            return _Name(_decode_name(match.group(1))), match.end()
        for keyword, value in ((b"true", True), (b"false", False), (b"null", None)):
            if data.startswith(keyword, index):
                return value, index + len(keyword)
        reference = _REF_RE.match(data, index)
        if reference is not None:
            return _Ref(int(reference.group(1)), int(reference.group(2))), reference.end()
        number = _NUMBER_RE.match(data, index)
        if number is not None:
            return _to_number(number.group(0)), number.end()
        raise _Refused("unparsable-object")

    def skip_space(self, index: int) -> int:
        data = self._data
        while index < len(data):
            if data[index : index + 1] in b" \t\r\n\f\x00":
                index += 1
            elif data[index : index + 1] == b"%":
                end = data.find(b"\n", index)
                index = len(data) if end == -1 else end + 1
            else:
                return index
        return index

    def _parse_dict(self, index: int, depth: int) -> tuple[dict[str, object], int]:
        out: dict[str, object] = {}
        data = self._data
        while True:
            index = self.skip_space(index)
            if data.startswith(b">>", index):
                return out, index + 2
            if index >= len(data):
                raise _Refused("truncated-dictionary")
            key, index = self.parse_value(index, depth + 1)
            if not isinstance(key, _Name):
                raise _Refused("malformed-dictionary-key")
            value, index = self.parse_value(index, depth + 1)
            out[key.value] = value

    def _parse_array(self, index: int, depth: int) -> tuple[list[object], int]:
        out: list[object] = []
        data = self._data
        while True:
            index = self.skip_space(index)
            if data.startswith(b"]", index):
                return out, index + 1
            if index >= len(data):
                raise _Refused("truncated-array")
            value, index = self.parse_value(index, depth + 1)
            out.append(value)

    def _parse_literal_string(self, index: int) -> tuple[bytes, int]:
        data = self._data
        out = bytearray()
        depth = 1
        while index < len(data):
            char = data[index]
            if char == 0x5C:  # backslash
                index = _append_escape(data, index + 1, out)
                continue
            if char == 0x28:  # (
                depth += 1
            elif char == 0x29:  # )
                depth -= 1
                if depth == 0:
                    return bytes(out), index + 1
            out.append(char)
            index += 1
        raise _Refused("truncated-string")

    def _parse_hex_string(self, index: int) -> tuple[bytes, int]:
        data = self._data
        end = data.find(b">", index)
        if end == -1:
            raise _Refused("truncated-hex-string")
        digits = bytes(c for c in data[index:end] if c not in b" \t\r\n\f\x00")
        if len(digits) % 2:
            digits += b"0"  # the spec pads a trailing odd digit with zero
        try:
            return bytes.fromhex(digits.decode("ascii")), end + 1
        except (ValueError, UnicodeDecodeError) as exc:
            raise _Refused("malformed-hex-string") from exc


_ESCAPES: Final = {
    0x6E: 0x0A,  # n
    0x72: 0x0D,  # r
    0x74: 0x09,  # t
    0x62: 0x08,  # b
    0x66: 0x0C,  # f
    0x28: 0x28,  # (
    0x29: 0x29,  # )
    0x5C: 0x5C,  # backslash
}


def _append_escape(data: bytes, index: int, out: bytearray) -> int:
    if index >= len(data):
        raise _Refused("truncated-string")
    char = data[index]
    if char in _ESCAPES:
        out.append(_ESCAPES[char])
        return index + 1
    if char in b"\n":
        return index + 1  # a line continuation contributes nothing
    if char in b"\r":
        return index + 2 if data[index + 1 : index + 2] == b"\n" else index + 1
    if 0x30 <= char <= 0x37:  # octal, one to three digits
        take = 0
        value = 0
        while take < 3 and index + take < len(data) and 0x30 <= data[index + take] <= 0x37:
            value = value * 8 + (data[index + take] - 0x30)
            take += 1
        out.append(value & 0xFF)
        return index + take
    out.append(char)  # an undefined escape is the character itself, per the spec
    return index + 1


def _decode_name(raw: bytes) -> str:
    """Resolve `#xx` hex escapes in a name, then decode as Latin-1 (names are byte strings)."""
    out = bytearray()
    index = 0
    while index < len(raw):
        if raw[index : index + 1] == b"#" and index + 2 < len(raw):
            try:
                out.append(int(raw[index + 1 : index + 3], 16))
            except ValueError as exc:
                raise _Refused("malformed-name") from exc
            index += 3
            continue
        out.append(raw[index])
        index += 1
    return out.decode("latin-1")


def _to_number(raw: bytes) -> int | float:
    text = raw.decode("ascii")
    return float(text) if "." in text else int(text)


# --------------------------------------------------------------------------------------
# Fonts: the only place a byte becomes a character
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FontMap:
    """How one font's character codes become text: fixed code width plus an exact table.

    There is no fallback and no "best effort" branch. A code the table does not hold refuses
    the document, because the alternative — emitting a placeholder, or skipping the glyph — is
    a diff that silently disagrees with the page.
    """

    code_bytes: int
    table: dict[int, str]

    def decode(self, raw: bytes) -> str:
        if len(raw) % self.code_bytes:
            raise _Refused("misaligned-character-code")
        out: list[str] = []
        for start in range(0, len(raw), self.code_bytes):
            code = int.from_bytes(raw[start : start + self.code_bytes], "big")
            mapped = self.table.get(code)
            if mapped is None:
                raise _Refused("unmappable-character-code")
            out.append(mapped)
        return "".join(out)


def _single_byte_table(codec: str, overrides: dict[int, str]) -> dict[int, str]:
    """A 256-entry code→character table built from a standard-library codec.

    Built from a codec rather than transcribed by hand because a hand-typed 224-row table is
    a place for a silent typo to live, and a typo here is a character that comes out wrong in
    a diff a reviewer trusts. Codes the codec leaves undefined stay absent, so showing one
    refuses the document instead of inventing a replacement character.
    """
    table = {}
    for code in range(256):
        try:
            table[code] = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            continue
    table.update(overrides)
    return table


# The two base encodings a PDF can *declare*, and the only two accepted without a
# `/ToUnicode`. Both are fixed tables, so accepting them is reading a stated fact rather than
# guessing at one.
#
# `WinAnsiEncoding` is cp1252 (its 0xA0 is a no-break space where the PDF table says "space";
# normalization folds both to a space before anything is hashed, so the distinction cannot
# reach a diff). `MacRomanEncoding` is Mac OS Roman with exactly one documented divergence:
# PDF's table puts the generic currency sign at 0xDB where Mac OS Roman puts the euro. That
# one cell is overridden rather than tolerated, because "close enough" is how a wrong
# character reaches a reviewer.
_WINANSI_TABLE: Final = _single_byte_table("cp1252", {})
_MACROMAN_TABLE: Final = _single_byte_table("mac_roman", {0xDB: "\u00a4"})
_DECLARED_ENCODINGS: Final = {
    "WinAnsiEncoding": _WINANSI_TABLE,
    "MacRomanEncoding": _MACROMAN_TABLE,
}


def _font_map(document: _Document, font: dict[str, object]) -> _FontMap:
    """Build the code→text map for one font, or refuse.

    `/ToUnicode` is preferred wherever it exists because it is the document's *own* statement
    about what its glyph codes mean — the only mapping that is evidence rather than
    inference. Where it is absent, the only accepted alternative is a simple font that
    *declares* one of the two fixed base encodings and modifies it with nothing.
    """
    subtype = font.get("Subtype")
    if subtype == _Name("Type3"):
        # A Type 3 font draws its glyphs with arbitrary content streams. There is no reliable
        # code→character mapping without one, and guessing one would invent page text.
        raise _Refused("unsupported-font/Type3")

    to_unicode = font.get("ToUnicode")
    if to_unicode is not None:
        return _parse_to_unicode(document.stream_bytes(to_unicode))

    if subtype == _Name("Type0"):
        raise _Refused("composite-font-without-tounicode")

    encoding = document.resolve(font.get("Encoding"))
    if isinstance(encoding, dict):
        if "Differences" in encoding:
            # `/Differences` remaps codes to glyph *names*, and turning a glyph name into a
            # character needs the Adobe Glyph List. Not carried, so not guessed.
            raise _Refused("unsupported-encoding/Differences")
        encoding = encoding.get("BaseEncoding")
    if isinstance(encoding, _Name) and encoding.value in _DECLARED_ENCODINGS:
        return _FontMap(code_bytes=1, table=_DECLARED_ENCODINGS[encoding.value])
    if encoding is None:
        # The font's *built-in* encoding, which lives inside the embedded font program. A
        # standard-14 text font is very probably StandardEncoding; a symbolic font is very
        # probably not; "very probably" is not a basis for publishing a diff.
        raise _Refused("unsupported-encoding/builtin")
    name = encoding.value if isinstance(encoding, _Name) else "malformed"
    raise _Refused(f"unsupported-encoding/{name}")


_CODESPACE_RE = re.compile(rb"begincodespacerange(.*?)endcodespacerange", re.DOTALL)
_BFCHAR_RE = re.compile(rb"beginbfchar(.*?)endbfchar", re.DOTALL)
_BFRANGE_RE = re.compile(rb"beginbfrange(.*?)endbfrange", re.DOTALL)
_HEX_RE = re.compile(rb"<([0-9A-Fa-f\s]*)>")


def _parse_to_unicode(cmap: bytes) -> _FontMap:
    """Read a `/ToUnicode` CMap into an exact code→text table.

    Mixed code widths are refused rather than handled: resolving them needs the codespace
    machine a real CMap interpreter implements, and a wrong width silently shifts every
    character in a string by one byte — which produces plausible-looking mojibake, the worst
    possible failure for a diff a human is meant to trust.
    """
    widths = set()
    for block in _CODESPACE_RE.findall(cmap):
        for hex_digits in _HEX_RE.findall(block):
            widths.add(len(_hex_bytes(hex_digits)))
    if len(widths) > 1:
        raise _Refused("mixed-width-cmap")
    code_bytes = widths.pop() if widths else 2
    if code_bytes not in (1, 2):
        raise _Refused(f"unsupported-cmap-width/{code_bytes}")

    table: dict[int, str] = {}
    for block in _BFCHAR_RE.findall(cmap):
        items = _HEX_RE.findall(block)
        if len(items) % 2:
            raise _Refused("malformed-bfchar")
        for index in range(0, len(items), 2):
            code = int.from_bytes(_hex_bytes(items[index]), "big")
            table[code] = _utf16be(_hex_bytes(items[index + 1]))

    for block in _BFRANGE_RE.findall(cmap):
        _read_bfrange(block, table)
    if not table:
        raise _Refused("empty-cmap")
    return _FontMap(code_bytes=code_bytes, table=table)


def _read_bfrange(block: bytes, table: dict[int, str]) -> None:
    """`<lo> <hi> <dst>` and `<lo> <hi> [ <d1> <d2> … ]` — both forms, no third."""
    index = 0
    while index < len(block):
        low = _HEX_RE.search(block, index)
        if low is None:
            return
        high = _HEX_RE.search(block, low.end())
        if high is None:
            raise _Refused("malformed-bfrange")
        start = int.from_bytes(_hex_bytes(low.group(1)), "big")
        end = int.from_bytes(_hex_bytes(high.group(1)), "big")
        if end < start or end - start > 0xFFFF:
            raise _Refused("malformed-bfrange")
        if block[high.end() :].lstrip().startswith(b"["):
            index = _read_bfrange_array(block, high.end(), start, end, table)
            continue
        destination = _HEX_RE.search(block, high.end())
        if destination is None:
            raise _Refused("malformed-bfrange")
        base = _hex_bytes(destination.group(1))
        for offset in range(end - start + 1):
            table[start + offset] = _utf16be(_increment_utf16be(base, offset))
        index = destination.end()


def _read_bfrange_array(
    block: bytes, after_high: int, start: int, end: int, table: dict[int, str]
) -> int:
    """The `<lo> <hi> [ <d1> <d2> … ]` form, which lists a destination per code."""
    close = block.find(b"]", after_high)
    if close == -1:
        raise _Refused("malformed-bfrange")
    destinations = _HEX_RE.findall(block[after_high:close])
    if len(destinations) != end - start + 1:
        raise _Refused("malformed-bfrange")
    for offset, destination in enumerate(destinations):
        table[start + offset] = _utf16be(_hex_bytes(destination))
    return close + 1


def _hex_bytes(hex_digits: bytes) -> bytes:
    digits = bytes(c for c in hex_digits if c not in b" \t\r\n")
    if len(digits) % 2:
        digits += b"0"
    try:
        return bytes.fromhex(digits.decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _Refused("malformed-hex-string") from exc


def _utf16be(raw: bytes) -> str:
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError as exc:
        raise _Refused("malformed-cmap-destination") from exc


def _increment_utf16be(base: bytes, offset: int) -> bytes:
    """Advance the LAST code unit of a UTF-16BE destination, as `bfrange` defines it."""
    if offset == 0 or len(base) < 2:
        return base
    head, tail = base[:-2], int.from_bytes(base[-2:], "big") + offset
    if tail > 0xFFFF:
        raise _Refused("malformed-bfrange")
    return head + tail.to_bytes(2, "big")


# --------------------------------------------------------------------------------------
# Content streams: where the text actually is
# --------------------------------------------------------------------------------------

_TOKEN_RE = re.compile(rb"[^\s\[\]<>(){}/%]+")


class _TextWalker:
    """Walks one content stream and emits every character it shows, in order.

    Line breaks are placed where the text matrix moves to a new Y, and spaces where a `TJ`
    adjustment is wide enough to be a word gap. Both of those decide *whitespace only*, and
    `normalize.normalize_text` collapses whitespace before anything is hashed or diffed — so
    a misplaced break makes a passage read a little oddly and cannot make a character vanish.
    Characters are never a judgment call: they come from the font's own map, or the document
    is refused.
    """

    def __init__(self, document: _Document, resources: dict[str, object], depth: int = 0) -> None:
        if depth > MAX_FORM_DEPTH:
            raise _Refused("form-xobject-too-deep")
        self._document = document
        self._resources = resources
        self._depth = depth
        self._out: list[str] = []
        self._font: _FontMap | None = None
        self._fonts: dict[str, _FontMap] = {}
        self._line_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        self._leading = 0.0
        self._pen: tuple[float, float] | None = None

    def run(self, content: bytes) -> str:
        self._walk(content)
        return "".join(self._out)

    # -- tokenizing --------------------------------------------------------------

    def _walk(self, content: bytes) -> None:
        parser = _Parser(content)
        operands: list[object] = []
        index = 0
        while index < len(content):
            index = parser.skip_space(index)
            if index >= len(content):
                return
            char = content[index : index + 1]
            if char in (b"(", b"<", b"/", b"[") or _NUMBER_RE.match(content, index):
                value, index = parser.parse_value(index)
                operands.append(value)
                continue
            if char == b"]" or char == b">":
                index += 1  # a stray closer; the value parser consumed its opener
                continue
            match = _TOKEN_RE.match(content, index)
            if match is None:
                index += 1
                continue
            operator = match.group(0)
            index = match.end()
            if operator == b"BI":
                index = _skip_inline_image(content, index)
                operands = []
                continue
            self._apply(operator, operands)
            operands = []

    # -- operators ---------------------------------------------------------------

    def _apply(self, operator: bytes, operands: list[object]) -> None:
        """Dispatch one operator.

        Unlisted operators are ignored, and that is safe rather than lax: the *only* ways a
        PDF shows a character are the four text operators below and a Type 3 glyph
        procedure, and a Type 3 font refuses the document before any of this runs.
        """
        handler = self._HANDLERS.get(operator)
        if handler is not None:
            handler(self, operands)

    def _begin_text(self, operands: list[object]) -> None:
        self._line_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        self._newline()

    def _end_text(self, operands: list[object]) -> None:
        self._newline()

    def _set_leading(self, operands: list[object]) -> None:
        if operands:
            self._leading = _as_float(operands[-1])

    def _set_matrix(self, operands: list[object]) -> None:
        if len(operands) < 6:
            return
        a, b, c, d, e, f = (_as_float(value) for value in operands[-6:])
        self._line_matrix = (a, b, c, d, e, f)
        self._break_if_moved()

    def _next_line_at(self, operands: list[object]) -> None:
        if len(operands) >= 2:
            self._translate(_as_float(operands[-2]), _as_float(operands[-1]))

    def _next_line_at_with_leading(self, operands: list[object]) -> None:
        if len(operands) >= 2:
            self._leading = -_as_float(operands[-1])
            self._translate(_as_float(operands[-2]), _as_float(operands[-1]))

    def _next_line(self, operands: list[object]) -> None:
        self._translate(0.0, -self._leading)

    def _show_operand(self, operands: list[object]) -> None:
        if operands:
            self._show(operands[-1])

    def _next_line_and_show(self, operands: list[object]) -> None:
        """`'` and `"` — both move to the next line, then show the string they end with."""
        self._translate(0.0, -self._leading)
        self._show_operand(operands)

    def _show_array_operand(self, operands: list[object]) -> None:
        if operands:
            self._show_array(operands[-1])

    def _do_operand(self, operands: list[object]) -> None:
        if operands:
            self._do_xobject(operands[-1])

    _HANDLERS: Final = {
        b"BT": _begin_text,
        b"ET": _end_text,
        b"Tf": lambda self, operands: self._select_font(operands),
        b"TL": _set_leading,
        b"Tm": _set_matrix,
        b"Td": _next_line_at,
        b"TD": _next_line_at_with_leading,
        b"T*": _next_line,
        b"Tj": _show_operand,
        b"'": _next_line_and_show,
        b'"': _next_line_and_show,
        b"TJ": _show_array_operand,
        b"Do": _do_operand,
    }

    def _select_font(self, operands: list[object]) -> None:
        names = [item for item in operands if isinstance(item, _Name)]
        if not names:
            raise _Refused("malformed-font-selection")
        key = names[-1].value
        if key not in self._fonts:
            fonts = self._document.resolve_dict(self._resources.get("Font", {}))
            if key not in fonts:
                # The stream shows text in a font its own resource dictionary does not
                # declare. We cannot map those codes and will not guess at them.
                raise _Refused("unknown-font-resource")
            self._fonts[key] = _font_map(self._document, self._document.resolve_dict(fonts[key]))
        self._font = self._fonts[key]

    def _show(self, value: object) -> None:
        if not isinstance(value, bytes):
            return
        if self._font is None:
            raise _Refused("text-shown-before-a-font-was-selected")
        self._out.append(self._font.decode(value))

    def _show_array(self, value: object) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            if isinstance(item, bytes):
                self._show(item)
            elif isinstance(item, int | float) and float(item) <= TJ_WORD_GAP:
                self._out.append(" ")

    def _do_xobject(self, value: object) -> None:
        """Draw an XObject — and if it is a *form*, walk into it, because forms hold text.

        This is the recursion the safety rule requires rather than an optimisation. A
        generator that puts a page's body text inside a Form XObject is common, and a parser
        that ignored `Do` would extract the page furniture, miss the content, and present the
        result as the page.
        """
        if not isinstance(value, _Name):
            return
        xobjects = self._document.resolve_dict(self._resources.get("XObject", {}))
        target = xobjects.get(value.value)
        if target is None:
            raise _Refused("unknown-xobject-resource")
        stream = self._document.resolve(target)
        if not isinstance(stream, _Stream):
            raise _Refused("expected-stream")
        subtype = stream.dictionary.get("Subtype")
        if subtype == _Name("Image"):
            return  # an image shows no text this extractor could read
        if subtype != _Name("Form"):
            raise _Refused("unsupported-xobject")
        resources = stream.dictionary.get("Resources")
        nested = _TextWalker(
            self._document,
            self._document.resolve_dict(resources) if resources is not None else self._resources,
            depth=self._depth + 1,
        )
        self._newline()
        self._out.append(nested.run(self._document.stream_bytes(target)))
        self._newline()

    # -- placement ---------------------------------------------------------------

    def _translate(self, tx: float, ty: float) -> None:
        a, b, c, d, e, f = self._line_matrix
        self._line_matrix = (a, b, c, d, tx * a + ty * c + e, tx * b + ty * d + f)
        self._break_if_moved()

    def _break_if_moved(self) -> None:
        position = (self._line_matrix[4], self._line_matrix[5])
        if self._pen is not None and position[1] != self._pen[1]:
            self._newline()
        elif self._pen is not None and position[0] != self._pen[0]:
            self._out.append(" ")
        self._pen = position

    def _newline(self) -> None:
        if self._out and not self._out[-1].endswith("\n"):
            self._out.append("\n")


def _skip_inline_image(content: bytes, index: int) -> int:
    """Step over a `BI … ID <binary> EI` inline image without tokenizing its payload."""
    data_at = content.find(b"ID", index)
    if data_at == -1:
        raise _Refused("unterminated-inline-image")
    cursor = data_at + 3
    while True:
        end = content.find(b"EI", cursor)
        if end == -1:
            raise _Refused("unterminated-inline-image")
        before = content[end - 1 : end]
        after = content[end + 2 : end + 3]
        if before in b" \t\r\n\x00" and (after == b"" or after in b" \t\r\n\x00/[<("):
            return end + 2
        cursor = end + 2


def _as_float(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    raise _Refused("malformed-numeric-operand")
