"""Where keys, cache and state live. Everything is read at call time so tests and users can change it."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .errors import NotConfigured

REPO_URL = "https://github.com/magicianmarty/heritage-research-mcp"

# source -> (environment variable, where to get a key)
KEYED_SOURCES: dict[str, tuple[str, str]] = {
    "dpla": ("DPLA_API_KEY", "https://pro.dp.la/developers/policies#get-a-key"),
    "nara": ("NARA_API_KEY", "https://www.archives.gov/research/catalog/help/api"),
    "smithsonian": ("SMITHSONIAN_API_KEY", "https://api.data.gov/signup/"),
}

_warned: set[str] = set()


def _home() -> Path:
    """The user's home, or the temp directory in a container that has none (no HOME and no passwd entry)."""
    try:
        return Path.home()
    except RuntimeError:
        return Path(tempfile.gettempdir())


def _base(env: str, fallback: str) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else _home() / fallback


def config_dir() -> Path:
    return _base("XDG_CONFIG_HOME", ".config") / "heritage-research-mcp"


def cache_dir() -> Path:
    override = os.environ.get("HERITAGE_MCP_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return _base("XDG_CACHE_HOME", ".cache") / "heritage-research-mcp"


def state_dir() -> Path:
    return _base("XDG_STATE_HOME", ".local/state") / "heritage-research-mcp"


def key_file(source: str) -> Path:
    return config_dir() / "keys" / source


def _read_key_file(source: str) -> str | None:
    path = key_file(source)
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if mode & 0o077 and str(path) not in _warned:
        _warned.add(str(path))
        print(
            f"heritage-research-mcp: {path} is readable by other users; run chmod 600 on it.", file=sys.stderr
        )
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def key_origin(source: str) -> str | None:
    """'env', 'file' or None. Never returns the key itself."""
    env_var = KEYED_SOURCES[source][0]
    if os.environ.get(env_var, "").strip():
        return "env"
    return "file" if _read_key_file(source) else None


def get_key(source: str) -> str | None:
    env_var = KEYED_SOURCES[source][0]
    value = os.environ.get(env_var, "").strip()
    return value or _read_key_file(source)


def require_key(source: str) -> str:
    key = get_key(source)
    if key:
        return key
    env_var, signup = KEYED_SOURCES[source]
    raise NotConfigured(source, env_var, signup, str(key_file(source)))


def user_agent() -> str:
    contact = os.environ.get("HERITAGE_MCP_CONTACT", "").strip()
    extra = f"; {contact}" if contact else ""
    return f"heritage-research-mcp/{__version__} (+{REPO_URL}{extra})"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def max_download_bytes() -> int:
    return _int_env("HERITAGE_MCP_MAX_DOWNLOAD_MB", 250) * 1024 * 1024


def nara_monthly_limit() -> int:
    return _int_env("NARA_MONTHLY_LIMIT", 10_000)


def nara_api_version() -> str:
    version = os.environ.get("NARA_API_VERSION", "v2").strip().lower()
    return version if version in {"v2", "v3"} else "v2"


def downloads_disabled() -> bool:
    """Hosted deployments cannot hand a downloaded file to anyone, so they can switch the tool off."""
    return os.environ.get("HERITAGE_MCP_DISABLE_DOWNLOADS", "").strip().lower() in {"1", "true", "yes", "on"}
