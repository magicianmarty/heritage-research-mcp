"""Request counters. Persisted per month so a provider's monthly quota (NARA: 10,000) survives restarts."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import config
from .errors import QuotaExceeded


class Usage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: dict[str, int] = defaultdict(int)
        self._rate: dict[str, dict[str, str]] = {}

    @staticmethod
    def _month() -> str:
        return dt.datetime.now(dt.UTC).strftime("%Y-%m")

    @staticmethod
    def _path() -> Path:
        return config.state_dir() / "usage.json"

    def _load(self) -> dict[str, dict[str, int]]:
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, int]]) -> None:
        path = self._path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def record(self, source: str) -> None:
        with self._lock:
            self._session[source] += 1
            data = self._load()
            month = data.setdefault(self._month(), {})
            month[source] = int(month.get(source, 0)) + 1
            for old in sorted(data)[:-12]:
                del data[old]
            self._save(data)

    def monthly(self, source: str) -> int:
        with self._lock:
            return int(self._load().get(self._month(), {}).get(source, 0))

    def check(self, source: str, limit: int) -> None:
        used = self.monthly(source)
        if used >= limit:
            raise QuotaExceeded(
                f"{source}: {used} requests used this month, at the configured limit of {limit}. "
                "The counter resets on the first of the month (UTC)."
            )

    def note_rate_headers(self, source: str, headers: Mapping[str, str]) -> None:
        seen = {
            "limit": headers.get("x-ratelimit-limit"),
            "remaining": headers.get("x-ratelimit-remaining"),
        }
        found = {k: v for k, v in seen.items() if v is not None}
        if found:
            self._rate[source] = found

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            month = self._load().get(self._month(), {})
            return {
                "month": self._month(),
                "this_month": dict(month),
                "this_session": dict(self._session),
                "provider_rate_limit": dict(self._rate),
            }


usage = Usage()
