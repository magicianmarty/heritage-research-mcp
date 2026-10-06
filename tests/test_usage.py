from __future__ import annotations

import json

import pytest

from heritage_research_mcp import config
from heritage_research_mcp.errors import QuotaExceeded
from heritage_research_mcp.usage import Usage


def test_counts_persist_between_instances() -> None:
    first = Usage()
    first.record("nara")
    first.record("nara")
    first.record("dpla")
    second = Usage()
    assert second.monthly("nara") == 2
    assert second.monthly("dpla") == 1
    assert second.monthly("commons") == 0


def test_session_counts_are_separate_from_monthly() -> None:
    Usage().record("nara")
    fresh = Usage()
    assert fresh.snapshot()["this_session"] == {}
    assert fresh.snapshot()["this_month"] == {"nara": 1}


def test_check_raises_at_the_limit() -> None:
    counter = Usage()
    counter.check("nara", 2)
    counter.record("nara")
    counter.check("nara", 2)
    counter.record("nara")
    with pytest.raises(QuotaExceeded) as caught:
        counter.check("nara", 2)
    assert "resets on the first of the month" in str(caught.value)


def test_old_months_are_pruned() -> None:
    path = config.state_dir() / "usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({f"2024-{m:02d}": {"nara": 1} for m in range(1, 13)} | {"2025-01": {"nara": 1}})
    )
    Usage().record("nara")
    kept = json.loads(path.read_text())
    assert len(kept) == 12
    assert "2024-01" not in kept and "2024-02" not in kept


def test_a_corrupt_state_file_is_ignored() -> None:
    path = config.state_dir() / "usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    counter = Usage()
    assert counter.monthly("nara") == 0
    counter.record("nara")
    assert counter.monthly("nara") == 1
