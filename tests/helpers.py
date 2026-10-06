"""Call tools through the MCP server the way a client does, on either SDK generation."""

from __future__ import annotations

import json
from typing import Any

from heritage_research_mcp.compat import ToolError


class ToolFailed(Exception):
    """A tool reported an error. `str(exc)` is what the model would read."""


def _text(content: Any) -> str:
    parts = [getattr(block, "text", "") for block in (content or [])]
    return "".join(parts)


async def call(server: Any, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        result = await server.call_tool(name, args or {})
    except ToolError as exc:  # SDK 1.x raises; its message carries ours
        raise ToolFailed(str(exc)) from exc
    if isinstance(result, tuple):  # 1.x: (content, structured)
        content, structured = result
        return structured if isinstance(structured, dict) else json.loads(_text(content))
    if getattr(result, "isError", False):  # 2.x
        raise ToolFailed(_text(result.content))
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    if isinstance(result, dict):
        return result
    return json.loads(_text(getattr(result, "content", result)))
