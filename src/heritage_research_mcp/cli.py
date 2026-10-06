"""`heritage-research-mcp download`: fetch one original from a record, for a host that can keep the file."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from .errors import HeritageError
from .sources import SOURCES
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
