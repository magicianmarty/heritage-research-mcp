"""Wikimedia Commons tools."""

from __future__ import annotations

from typing import Any

from ..compat import FastMCP
from ..sources import commons


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def commons_search(query: str, limit: int = 10, filetype: str | None = None) -> dict[str, Any]:
        """Search Wikimedia Commons files. Each record includes the licence read from the file's metadata.

        Args:
            query: Search words.
            limit: Results to return (1 to 50).
            filetype: bitmap, drawing, audio, video, office or multimedia.
        """
        return await commons.search(query, limit=limit, filetype=filetype)

    @mcp.tool()
    async def commons_file_info(title: str) -> dict[str, Any]:
        """Get one Commons file's licence, author, description, categories and original size.

        Args:
            title: The file name, with or without the "File:" prefix.
        """
        return await commons.file_info(title)

    @mcp.tool()
    async def commons_category_members(
        category: str, limit: int = 20, filetype: str | None = None
    ) -> dict[str, Any]:
        """List the files in a Commons category (for example "Mosby's Rangers").

        Args:
            category: The category name, with or without the "Category:" prefix.
            limit: Results to return (1 to 50).
            filetype: Optionally keep only bitmap, drawing, audio or video files.
        """
        return await commons.category_members(category, limit=limit, filetype=filetype)
