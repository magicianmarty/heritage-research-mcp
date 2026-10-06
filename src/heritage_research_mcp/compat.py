# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
"""Run on both MCP SDK generations.

1.x ships FastMCP; 2.x renamed it MCPServer. In 2.x only a `ToolError` reaches the model with its message;
any other exception is reported as a crash, so every error we expect subclasses it.

Each environment can resolve only one of the two import paths, so the type checker is told not to mind.
"""

from __future__ import annotations

try:
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError as _ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError as _ToolError

FastMCP = _Server
ToolError = _ToolError

__all__ = ["FastMCP", "ToolError"]
