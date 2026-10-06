"""Shared fixtures. Every test runs against an empty home, no keys and no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx

from heritage_research_mcp.sources import internet_archive as ia_src
from heritage_research_mcp.usage import usage

FIXTURES = Path(__file__).parent / "fixtures"

_ENV_TO_CLEAR = (
    "DPLA_API_KEY",
    "NARA_API_KEY",
    "SMITHSONIAN_API_KEY",
    "HERITAGE_MCP_CONTACT",
    "NARA_API_VERSION",
    "NARA_MONTHLY_LIMIT",
    "HERITAGE_MCP_MAX_DOWNLOAD_MB",
)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("HERITAGE_MCP_CACHE_DIR", str(tmp_path / "cache"))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("heritage_research_mcp.http.sleep", no_sleep)
    ia_src._text_cache.clear()
    usage._session.clear()
    usage._rate.clear()


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def set_key(monkeypatch: pytest.MonkeyPatch):
    def _set(source: str, value: str = "test-key-123") -> str:
        from heritage_research_mcp import config

        monkeypatch.setenv(config.KEYED_SOURCES[source][0], value)
        return value

    return _set
