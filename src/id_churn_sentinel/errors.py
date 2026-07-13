"""The error taxonomy. One base class, so a caller can catch everything this tool raises."""

from __future__ import annotations

__all__ = [
    "PublishError",
    "RegistryError",
    "ReviewError",
    "SentinelError",
    "StoreError",
]


class SentinelError(Exception):
    """Base class for every error raised by this package."""


class RegistryError(SentinelError):
    """The committed source registry is malformed, or an entry violates a registry rule."""


class StoreError(SentinelError):
    """The snapshot/change store was asked for something it cannot do."""


class ReviewError(SentinelError):
    """A review action was rejected — most often, a classification without a human reviewer."""


class PublishError(SentinelError):
    """Publication was refused. The only reason this exists: an unreviewed record reached
    the feed writer. That is a bug, and it must be loud rather than published."""
