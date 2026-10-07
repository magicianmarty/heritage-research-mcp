"""National Archives Catalog tools (need a free API key)."""

from __future__ import annotations

from typing import Any

from ..compat import FastMCP
from ..sources import nara


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def nara_search(
        q: str | None = None,
        title: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        available_online: bool | None = None,
        type_of_materials: str | None = None,
        level: str | None = None,
        record_group: str | None = None,
        ancestor_na_id: int | None = None,
        geographic: str | None = None,
        creators: str | None = None,
        include_extracted_text: bool = False,
        limit: int = 10,
        page: int = 1,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Search the US National Archives Catalog. Needs NARA_API_KEY (10,000 queries a month by default).

        One page per call, at most 100 records: the API's terms forbid scraping or bulk download. The remaining
        monthly budget is visible in usage_report. Results include the attribution NARA requires.

        The first page also returns `facets`: the types of material and the six largest record groups among the
        hits (not every group), so a broad query can be narrowed with `record_group` or `type_of_materials`.
        Each record shows its record group and series; `nara_get_record` adds a ready-made citation.

        Args:
            q: Search words. Several words must all match, so use "double quotes" for an exact phrase
                ("Fairfax County" rather than Fairfax County, which also finds Fairfax, Oklahoma). OR, NOT and
                wildcards (mosb*) work. Avoid parentheses next to AND or OR: NARA's firewall often refuses them.
            title: Words in the title.
            start_date: Earliest date (YYYY, YYYY-MM or YYYY-MM-DD). NARA also matches records whose parent
                series or file spans the range, so results include undated records and some outside the range:
                read each record's own `date`.
            end_date: Latest date, same formats. Either bound alone is fine.
            available_online: Only records with a digital copy. A record without `media` has none online.
            type_of_materials: One of Textual Records, Photographs and other Graphic Materials, Maps and Charts,
                Moving Images, Sound Recordings, Architectural and Engineering Drawings, Data Files, Artifacts or
                Web Pages. Short forms such as map, photo or text are accepted.
            level: recordGroup, collection, series, fileUnit or item.
            record_group: Record group number, e.g. 109 (Confederate records) or 94 (Adjutant General's Office).
            ancestor_na_id: Only records beneath this naId (a series or file unit).
            geographic: Geographic subject heading, e.g. Virginia. Many records have none (maps and military
                files especially), so this can return nothing; a place name in `q` is more reliable.
            creators: Creator heading, e.g. Brady. Same caution: only records with that heading match.
            include_extracted_text: Add a 300-character OCR excerpt to each file that has OCR text. Full text:
                nara_extracted_text.
            limit: Results per page (1 to 100).
            page: Page number from 1.
            kind: text, image, map, audio or video, mapped to NARA's type of materials.
        """
        return await nara.search(
            q=q, title=title, start_date=start_date, end_date=end_date, available_online=available_online,
            type_of_materials=type_of_materials, level=level, record_group=record_group,
            ancestor_na_id=ancestor_na_id, geographic=geographic, creators=creators,
            include_extracted_text=include_extracted_text, limit=limit, page=page, kind=kind,
        )  # fmt: skip

    @mcp.tool()
    async def nara_get_record(na_id: int) -> dict[str, Any]:
        """Get one Catalog record by its numeric naId. Needs NARA_API_KEY.

        Returns the digital files (each with an `id` for nara_extracted_text, first 25 only on long files; a
        missing `bytes` means NARA's size was absent or a placeholder, not zero; no `media` means no digital
        copy is online), the
        use and access restrictions in NARA's own words, the series and record group, related links such as
        Fold3 or microfilm publications, and a `citation` string in archival form.
        """
        return await nara.get_record(na_id)

    @mcp.tool()
    async def nara_children(parent_na_id: int, limit: int = 20, page: int = 1) -> dict[str, Any]:
        """List the immediate children of a series or file unit (one level down). Needs NARA_API_KEY."""
        return await nara.children(parent_na_id, limit=limit, page=page)

    @mcp.tool()
    async def nara_extracted_text(
        na_id: int, object_id: int | None = None, limit: int = 5, page: int = 1, max_chars: int = 4000
    ) -> dict[str, Any]:
        """Get the text NARA holds for a record's scans, one entry per file. Needs NARA_API_KEY.

        Each file can have machine `ocr` and `transcriptions` contributed by NARA partners or volunteers (for
        example FamilySearch on pension files); some are AI-generated and flagged so. Treat both as a finding
        aid for names and places, not as a transcript to quote.

        Args:
            na_id: The record's naId.
            object_id: One file's `id` from the record's media list, to read just that scan.
            limit: Files per page (1 to 50).
            page: Page number from 1, for records with many scans.
            max_chars: Longest text returned per item (200 to 50,000); `ocr_chars` and `chars` give full lengths.
        """
        return await nara.extracted_text(
            na_id, object_id=object_id, limit=limit, page=page, max_chars=max_chars
        )
