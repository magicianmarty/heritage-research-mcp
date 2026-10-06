"""Make unexpected failures readable.

MCP SDK 2.x tells the model only "Error executing tool X" for any exception that is not a ToolError, which
hides the cause from the model and from whoever is debugging a hosted deployment. Every tool is wrapped so
an unexpected exception becomes a HeritageError that names the exception, with any key redacted.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from .errors import HeritageError
from .http import redact


def guarded(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    signature = inspect.signature(fn, eval_str=True)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except HeritageError:
            raise
        except Exception as exc:
            raise HeritageError(
                redact(f"{fn.__name__} failed unexpectedly: {type(exc).__name__}: {exc}")
            ) from exc

    wrapper.__signature__ = signature  # type: ignore[attr-defined]
    wrapper.__annotations__ = {
        name: param.annotation
        for name, param in signature.parameters.items()
        if param.annotation is not param.empty
    } | (
        {"return": signature.return_annotation} if signature.return_annotation is not signature.empty else {}
    )
    return wrapper


def install(server: Any) -> None:
    """Wrap every tool registered on `server` from now on."""
    original = server.tool

    def tool(*args: Any, **kwargs: Any) -> Callable[[Callable[..., Awaitable[Any]]], Any]:
        decorate = original(*args, **kwargs)
        return lambda fn: decorate(guarded(fn))

    server.tool = tool
