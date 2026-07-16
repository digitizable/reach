"""Optional desktop-side log file (no Spectre core required)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app_config import user_data_dir


def log_path() -> Path:
    return user_data_dir() / "desktop.log"


def write_log(message: str, *, enabled: bool = True, level: str = "info") -> None:
    if not enabled:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} [{level}] {message}\n"
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
