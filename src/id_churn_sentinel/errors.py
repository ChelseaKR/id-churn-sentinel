"""The error taxonomy. One base class, so a caller can catch everything this tool raises."""

from __future__ import annotations

__all__ = [
    "PublishError",
    "RegistryError",
    "ReviewError",
    "SentinelError",
    "StoreError",
    "VerificationError",
]


class SentinelError(Exception):
    """Base class for every error raised by this package."""


class RegistryError(SentinelError):
    """The committed source registry is malformed, or an entry violates a registry rule."""


class StoreError(SentinelError):
    """The snapshot/change store was asked for something it cannot do."""


class ReviewError(SentinelError):
    """A review action was rejected — most often, a classification without a human reviewer."""


class VerificationError(SentinelError):
    """A source verification was refused — most often, a confirmation with no named verifier.

    Same shape as `ReviewError`, and for the same reason: `verified: true` is a claim that a
    *person* opened a government page and confirmed it is the right one. An anonymous
    verification is indistinguishable from a machine's, and the machine's opinion is exactly
    what this field exists to not be."""


class PublishError(SentinelError):
    """Publication was refused. The only reason this exists: an unreviewed record reached
    the feed writer. That is a bug, and it must be loud rather than published."""
