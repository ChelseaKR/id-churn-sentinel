"""Throwaway file to verify the protect-main required_status_checks rule actually blocks a
merge. Deliberately fails lint (unused import) so `verify` goes red. Never meant to land —
this PR is closed without merging once the block is confirmed."""

import os  # noqa is deliberately NOT here — this should fail ruff F401 (unused import)


def nothing() -> None:
    pass
