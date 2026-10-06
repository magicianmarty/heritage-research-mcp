"""Exceptions. Every message is safe to show to the model and the user: no keys, no raw URLs with secrets."""

from __future__ import annotations

from .compat import ToolError


class HeritageError(ToolError):  # pyright: ignore[reportGeneralTypeIssues]
    """Base class for errors the model can act on. A ToolError so the message reaches the model on every SDK."""


class NotConfigured(HeritageError):
    def __init__(self, source: str, env_var: str, signup_url: str, key_file: str) -> None:
        self.source = source
        super().__init__(
            f"{source} needs an API key. Get one at {signup_url}, then set {env_var} "
            f"or put the key on one line in {key_file}."
        )


class QuotaExceeded(HeritageError):
    pass


class SourceHTTPError(HeritageError):
    def __init__(self, source: str, status: int | None, detail: str) -> None:
        self.source = source
        self.status = status
        where = f" (HTTP {status})" if status else ""
        super().__init__(f"{source} request failed{where}: {detail}")


class NotFound(HeritageError):
    pass


class UnsafeURL(HeritageError):
    pass


class DownloadTooLarge(HeritageError):
    pass
