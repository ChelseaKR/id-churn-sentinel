#!/usr/bin/env python3
"""Fail when the feed GitHub Pages serves is not the feed this checkout publishes.

The published surface is `docs/`, committed and served straight from the branch
with no build step and no CI, because a site that exists only while somebody
else's billing system agrees to run a job is a site that does not exist. That
decision removes a deploy pipeline, and with it every place a deploy could be
checked. Nothing in this repository has ever looked at the bytes a consumer
receives, so Pages serving an older commit, or Jekyll silently dropping a file,
or the branch never having been published at all, would leave every gate green
while an org polling `feed-us-tx.xml` read a different set of changes.

That matters more here than it would elsewhere. These feeds carry name and
gender-marker change procedures for 52 jurisdictions, and a consumer acting on
a stale one sends somebody to a counter with the wrong form.

This is the check for the deployment. It takes every file git tracks under
`docs/`, which is exactly what branch-served Pages publishes, fetches each over
HTTPS, and fails naming every byte-level difference.

    python3 tools/verify_live_site.py

The inventory comes from `git ls-files` rather than from the filesystem, for
the same reason the surface is committed in the first place: what the branch
serves is what is committed, and an untracked file sitting in a working copy is
not part of the published surface.

Vacuity is the failure mode a check like this is most exposed to, so three
things are refused outright instead of being reported as a pass:

  * an empty or short comparison set, because a sentinel that compares nothing
    and prints OK is worse than no sentinel at all (`--minimum`);
  * any fetch that does not return HTTP 200, an unreachable host included;
  * an origin that answers a guaranteed-missing path with anything but 404,
    which is how a catch-all would make every matching comparison meaningless.

Exit codes: 0 the live surface is the published surface, 1 it is not, 4 the
check could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import secrets
import ssl
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]

# The published origin, and the tree the deploy uploads to it.
LIVE_URL = "https://chelseakr.github.io/id-churn-sentinel/"
PUBLISHED_DIR = REPO / "docs"

# Every tracked file under docs/ is served from the branch. Nothing is excluded.
NOT_PUBLISHED: frozenset[str] = frozenset()

# The floor under the comparison set. A sentinel that finds nothing to compare
# and prints OK is worse than no sentinel, so a set smaller than this is a
# failure and not a pass.
MINIMUM_FILES = 100

# And a floor under the bytes, because a tree of 158 empty files would clear the
# file count and still compare nothing anybody could read.
MINIMUM_BYTES = 1_000_000

# Regenerating the published tree before comparing it, so the bytes checked
# against the deployment are bytes the code still produces. None where the
# published tree cannot be regenerated offline; see the note above.
REBUILD_COMMAND: tuple[str, ...] | None = None

MAXIMUM_FILE_BYTES = 16 * 1024 * 1024
EXIT_DIFFERS = 1
EXIT_CANNOT_RUN = 4


class LiveSiteError(RuntimeError):
    """The live surface could not be verified against this checkout."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class Origin:
    """Bounded HTTPS reads from one fixed public origin. Redirects are not followed."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            raise LiveSiteError(f"live URL {url!r} is not a canonical HTTPS origin")
        if not 1.0 <= timeout_seconds <= 60.0:
            raise LiveSiteError("timeout must be between 1 and 60 seconds")
        self.host = parts.hostname
        self.base = parts.path.rstrip("/")
        self.url = url
        self._timeout = timeout_seconds

    def target(self, relative: str, nonce: str) -> str:
        if relative.startswith("/") or "?" in relative or "#" in relative:
            raise LiveSiteError(f"relative path {relative!r} is not canonical")
        return f"{self.base}/{relative}?live-integrity={nonce}"

    def get(
        self,
        relative: str,
        *,
        nonce: str,
        maximum_bytes: int = MAXIMUM_FILE_BYTES,
    ) -> Response:
        target = self.target(relative, nonce)
        # The audit rule below is about HTTPSConnection used without certificate
        # verification: Python before 3.4.3 did not verify by default. This call
        # passes ssl.create_default_context(), which verifies both the chain and
        # the hostname, and is the condition the rule exists to require.
        # nosemgrep: httpsconnection-detected
        connection = http.client.HTTPSConnection(
            self.host, timeout=self._timeout, context=ssl.create_default_context()
        )
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept-Encoding": "identity",
                    "Cache-Control": "no-cache, no-store, max-age=0",
                    "Pragma": "no-cache",
                    "User-Agent": "id-churn-sentinel-live-integrity/1",
                },
            )
            response = connection.getresponse()
            encoding = response.getheader("Content-Encoding")
            if encoding not in {None, "identity"}:
                raise LiveSiteError(f"{target} came back {encoding}-encoded, not identity")
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                raise LiveSiteError(f"{target} exceeds the {maximum_bytes} byte read limit")
            return Response(status=response.status, body=body)
        except (OSError, http.client.HTTPException) as exc:
            raise LiveSiteError(f"GET https://{self.host}{target} failed: {exc}") from exc
        finally:
            connection.close()


def short(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:16]


def regenerate_from_the_checkout() -> None:
    """Refuse to compare against a committed tree the code no longer produces."""
    if REBUILD_COMMAND is None:
        return
    # REBUILD_COMMAND is a literal constant declared at the top of this file, never
    # an argument and never read from the environment, and the call takes no shell.
    result = subprocess.run(  # noqa: S603
        list(REBUILD_COMMAND),
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LiveSiteError(
            f"`{' '.join(REBUILD_COMMAND)}` failed, so the committed tree is not what "
            f"the code produces and there is nothing trustworthy to compare the live "
            f"surface with:\n{result.stdout}{result.stderr}"
        )


def tracked_paths() -> list[str]:
    """The files git tracks under docs/, which is what the branch serves."""
    result = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--", PUBLISHED_DIR.name],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise LiveSiteError(
            "git ls-files failed, so the published inventory is unknown: "
            f"{result.stderr.decode('utf-8', 'replace')}"
        )
    return [entry for entry in result.stdout.decode("utf-8").split("\0") if entry]


def published_inventory() -> dict[str, bytes]:
    """Every file the branch publishes, keyed by the path it is served at."""
    if not PUBLISHED_DIR.is_dir():
        raise LiveSiteError(f"{PUBLISHED_DIR} is not a directory")
    inventory: dict[str, bytes] = {}
    for entry in sorted(tracked_paths()):
        path = REPO / entry
        if path.is_symlink():
            raise LiveSiteError(f"{path} is a symlink; refusing to publish-compare it")
        if not path.is_file():
            raise LiveSiteError(f"{entry} is tracked under docs/ but is not a file here")
        relative = path.relative_to(PUBLISHED_DIR).as_posix()
        if relative in NOT_PUBLISHED:
            continue
        # docs/.nojekyll is deliberately zero bytes: its presence is the whole
        # instruction, and Jekyll silently drops files when it is missing. So an
        # empty file is compared like any other, and the floor that stops this
        # comparing nothing is MINIMUM_FILES plus MINIMUM_BYTES below.
        inventory[relative] = path.read_bytes()
    return inventory


def prove_the_origin_discriminates(origin: Origin, nonce: str) -> None:
    """A host that answers everything with 200 makes every comparison vacuous."""
    missing = f".live-integrity-guaranteed-missing-{nonce}"
    response = origin.get(missing, nonce=nonce, maximum_bytes=1024 * 1024)
    if response.status != 404:
        raise LiveSiteError(
            f"the origin answered a guaranteed-missing path with HTTP {response.status} "
            f"instead of 404, so a matching fetch would prove nothing: /{missing}"
        )


def compare(origin: Origin, inventory: dict[str, bytes], nonce: str) -> list[str]:
    differences: list[str] = []
    for relative, expected in sorted(inventory.items()):
        response = origin.get(relative, nonce=nonce)
        if response.status != 200:
            differences.append(
                f"{relative}: the live origin returned HTTP {response.status}; "
                f"this checkout publishes {len(expected)} bytes"
            )
            continue
        if response.body != expected:
            differences.append(
                f"{relative}: live sha256 {short(response.body)} "
                f"({len(response.body)} bytes) is not the published "
                f"{short(expected)} ({len(expected)} bytes)"
            )
    # The base path has to serve the index document too: a deploy that uploads the
    # file but stops serving the directory is still a broken publication.
    index = inventory.get("index.html")
    if index is not None:
        root = origin.get("", nonce=nonce)
        if root.status != 200:
            differences.append(f"/: the live origin returned HTTP {root.status}")
        elif root.body != index:
            differences.append(
                f"/: live sha256 {short(root.body)} is not the published index.html {short(index)}"
            )
    return differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=LIVE_URL, help=f"live site root (default {LIVE_URL})")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--minimum",
        type=int,
        default=MINIMUM_FILES,
        help="refuse to pass on fewer published files than this",
    )
    parser.add_argument(
        "--skip-rebuild",
        action="store_true",
        help="compare the committed tree without first regenerating it",
    )
    args = parser.parse_args(argv)

    try:
        if not args.skip_rebuild:
            regenerate_from_the_checkout()
        inventory = published_inventory()
        if len(inventory) < args.minimum:
            raise LiveSiteError(
                f"the comparison set holds {len(inventory)} file(s), below the floor of "
                f"{args.minimum}. A check that compares nothing must fail, not pass."
            )
        published_bytes = sum(len(payload) for payload in inventory.values())
        if published_bytes < MINIMUM_BYTES:
            raise LiveSiteError(
                f"the comparison set holds {published_bytes} bytes, below the floor of "
                f"{MINIMUM_BYTES}. A check that compares nothing must fail, not pass."
            )
        origin = Origin(args.url, timeout_seconds=args.timeout_seconds)
        nonce = secrets.token_hex(16)
        prove_the_origin_discriminates(origin, nonce)
        differences = compare(origin, inventory, nonce)
    except LiveSiteError as exc:
        print(f"live integrity check could not run: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if differences:
        print(
            f"The live surface at {origin.url} is not what this checkout publishes.",
            file=sys.stderr,
        )
        for difference in differences:
            print(f"  {difference}", file=sys.stderr)
        print(
            "\nCheck the repository's Pages settings still serve docs/ from the default branch, and that the branch has been pushed.",
            file=sys.stderr,
        )
        return EXIT_DIFFERS

    total = sum(len(payload) for payload in inventory.values())
    print(
        f"{origin.url} serves exactly what this checkout publishes: "
        f"{len(inventory)} file(s), {total} bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
