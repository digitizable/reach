"""Single source of truth for app identity and paths."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

APPLICATION_ID = "com.digitizable.reach"
APPLICATION_NAME = "Reach"
APPLICATION_VERSION = "0.6.0"
APPLICATION_ICON = APPLICATION_ID

# Upstream for update checks (GitHub Releases)
GITHUB_OWNER = "digitizable"
GITHUB_REPO = "reach"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"

# Legacy (pre-rename Spectre Desktop)
_LEGACY_DIR_NAME = "spectre-desktop"
_DIR_NAME = "reach"


def src_dir() -> Path:
    return Path(__file__).resolve().parent


def project_root() -> Path:
    env = os.environ.get("REACH_ROOT") or os.environ.get("SPECTRE_DESKTOP_ROOT")
    if env:
        return Path(env).resolve()
    return src_dir().parent


def ensure_import_path() -> None:
    """Allow launching via `python src/main.py` or the desktop launcher."""
    import sys

    path = str(src_dir())
    if path not in sys.path:
        sys.path.insert(0, path)


def _migrate_legacy_dir(base: Path) -> Path:
    """Prefer ~/.…/reach; one-shot rename from spectre-desktop when present."""
    path = base / _DIR_NAME
    legacy = base / _LEGACY_DIR_NAME
    if not path.exists() and legacy.exists():
        try:
            legacy.rename(path)
        except OSError:
            # Cross-device or busy: copy tree once, leave legacy in place.
            try:
                shutil.copytree(legacy, path)
            except OSError:
                path = legacy
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return _migrate_legacy_dir(root)


def user_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return _migrate_legacy_dir(root)
