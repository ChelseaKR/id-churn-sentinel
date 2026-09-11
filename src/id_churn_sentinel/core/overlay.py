"""Registry overlays — an organization's own sources, held to this repository's discipline (#77).

The committed registry is national by design and closed by process: every entry needs a named
verifier and a dated fetch-policy reading before it enters the attempt denominator. A county
legal-aid office wants that same discipline over pages this registry will never carry — its
clerk's name-change page, a district court's local fee schedule — and until now its only
options were a fork or a private copy that drifts.

An overlay is a registry-shaped file with one extra top-level key, ``overlay_id``. What makes it
safe is how little is new:

* **Same validator.** It is parsed by :func:`~id_churn_sentinel.core.registry.
  parse_registry_document`, the function the committed registry goes through, so a
  ``verified: true`` nobody signed does not load here either.
* **Same predicate.** Its entries go through
  :func:`~id_churn_sentinel.core.eligibility.evaluate_source` unchanged. An overlay cannot buy
  itself eligibility: it still needs a named verifier, dated evidence, an expiry, and a dated
  fetch-policy decision.
* **Its own namespace.** Every entry is stamped with the overlay's id, and the store keys every
  row on ``(overlay_id, source_id)`` with ``''`` meaning the committed registry (migration 12).
  Two counties both calling a page ``clerk-name-change`` share no row, and neither shares one
  with a committed entry of that name.

And what it can never do, each enforced somewhere other than this module:

* reach the public artifact — ``publish`` refuses an overlay registry and every overlay record,
  and ``publish --overlay`` refuses ``docs/`` before it renders anything (`core/publish.py`);
* move a published coverage number — ``coverage --check-docs`` never opens an overlay file;
* make the public run-health green — a run that declared an overlay is excluded from
  ``status.json`` and every per-jurisdiction receipt (`core/store.py`).

**Collisions are refused by name, in both directions that matter.** An overlay entry whose URL
the committed registry already watches is refused naming both ids: watching one page twice
doubles every change a reviewer sees, which is how a reviewer learns to ignore the queue. The
same holds between two overlays loaded together, and two overlay files may not declare one
``overlay_id``. URLs are compared under the crosswalk's own normalizer, so a host spelled in
capitals is not a different page.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from id_churn_sentinel.core.registry import (
    JURISDICTIONS,
    OVERLAY_ID_KEY,
    Registry,
    Source,
    load_registry,
    parse_registry_document,
    read_registry_document,
)
from id_churn_sentinel.core.staleness import normalize_url
from id_churn_sentinel.errors import RegistryError

__all__ = [
    "RESERVED_OVERLAY_IDS",
    "Overlay",
    "load_overlay",
    "load_overlays",
    "load_registry_file",
    "validate_overlays",
]

# The source-id slug grammar. Sharing it is what makes a store key `<overlay>/<source>`
# unambiguous: neither half can contain the separator.
_OVERLAY_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# The names `publish` gives per-jurisdiction files are `feed-<slug>.xml` and
# `changes-<slug>.json`, and an overlay's artifact set is `feed-<overlay_id>.xml` and
# `changes-<overlay_id>.json`. An overlay called `us-tx` would therefore write a file with
# exactly the name of the public Texas feed. Refused at load time, where the reason can be
# stated, rather than discovered as an overwritten file. Held equal to `site.feed_slug` over
# every jurisdiction by `tests/test_overlays.py`.
RESERVED_OVERLAY_IDS: frozenset[str] = frozenset(
    {"us"}
    | {f"us-{jurisdiction.lower()}" for jurisdiction in JURISDICTIONS if jurisdiction != "US"}
)


@dataclass(frozen=True, slots=True)
class Overlay:
    """One loaded, validated overlay. `registry.overlay_id` and every source's equal `overlay_id`."""

    overlay_id: str
    registry: Registry
    path: Path

    @property
    def sources(self) -> tuple[Source, ...]:
        return self.registry.sources


def load_overlay(path: Path) -> Overlay:
    """Load one overlay through the committed registry's validator. Any violation raises."""
    raw = read_registry_document(path, kind="overlay")
    overlay_id = raw.get(OVERLAY_ID_KEY)
    if not isinstance(overlay_id, str) or not _OVERLAY_ID_RE.match(overlay_id):
        raise RegistryError(
            f"overlay {path}: `{OVERLAY_ID_KEY}` must be a lowercase-hyphen slug (the same "
            f"grammar as a source id); got {overlay_id!r}. It namespaces every store row this "
            "overlay's sources write, so it cannot be absent or free text."
        )
    if overlay_id in RESERVED_OVERLAY_IDS:
        raise RegistryError(
            f"overlay {path}: `{OVERLAY_ID_KEY}` {overlay_id!r} is the name of a published "
            f"per-jurisdiction feed (feed-{overlay_id}.xml). An overlay's artifacts are named "
            "after its id, so this one would share a filename with the public feed. Choose a "
            "name that says whose sources these are."
        )
    return Overlay(
        overlay_id=overlay_id,
        registry=parse_registry_document(raw, overlay_id=overlay_id),
        path=path,
    )


def load_registry_file(path: Path) -> Registry:
    """Load the committed registry or an overlay — whichever the file itself declares.

    For the writers `sentinel verify` and `sentinel sources policy`, which record a human's
    decision into a file and then load it back through the validator to prove it is still
    loadable. The file says which validator it answers to; the writer does not get to choose.
    """
    raw = read_registry_document(path)
    return load_overlay(path).registry if OVERLAY_ID_KEY in raw else load_registry(path)


def load_overlays(paths: Sequence[Path], committed: Registry) -> tuple[Overlay, ...]:
    """Load every overlay named on the command line and refuse any collision between them."""
    overlays = tuple(load_overlay(path) for path in paths)
    validate_overlays(committed, overlays)
    return overlays


def validate_overlays(committed: Registry, overlays: Sequence[Overlay]) -> None:
    """Refuse a duplicate overlay id, and any URL watched in two namespaces at once.

    Called by every loader path and again by the watcher itself, so a programmatic caller that
    assembled overlays by hand gets the same refusal as the command line.
    """
    if committed.overlay_id:
        raise RegistryError(
            f"the committed registry cannot itself be an overlay ({committed.overlay_id!r})"
        )
    declared: dict[str, Path] = {}
    for overlay in overlays:
        earlier = declared.get(overlay.overlay_id)
        if earlier is not None:
            raise RegistryError(
                f"overlay id {overlay.overlay_id!r} is declared by both {earlier} and "
                f"{overlay.path}. An overlay id is a store namespace; two files sharing one "
                "would silently share every row either of them writes."
            )
        declared[overlay.overlay_id] = overlay.path

    # namespace → first key seen at this URL. Within one namespace the registry's own rule
    # (a unique jurisdiction/document-class/URL triple) already applies and is not restated.
    claimed: dict[str, tuple[str, str]] = {}
    for source in committed.sources:
        claimed.setdefault(normalize_url(source.url), ("", source.key))
    for overlay in overlays:
        for source in overlay.sources:
            url = normalize_url(source.url)
            holder = claimed.get(url)
            if holder is not None and holder[0] != overlay.overlay_id:
                owner = "the committed registry" if not holder[0] else f"overlay {holder[0]!r}"
                raise RegistryError(
                    f"overlay source {source.key!r} repeats a URL {owner} already watches as "
                    f"{holder[1]!r}: {source.url}. The same page watched under two ids doubles "
                    "every change a reviewer sees. Drop the overlay entry, or propose the page "
                    "to the committed registry by pull request."
                )
            claimed.setdefault(url, (overlay.overlay_id, source.key))
