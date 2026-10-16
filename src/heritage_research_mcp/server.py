"""Heritage Research MCP server entry point."""

from __future__ import annotations

import sys

from . import __version__, guard
from .compat import FastMCP
from .tools import chronam, commons, dispatch, dpla, general, internet_archive, nara, smithsonian

mcp = FastMCP(
    "Heritage Research",
    instructions=(
        "Historical research across the Internet Archive, Wikimedia Commons, the Digital Public Library of "
        "America (DPLA), the US National Archives Catalog (NARA), Smithsonian Open Access and the Richmond Daily "
        "Dispatch.\n\n"
        "HOW TO USE IT:\n"
        "• list_sources shows which archives are ready. The Internet Archive and Commons need no key; DPLA, NARA and "
        "Smithsonian need a free key and are skipped until one is set.\n"
        "• search queries every ready archive at once. Add fulltext=true to also search the text inside Internet "
        "Archive books, which is how to find a name or place inside a memoir or official report.\n"
        "• ia_grep_text finds a phrase inside one book and ia_read_text reads around it.\n"
        "• get_record fetches one record; download_media caches an original with a provenance sidecar.\n"
        "• chronam_search finds digitised US newspaper pages (Chronicling America) by words, dates and state; "
        "chronam_page_text reads a page's raw OCR. It is rate limited to one request per 3 s and may be blocked "
        "by the Library of Congress for a while: if it says so, wait rather than retry.\n"
        "• To work with whole years of newspapers (say all Virginia papers of 1863), plan a local index with "
        "chronam_bulk_plan, build it with chronam_bulk_start (a large download: tell the user the size first), "
        "then search it with chronam_local_search. Searching the index makes no requests.\n"
        "• The Richmond Daily Dispatch (1860-65) is searched from a local index: run dispatch_prepare once "
        "(about 92 MB), then dispatch_search, dispatch_get_article and dispatch_issue.\n"
        "• For federal records (maps, photographs, military files) use nara_search: its `facets` show which "
        "record groups hold the hits so you can narrow with record_group; nara_get_record adds an archival "
        "citation; nara_extracted_text gives each scan's OCR and any partner transcription.\n\n"
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
dispatch.register(mcp)
chronam.register(mcp)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"--version", "-V"}:
        print(f"heritage-research-mcp {__version__}")
        return
    if args and args[0] == "doctor":
        from .doctor import run

        raise SystemExit(run(live="--live" in args[1:]))
    if args and args[0] == "chronam-index":
        from .cli import run_chronam_index

        raise SystemExit(run_chronam_index(args[1:]))
    if args and args[0] == "dispatch-index":
        from .cli import run_dispatch_index

        raise SystemExit(run_dispatch_index(args[1:]))
    if args and args[0] == "download":
        from .cli import run as download

        raise SystemExit(download(args[1:]))
    mcp.run()


if __name__ == "__main__":
    main()
