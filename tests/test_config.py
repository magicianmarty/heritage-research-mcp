from __future__ import annotations

from pathlib import Path

import pytest

from heritage_research_mcp import __version__, config
from heritage_research_mcp.errors import NotConfigured


def write_key(source: str, text: str, mode: int = 0o600) -> Path:
    path = config.key_file(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_no_key_anywhere() -> None:
    assert config.get_key("dpla") is None
    assert config.key_origin("dpla") is None


def test_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPLA_API_KEY", "  from-env  ")
    assert config.get_key("dpla") == "from-env"
    assert config.key_origin("dpla") == "env"


def test_key_file_skips_comments_and_blank_lines() -> None:
    write_key("nara", "\n# the NARA key\n\nabc123\nsecond-line-ignored\n")
    assert config.get_key("nara") == "abc123"
    assert config.key_origin("nara") == "file"


def test_environment_wins_over_file(monkeypatch: pytest.MonkeyPatch) -> None:
    write_key("smithsonian", "from-file")
    monkeypatch.setenv("SMITHSONIAN_API_KEY", "from-env")
    assert config.get_key("smithsonian") == "from-env"


def test_empty_key_file_is_not_a_key() -> None:
    write_key("dpla", "# nothing yet\n")
    assert config.get_key("dpla") is None


def test_missing_key_error_explains_how_to_fix_it_and_leaks_nothing() -> None:
    with pytest.raises(NotConfigured) as caught:
        config.require_key("nara")
    message = str(caught.value)
    assert "NARA_API_KEY" in message
    assert "https://www.archives.gov/research/catalog/help/api" in message
    assert str(config.key_file("nara")) in message


def test_loose_permissions_are_reported_once(capsys: pytest.CaptureFixture[str]) -> None:
    write_key("dpla", "abc", mode=0o644)
    config.get_key("dpla")
    config.get_key("dpla")
    assert capsys.readouterr().err.count("readable by other users") == 1


def test_strict_permissions_are_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    write_key("dpla", "abc", mode=0o600)
    config.get_key("dpla")
    assert capsys.readouterr().err == ""


def test_user_agent_names_the_project_and_optional_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.user_agent() == f"heritage-research-mcp/{__version__} (+{config.REPO_URL})"
    monkeypatch.setenv("HERITAGE_MCP_CONTACT", "ops@example.org")
    assert config.user_agent().endswith("; ops@example.org)")


def test_numeric_settings_fall_back_on_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.max_download_bytes() == 250 * 1024 * 1024
    monkeypatch.setenv("HERITAGE_MCP_MAX_DOWNLOAD_MB", "5")
    assert config.max_download_bytes() == 5 * 1024 * 1024
    monkeypatch.setenv("HERITAGE_MCP_MAX_DOWNLOAD_MB", "lots")
    assert config.max_download_bytes() == 250 * 1024 * 1024
    monkeypatch.setenv("NARA_MONTHLY_LIMIT", "oops")
    assert config.nara_monthly_limit() == 10_000


def test_nara_version_only_accepts_known_values(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.nara_api_version() == "v2"
    monkeypatch.setenv("NARA_API_VERSION", "V3")
    assert config.nara_api_version() == "v3"
    monkeypatch.setenv("NARA_API_VERSION", "v9")
    assert config.nara_api_version() == "v2"
