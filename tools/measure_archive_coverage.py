#!/usr/bin/env python3
"""Ask the Internet Archive what it holds for every registry URL, and write it down.

    python3 tools/measure_archive_coverage.py

Writes `docs/evidence/archive-coverage.json` and `docs/ARCHIVE-COVERAGE.md`, and
caches the raw CDX answers in `var/archive-cdx-raw.json` so a re-run resumes rather
than re-asking. This is an operator's diagnostic, never a gate: it needs the network,
and a merge gate that needs a third party's uptime is a merge gate that will fail on
somebody else's bad day.

## It is deliberately slow

The first run of this measurement used five concurrent workers. The Archive refused
**136 of 156** connections with `Connection refused`, and every one of those refusals
would have become "this page has no captures" in a naive count. So: one request at a
time, three seconds between them, four attempts with exponential backoff, and a
failure recorded as a failure. Roughly forty minutes for the whole registry. That is
the correct speed for asking a free public service 156 questions.

Resuming keeps successful answers and retries only failures, so a partial run is
never mistaken for a complete one — `--fresh` discards the cache.

## What it does not do

No Save Page Now, no writes, no account, no credential: this reads a public index.
It also never touches `sources/registry.json`, the snapshot store, or any published
feed artifact. The only files it writes are its own two outputs and its cache.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from id_churn_sentinel.core.archive_coverage import (  # noqa: E402
    measure,
    render_markdown,
)
from id_churn_sentinel.core.registry import load_registry  # noqa: E402

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
DEFAULT_ROW_LIMIT = 2000
REQUEST_TIMEOUT_SECONDS = 120.0
SECONDS_BETWEEN_REQUESTS = 3.0
MAX_ATTEMPTS = 4

USER_AGENT = (
    "id-churn-sentinel archive-coverage measurement "
    "(+https://github.com/ChelseaKR/id-churn-sentinel)"
)

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "var" / "archive-cdx-raw.json"
JSON_OUT = ROOT / "docs" / "evidence" / "archive-coverage.json"
MARKDOWN_OUT = ROOT / "docs" / "ARCHIVE-COVERAGE.md"


def query_cdx(url: str, *, row_limit: int) -> dict[str, Any]:
    """One CDX query. Returns the answer, or a recorded failure — never a guess."""
    params = urllib.parse.urlencode(
        {
            "url": url,
            "output": "json",
            "fl": "timestamp,statuscode,digest,mimetype",
            "limit": str(row_limit),
        }
    )
    last_error = "not attempted"
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(min(60.0, 5.0 * (2**attempt)))
        request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
            f"{CDX_ENDPOINT}?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
                body = response.read(8_000_000).decode("utf-8", errors="replace")
            rows = json.loads(body) if body.strip() else []
            return {"ok": True, "rows": rows, "limit": row_limit, "attempts": attempt + 1}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return {"ok": False, "error": last_error, "attempts": MAX_ATTEMPTS}


def collect(*, row_limit: int, fresh: bool) -> dict[str, Any]:
    registry = load_registry()
    cached: dict[str, Any] = {}
    if CACHE_PATH.exists() and not fresh:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    todo = [s for s in registry.sources if not cached.get(s.id, {}).get("ok")]
    print(f"{len(todo)} of {len(registry.sources)} sources to collect", flush=True)

    for n, source in enumerate(todo, start=1):
        cached[source.id] = query_cdx(source.url, row_limit=row_limit)
        ok = cached[source.id].get("ok")
        print(f"[{n}/{len(todo)}] {source.id} ok={ok}", flush=True)
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cached, indent=2, sort_keys=True), encoding="utf-8")
        time.sleep(SECONDS_BETWEEN_REQUESTS)

    return cached


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row-limit", type=int, default=DEFAULT_ROW_LIMIT)
    parser.add_argument(
        "--fresh", action="store_true", help="discard the cache and re-ask every source"
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="regenerate the report from the cache without any network access",
    )
    args = parser.parse_args(argv)

    if args.report_only:
        if not CACHE_PATH.exists():
            print(f"no cache at {CACHE_PATH}; run without --report-only first", file=sys.stderr)
            return 2
        answers = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    else:
        answers = collect(row_limit=args.row_limit, fresh=args.fresh)

    registry = load_registry()
    report = measure(
        registry,
        answers,
        measured_on=datetime.now(UTC).date().isoformat(),
        row_limit=args.row_limit,
    )

    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(report.to_json(), encoding="utf-8")
    MARKDOWN_OUT.write_text(render_markdown(report), encoding="utf-8")

    print(f"\nwrote {JSON_OUT.relative_to(ROOT)} and {MARKDOWN_OUT.relative_to(ROOT)}")
    for outcome, count in sorted(report.counts_by_outcome.items()):
        print(f"  {outcome}: {count}")
    print(f"  captured but no usable (HTTP 200) capture: {report.n_captured_but_none_usable}")
    print(f"  unfetchable by us: {report.unfetchable_by_us}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
