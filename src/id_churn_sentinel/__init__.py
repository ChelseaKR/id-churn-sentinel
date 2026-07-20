"""ID Churn Sentinel — cited, machine-checkable change detection for US transgender
identity-document law and process.

The gap this fills is *freshness*, not coverage. Three incumbents already document how to
change the name and gender marker on an ID in every US jurisdiction, and all three say, in
their own words, that they cannot keep up with how fast the rules move. Nobody publishes
structured, cited, machine-checkable change detection over the official sources themselves.
This is that layer, and its consumers are those incumbents — not their users.

What it will and will not say:

    will      "the official Texas DPS page for driver-license changes held different
              bytes today than last week; here are the passages that differ; a human
              named N reviewed it and called it substantive"
    will not  anything at all about what the law *is*

See `README.md` for the finding, `docs/RESPONSIBLE-TECH-AUDITS.md` for why the second line
is a hard boundary rather than a phase-two feature.
"""

from __future__ import annotations

from importlib import metadata

__all__ = ["__version__"]

try:
    # Derived, never hand-copied: the version lives in pyproject.toml and nowhere else.
    __version__ = metadata.version("id-churn-sentinel")
except metadata.PackageNotFoundError:  # pragma: no cover — source tree, not installed
    __version__ = "0.0.0+unknown"
