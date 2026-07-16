"""Unit tests for version parsing / schedule (no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.updates import (
    parse_version,
    should_check_now,
    version_is_newer,
)


def test_parse_version() -> None:
    assert parse_version("0.1.0") == (0, 1, 0)
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("2.0.0-beta.1") == (2, 0, 0)


def test_version_is_newer() -> None:
    assert version_is_newer("0.2.0", "0.1.0")
    assert version_is_newer("1.0.0", "0.9.9")
    assert not version_is_newer("0.1.0", "0.1.0")
    assert not version_is_newer("0.1.0", "0.2.0")
    assert version_is_newer("v0.1.1", "0.1.0")


def test_should_check_now() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    assert should_check_now(enabled=False, last_check_iso="", now=now) is False
    assert should_check_now(enabled=True, last_check_iso="", now=now) is True
    recent = (now - timedelta(hours=1)).isoformat()
    assert (
        should_check_now(
            enabled=True,
            last_check_iso=recent,
            interval_hours=24,
            now=now,
        )
        is False
    )
    old = (now - timedelta(hours=25)).isoformat()
    assert (
        should_check_now(
            enabled=True,
            last_check_iso=old,
            interval_hours=24,
            now=now,
        )
        is True
    )
