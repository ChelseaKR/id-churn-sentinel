"""The kernel: registry → fetch → normalize → detect → review → publish.

The five modules and the one rule each exists to hold:

    registry    a closed vocabulary of jurisdictions and document classes; every entry
                is an *official* https URL, and `verified` only a human can flip
    fetch       the injectable network seam — and "a fetch failure is never drift"
    normalize   markup churn is not content change; the hash and the diff derive from
                exactly the same normalized bytes
    detect      on drift, produce the *passage that changed*, never a classification
    changes     significance is a human judgment; the type has no path to assert one
    store       the same human-in-the-loop rule again, as a SQL CHECK constraint
    publish     only human-confirmed records reach the feed, and the feed needs no account

Nothing in this package asserts what the law is. It asserts that a specific official page
holds different bytes than it did last week, and it shows you which ones.
"""

from __future__ import annotations

from id_churn_sentinel.core.changes import ChangeRecord, ReviewStatus, Significance
from id_churn_sentinel.core.detect import WatchReport, watch
from id_churn_sentinel.core.fetch import Fetcher, FetchResult, HttpFetcher
from id_churn_sentinel.core.normalize import content_hash, normalize_html
from id_churn_sentinel.core.publish import FEED_SCHEMA_VERSION, publish
from id_churn_sentinel.core.registry import (
    DOCUMENT_CLASSES,
    JURISDICTIONS,
    Registry,
    Source,
    load_registry,
)
from id_churn_sentinel.core.store import SnapshotStore

__all__ = [
    "DOCUMENT_CLASSES",
    "FEED_SCHEMA_VERSION",
    "JURISDICTIONS",
    "ChangeRecord",
    "FetchResult",
    "Fetcher",
    "HttpFetcher",
    "Registry",
    "ReviewStatus",
    "Significance",
    "SnapshotStore",
    "Source",
    "WatchReport",
    "content_hash",
    "load_registry",
    "normalize_html",
    "publish",
    "watch",
]
