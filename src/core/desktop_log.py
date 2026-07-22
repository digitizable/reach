"""Optional desktop-side log file (no Spectre core required)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app_config import user_data_dir


def log_path() -> Path:
    return user_data_dir() / "desktop.log"


_MAX_LOG_BYTES = 512 * 1024  # rotate when larger than 512 KiB
_KEEP_LOG_BYTES = 256 * 1024


def write_log(message: str, *, enabled: bool = True, level: str = "info") -> None:
    if not enabled:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} [{level}] {message}\n"
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.is_file() and path.stat().st_size > _MAX_LOG_BYTES:
                raw = path.read_bytes()
                path.write_bytes(raw[-_KEEP_LOG_BYTES:])
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
