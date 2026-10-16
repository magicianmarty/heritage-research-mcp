"""`heritage-research-mcp download`: fetch one original from a record, for a host that can keep the file."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .errors import HeritageError
from .sources import SOURCES
from .sources import chronam_bulk as bulk_src
from .sources import dispatch as dispatch_src
from .tools.general import download_from_record

_KINDS = ["pdf", "text", "image", "audio", "video", "archive"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heritage-research-mcp download",
        description="Download one original file from a record into the cache and write a provenance sidecar.",
    )
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("record_id", help="the id a search result returned")
    parser.add_argument(
        "--media-index", type=int, default=0, help="which entry of the record's media to fetch"
    )
    parser.add_argument("--kind", choices=_KINDS, help="take the first media of this kind instead")
    parser.add_argument("--max-mb", type=int, help="refuse files larger than this")
    parser.add_argument("--overwrite", action="store_true", help="fetch again even if already cached")
    return parser


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    out: dict[str, Any]
    try:
        out = asyncio.run(
            download_from_record(
                args.source, args.record_id, args.media_index, args.kind, args.overwrite, args.max_mb
            )
        )
        code = 0
    except HeritageError as exc:
        out = {"error": str(exc)}
        code = 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return code


def run_dispatch_index(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="heritage-research-mcp dispatch-index",
        description="Download the Richmond Daily Dispatch TEI archive (about 92 MB) and build the local search index.",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="download and index again even if an index exists"
    )
    args = parser.parse_args(argv)
    try:
        out = asyncio.run(dispatch_src.prepare(refresh=args.refresh))
        code = 0
    except HeritageError as exc:
        out = {"error": str(exc)}
        code = 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return code


def run_chronam_index(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="heritage-research-mcp chronam-index",
        description="Build a local full-text index of Chronicling America newspaper pages for a range of years "
        "from the Library of Congress's bulk OCR archives. Large downloads: run --plan first.",
    )
    parser.add_argument(
        "--from", dest="date_from", required=True, help="first date: YYYY, YYYY-MM or YYYY-MM-DD"
    )
    parser.add_argument("--to", dest="date_to", required=True, help="last date, same formats")
    parser.add_argument("--state", help="only newspapers published in this US state, e.g. virginia")
    parser.add_argument("--lccn", action="append", help="only this newspaper (repeatable), e.g. sn84024738")
    parser.add_argument(
        "--max-gb", type=float, default=bulk_src.DEFAULT_MAX_GB, help="refuse larger downloads"
    )
    parser.add_argument("--plan", action="store_true", help="show what would be downloaded and stop")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="read every archive to the end and check its SHA-256 (slower; by default reading stops once the "
        "range has been passed)",
    )
    args = parser.parse_args(argv)

    def progress(done: int, total: int, current: str | None, pages: int) -> None:
        print(
            f"[{done}/{total}] {current or 'finished'}: {pages} pages kept so far",
            file=sys.stderr,
            flush=True,
        )

    async def go() -> dict[str, Any]:
        planned = await bulk_src.plan(args.date_from, args.date_to, state=args.state, lccns=args.lccn)
        if args.plan:
            return planned
        if planned["download_gb"] > args.max_gb:
            raise HeritageError(
                f"this would download {planned['download_gb']} GB, over --max-gb {args.max_gb:g}. "
                "Narrow it with --state or --lccn, or raise --max-gb."
            )
        low, high = planned["range"]
        chosen = await bulk_src.select(low, high, args.state, args.lccn)
        done = await asyncio.to_thread(
            bulk_src.run_job, chosen, low, high, bool(args.state or args.lccn), progress, args.verify
        )
        return {**done, "index": bulk_src.summary()}

    try:
        out = asyncio.run(go())
        code = 0
    except HeritageError as exc:
        out = {"error": str(exc)}
        code = 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return code
