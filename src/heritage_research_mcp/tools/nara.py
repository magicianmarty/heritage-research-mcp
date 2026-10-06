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

        Args:
            q: Search words; supports AND, OR, NOT, wildcards (*) and "exact phrases".
            title: Words in the title.
            start_date: Earliest date (YYYY, YYYY-MM or YYYY-MM-DD).
            end_date: Latest date.
            available_online: Only records with digitised objects.
            type_of_materials: e.g. Photographs and other Graphic Materials, Textual Records, Maps.
            level: series, fileUnit, item, recordGroup, etc.
            record_group: Record group number, e.g. 109 (Confederate records).
            ancestor_na_id: Only records beneath this naId.
            geographic: Geographic subject heading.
            creators: Creator heading.
            include_extracted_text: Include OCR text in the results where NARA has it.
            limit: Results per page (1 to 100).
            page: Page number from 1.
            kind: text, image, map, audio or video, mapped to NARA's type of materials (unverified until a key
                has been used against the live service).
        """
        return await nara.search(
            q=q, title=title, start_date=start_date, end_date=end_date, available_online=available_online,
            type_of_materials=type_of_materials, level=level, record_group=record_group,
            ancestor_na_id=ancestor_na_id, geographic=geographic, creators=creators,
            include_extracted_text=include_extracted_text, limit=limit, page=page, kind=kind,
        )  # fmt: skip

    @mcp.tool()
    async def nara_get_record(na_id: int) -> dict[str, Any]:
        """Get one Catalog record by its numeric naId, with digital objects and ancestry. Needs NARA_API_KEY."""
        return await nara.get_record(na_id)

    @mcp.tool()
    async def nara_children(parent_na_id: int, limit: int = 20, page: int = 1) -> dict[str, Any]:
        """List the immediate children of a series or file unit (one level down). Needs NARA_API_KEY."""
        return await nara.children(parent_na_id, limit=limit, page=page)

    @mcp.tool()
    async def nara_extracted_text(
        na_id: int, object_id: int | None = None, limit: int = 5, page: int = 1
    ) -> dict[str, Any]:
        """Get OCR text extracted from a record's digital objects. Needs NARA_API_KEY.

        The response is passed through from NARA with long strings shortened to 4,000 characters.
        """
        return await nara.extracted_text(na_id, object_id=object_id, limit=limit, page=page)
