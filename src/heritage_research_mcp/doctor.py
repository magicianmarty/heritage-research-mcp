"""`heritage-research-mcp doctor [--live]`: show what is configured and optionally try each source."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from . import __version__, config
from .errors import Blocked, HeritageError
from .sources import SOURCES, is_configured
from .sources import chronam_bulk as bulk_src
from .sources import chronicling_america as chronam_src
from .sources import commons as commons_src
from .sources import dispatch as dispatch_src
from .sources import dpla as dpla_src
from .sources import internet_archive as ia_src
from .sources import nara as nara_src
from .sources import smithsonian as si_src

_PROBES: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
    "internet_archive": lambda: ia_src.search("mosby", rows=1),
    "commons": lambda: commons_src.search("Mosby", limit=1),
    "dpla": lambda: dpla_src.search(q="mosby", page_size=1),
    "nara": lambda: nara_src.search(q="mosby", limit=1),
    "smithsonian": lambda: si_src.search("mosby", rows=1),
    "dispatch": lambda: dispatch_src.search("mosby", rows=1),
    "chronam": lambda: chronam_src.search("mosby", rows=1),
    "chronam_local": lambda: bulk_src.search("mosby", rows=1),
}


async def _probe(name: str) -> str:
    started = time.monotonic()
    try:
        result = await _PROBES[name]()
    except Blocked as exc:
        return f"BLOCKED  {exc}"
    except HeritageError as exc:
        return f"FAIL  {exc}"
    except Exception as exc:  # network or parsing surprises should not hide the other sources
        return f"FAIL  {type(exc).__name__}: {exc}"
    count = len(result.get("records") or [])
    return f"ok    {count} record(s) in {time.monotonic() - started:.1f}s"


async def _live() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in SOURCES:
        out[name] = (
            await _probe(name) if is_configured(name) else f"skipped ({SOURCES[name].setup or 'no key'})"
        )
    return out


def run(*, live: bool) -> int:
    print(f"heritage-research-mcp {__version__}")
    print(f"  keys:  {config.config_dir() / 'keys'}")
    print(f"  cache: {config.cache_dir()}")
    print(f"  state: {config.state_dir()}\n")
    results = asyncio.run(_live()) if live else {}
    failed = False
    for name, info in SOURCES.items():
        if name == "dispatch":
            state = (
                "local index ready"
                if is_configured(name)
                else "NO INDEX: run dispatch_prepare or `heritage-research-mcp dispatch-index`"
            )
        elif name == "chronam_local":
            state = (
                "local index ready"
                if is_configured(name)
                else "NO INDEX: see `heritage-research-mcp chronam-index --help`"
            )
        elif info.keyed:
            origin = config.key_origin(name)
            state = f"key from {origin}" if origin else f"NO KEY: {config.KEYED_SOURCES[name][1]}"
        else:
            state = "no key needed"
        line = f"  {name:<17} {state}"
        if live:
            verdict = results[name]
            failed = failed or verdict.startswith("FAIL")
            line += f"\n  {'':<17} {verdict}"
        print(line)
    if not live:
        print("\nRun with --live to make one small request to each configured source.")
    return 1 if failed else 0
