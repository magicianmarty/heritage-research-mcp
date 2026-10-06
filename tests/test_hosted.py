"""Behaviour in a hosted sandbox: no home directory, a read-only disk, and unexpected failures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from conftest import load
from helpers import ToolFailed, call
from heritage_research_mcp import config, download
from heritage_research_mcp.errors import HeritageError
from heritage_research_mcp.guard import guarded
from heritage_research_mcp.server import mcp


@pytest.fixture
def container(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A user with no HOME, no passwd entry and none of the XDG or project variables."""

    def no_home(cls: type[Path]) -> Path:
        raise RuntimeError("Could not determine home directory.")

    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "HERITAGE_MCP_CACHE_DIR", "HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(no_home))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def test_directories_fall_back_to_the_temp_folder(container: Path) -> None:
    assert config.config_dir() == container / ".config" / "heritage-research-mcp"
    assert config.cache_dir() == container / ".cache" / "heritage-research-mcp"
    assert config.state_dir() == container / ".local" / "state" / "heritage-research-mcp"
    assert config.get_key("dpla") is None


async def test_every_kind_of_tool_works_without_a_home_directory(api, container: Path, set_key) -> None:
    set_key("dpla")
    sources = (await call(mcp, "list_sources"))["sources"]
    assert sources["dpla"]["configured"] is True and sources["nara"]["configured"] is False
    api.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json=load("commons_search.json"))
    )
    out = await call(mcp, "commons_search", {"query": "mosby"})
    assert out["returned"] == 2
    assert (await call(mcp, "usage_report"))["this_session"]["commons"] == 1


async def test_an_unwritable_cache_is_explained(api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where a folder should be")
    monkeypatch.setenv("HERITAGE_MCP_CACHE_DIR", str(blocker))

    async def public(_host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(download, "resolve", public)
    api.get("https://files.example.org/a.jpg").mock(return_value=httpx.Response(200, content=b"x"))
    with pytest.raises(HeritageError) as caught:
        await download.download_file(source="commons", record_id="r", url="https://files.example.org/a.jpg")
    assert "HERITAGE_MCP_CACHE_DIR" in str(caught.value)


async def test_an_unexpected_failure_names_itself_and_hides_any_key(api) -> None:
    api.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(
            200,
            json={"query": {"pages": [{"title": "File:X", "imageinfo": [{"extmetadata": "not a mapping"}]}]}},
        )
    )
    with pytest.raises(ToolFailed) as caught:
        await call(mcp, "commons_search", {"query": "x"})
    assert "failed unexpectedly" in str(caught.value) and "commons_search" in str(caught.value)


async def test_the_guard_redacts_secrets_in_unexpected_errors() -> None:
    async def boom() -> None:
        raise ValueError("bad url https://x/?api_key=SECRET123&q=1")

    with pytest.raises(HeritageError) as caught:
        await guarded(boom)()
    assert "SECRET123" not in str(caught.value) and "ValueError" in str(caught.value)


async def test_wrapped_tools_keep_their_parameters_and_descriptions() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}

    def schema_of(tool: object) -> dict:
        return getattr(tool, "inputSchema", None) or tool.input_schema  # 1.x and 2.x spell it differently

    schema = schema_of(tools["search"])
    assert {"query", "sources", "limit", "kind", "fulltext"} <= set(schema["properties"])
    assert schema["required"] == ["query"]
    assert "rights.reuse" in (tools["search"].description or "")
    assert "kind" in schema_of(tools["ia_search"])["properties"]
