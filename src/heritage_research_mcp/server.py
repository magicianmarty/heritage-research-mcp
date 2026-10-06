"""Heritage Research MCP server entry point."""

from __future__ import annotations

import sys

from . import __version__, guard
from .compat import FastMCP
from .tools import commons, dpla, general, internet_archive, nara, smithsonian

mcp = FastMCP(
    "Heritage Research",
    instructions=(
        "Historical research across the Internet Archive, Wikimedia Commons, the Digital Public Library of "
        "America (DPLA), the US National Archives Catalog (NARA) and Smithsonian Open Access.\n\n"
        "HOW TO USE IT:\n"
        "• list_sources shows which archives are ready. The Internet Archive and Commons need no key; DPLA, NARA and "
        "Smithsonian need a free key and are skipped until one is set.\n"
        "• search queries every ready archive at once. Add fulltext=true to also search the text inside Internet "
        "Archive books, which is how to find a name or place inside a memoir or official report.\n"
        "• ia_grep_text finds a phrase inside one book and ia_read_text reads around it.\n"
        "• get_record fetches one record; download_media caches an original with a provenance sidecar.\n\n"
        "RIGHTS: every record has rights.reuse (free, attribution, share_alike, non_commercial, no_derivatives, "
        "restricted, unknown). 'unknown' means the holder stated nothing, not that reuse is allowed. Never present "
        "an item as free to reuse unless rights.reuse says so, and keep rights.attribution with anything you reuse.\n\n"
        "LIMITS: NARA allows 10,000 requests a month and forbids bulk download, so prefer targeted queries. "
        "Do not loop over many pages to harvest a collection."
    ),
)

guard.install(mcp)

general.register(mcp)
internet_archive.register(mcp)
commons.register(mcp)
dpla.register(mcp)
nara.register(mcp)
smithsonian.register(mcp)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"--version", "-V"}:
        print(f"heritage-research-mcp {__version__}")
        return
    if args and args[0] == "doctor":
        from .doctor import run

        raise SystemExit(run(live="--live" in args[1:]))
    mcp.run()


if __name__ == "__main__":
    main()
