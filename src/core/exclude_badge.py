"""Window identity for excluded (clearnet) app instances.

Taskbars map windows via StartupWMClass. We:

  1. Give each excluded instance a unique WM class (``SpectreExclude-…``)
  2. Install a user .desktop file with that class and the **normal** app icon
     (no badge overlay)

Menu/dock launches of the real app are unchanged (different WM class).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from core.apps import RoutedApp


def exclude_wm_class(app: RoutedApp) -> str:
    """Stable X11/WM class for an excluded instance of *app*."""
    raw = (app.desktop_id or app.id or app.name or "app").strip()
    raw = raw.removesuffix(".desktop")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-")
    if not slug:
        slug = "app"
    slug = slug[:40]
    if slug[0].isdigit():
        slug = "a" + slug
    return f"SpectreExclude-{slug}"


def _applications_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "share"
    d = root / "applications"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _app_file_key(app: RoutedApp) -> str:
    raw = (app.id or app.desktop_id or app.name or "app").strip()
    raw = raw.removesuffix(".desktop")
    key = re.sub(r"[^\w.\-]+", "-", raw)[:80].strip("-") or "app"
    return key


def _desktop_path(app: RoutedApp) -> Path:
    return _applications_dir() / f"spectre-exclude-{_app_file_key(app)}.desktop"


def ensure_exclude_desktop_entry(
    app: RoutedApp,
    *,
    wm_class: str,
    exec_argv: list[str] | None = None,
) -> Path | None:
    """Install a user .desktop with the normal app icon (no badge).

    Never changes the real app's .desktop. Only writes spectre-exclude-*.desktop.
    """
    icon_field = (app.icon_name or "").strip() or "application-x-executable"

    if exec_argv:
        parts = []
        for a in exec_argv:
            if re.search(r'[\s"\\]', a):
                parts.append('"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"')
            else:
                parts.append(a)
        exec_line = " ".join(parts)
    else:
        exec_line = app.command

    name = f"{app.name} (clearnet)"
    comment = "Spectre excluded instance — clearnet / outside the path"
    path = _desktop_path(app)

    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Comment={comment}\n"
        f"Exec={exec_line}\n"
        f"Icon={icon_field}\n"
        "Terminal=False\n"
        "Categories=Network;\n"
        "NoDisplay=true\n"
        f"StartupWMClass={wm_class}\n"
        "StartupNotify=true\n"
        f"X-Spectre-Exclude=1\n"
        f"X-Spectre-App-Id={app.id}\n"
    )
    try:
        path.write_text(body, encoding="utf-8")
    except OSError:
        return None

    try:
        subprocess.run(
            ["update-desktop-database", str(_applications_dir())],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass

    return path


def prepare_exclude_window_identity(
    app: RoutedApp,
    argv: list[str],
) -> tuple[list[str], str]:
    """Apply unique WM class to *argv* and install .desktop with normal icon.

    Returns (argv_with_class, note).
    """
    wm_class = exclude_wm_class(app)
    out = list(argv)

    cleaned: list[str] = [out[0]]
    skip_next = False
    for a in out[1:]:
        if skip_next:
            skip_next = False
            continue
        if a in ("--class", "-class"):
            skip_next = True
            continue
        if a.startswith("--class=") or a.startswith("-class="):
            continue
        cleaned.append(a)
    out = cleaned

    exe = Path(out[0]).name.lower() if out else ""
    chromiumish = exe in {
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "google-chrome-beta",
        "brave-browser",
        "brave",
        "microsoft-edge",
        "microsoft-edge-stable",
        "vivaldi",
        "vivaldi-stable",
        "opera",
        "code",
        "code-oss",
        "codium",
        "cursor",
        "slack",
        "discord",
        "element-desktop",
        "signal-desktop",
        "spotify",
        "obsidian",
    }
    if chromiumish:
        out = [out[0], f"--class={wm_class}", *out[1:]]
    else:
        out = [out[0], "--class", wm_class, *out[1:]]

    desktop = ensure_exclude_desktop_entry(app, wm_class=wm_class, exec_argv=out)
    if desktop is not None:
        note = f"wm class {wm_class}"
    else:
        note = f"wm class {wm_class}"
    return out, note
