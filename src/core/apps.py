"""Routed applications — system discovery + optional custom entries."""

from __future__ import annotations

import json
import re
import shlex
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app_config import user_data_dir

# Source markers
SOURCE_CUSTOM = "custom"
SOURCE_SYSTEM = "system"

_DEFAULT_ICON = "application-x-executable-symbolic"


@dataclass
class RoutedApp:
    """An application that can be launched excluded from the Spectre path."""

    id: str
    name: str
    command: str  # executable + args (shell-safe via shlex)
    enabled: bool = True
    desktop_file: str = ""  # optional .desktop path
    icon_name: str = _DEFAULT_ICON
    mode: str = "env"  # env | proxychains
    notes: str = ""
    source: str = SOURCE_CUSTOM  # custom | system
    desktop_id: str = ""  # e.g. firefox.desktop (stable system key)

    def argv(self) -> list[str]:
        try:
            parts = shlex.split(self.command, posix=True)
        except ValueError:
            parts = self.command.split()
        if not parts:
            raise ValueError("Command is empty")
        return parts

    @property
    def is_system(self) -> bool:
        return self.source == SOURCE_SYSTEM

    @property
    def is_custom(self) -> bool:
        return self.source != SOURCE_SYSTEM


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "app"


def strip_desktop_field_codes(cmd: str) -> str:
    """Remove .desktop Exec field codes (%u, %F, …)."""
    cmd = re.sub(r"\s+%[a-zA-Z@]", "", cmd).strip()
    cmd = re.sub(r"%[a-zA-Z@]", "", cmd).strip()
    return cmd


def parse_desktop_file(path: Path) -> dict[str, str]:
    """Minimal .desktop parser for Name / Exec / Icon / visibility."""
    name = path.stem
    exec_cmd = ""
    icon = _DEFAULT_ICON
    no_display = False
    hidden = False
    entry_type = "Application"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"Cannot read desktop file: {exc}") from exc
    in_entry = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_entry = line.lower() in ("[desktop entry]",)
            continue
        if not in_entry or not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key == "Name" and val:
            name = val
        elif key == "Exec" and val:
            exec_cmd = strip_desktop_field_codes(val)
        elif key == "Icon" and val:
            icon = val
        elif key == "Type" and val:
            entry_type = val
        elif key == "NoDisplay":
            no_display = val.lower() in ("true", "1", "yes")
        elif key == "Hidden":
            hidden = val.lower() in ("true", "1", "yes")
    if hidden or no_display:
        raise ValueError("Desktop entry is hidden")
    if entry_type and entry_type != "Application":
        raise ValueError(f"Not an Application entry (Type={entry_type})")
    if not exec_cmd:
        raise ValueError("Desktop file has no Exec=")
    return {
        "name": name,
        "command": exec_cmd,
        "icon_name": icon,
        "desktop_file": str(path),
        "desktop_id": path.name,
    }


def _icon_name_from_gicon(gicon: object | None) -> str:
    if gicon is None:
        return _DEFAULT_ICON
    try:
        from gi.repository import Gio

        if isinstance(gicon, Gio.ThemedIcon):
            names = gicon.get_names()
            if names:
                return str(names[0])
        if isinstance(gicon, Gio.FileIcon):
            f = gicon.get_file()
            if f is not None:
                path = f.get_path()
                if path:
                    return path
    except Exception:
        pass
    return _DEFAULT_ICON


def discover_system_apps() -> list[RoutedApp]:
    """Return user-visible installed applications (Gio / XDG desktop entries).

    Covers system packages, ~/.local, Flatpak exports, Snap desktop files —
    anything registered with GIO's app info database.
    """
    try:
        from gi.repository import Gio
    except ImportError:
        return _discover_system_apps_filesystem()

    out: list[RoutedApp] = []
    seen: set[str] = set()

    for info in Gio.AppInfo.get_all():
        try:
            if not info.should_show():
                continue
        except Exception:
            continue

        desktop_id = (info.get_id() or "").strip()
        key = desktop_id or (info.get_executable() or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)

        cmdline = (info.get_commandline() or "").strip()
        if not cmdline:
            exe = (info.get_executable() or "").strip()
            if not exe:
                continue
            cmdline = exe
        command = strip_desktop_field_codes(cmdline)
        if not command:
            continue

        name = (info.get_display_name() or "").strip()
        if not name:
            name = desktop_id.removesuffix(".desktop") if desktop_id else command.split()[0]

        desktop_file = ""
        try:
            # DesktopAppInfo exposes the on-disk .desktop path when available.
            get_fn = getattr(info, "get_filename", None)
            if callable(get_fn):
                desktop_file = (get_fn() or "") or ""
        except Exception:
            desktop_file = ""

        app_id = f"sys:{desktop_id}" if desktop_id else f"sys:{_slug(name)}-{_slug(command)[:24]}"
        out.append(
            RoutedApp(
                id=app_id,
                name=name,
                command=command,
                enabled=True,
                desktop_file=desktop_file,
                icon_name=_icon_name_from_gicon(info.get_icon()),
                mode="env",
                notes="",
                source=SOURCE_SYSTEM,
                desktop_id=desktop_id,
            )
        )

    out.sort(key=lambda a: a.name.casefold())
    return out


def _xdg_application_dirs() -> list[Path]:
    """XDG application search paths (user first, then system / Flatpak / Snap)."""
    import os

    dirs: list[Path] = []
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if not data_home:
        data_home = str(Path.home() / ".local" / "share")
    dirs.append(Path(data_home) / "applications")

    data_dirs = os.environ.get("XDG_DATA_DIRS", "").strip()
    if not data_dirs:
        data_dirs = "/usr/local/share:/usr/share"
    for part in data_dirs.split(":"):
        part = part.strip()
        if part:
            dirs.append(Path(part) / "applications")

    # Flatpak / Snap exports (also often on XDG_DATA_DIRS when session is set up)
    extra = [
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ]
    for p in extra:
        if p not in dirs:
            dirs.append(p)
    return dirs


def _discover_system_apps_filesystem() -> list[RoutedApp]:
    """Fallback scan of .desktop files when Gio is unavailable."""
    out: list[RoutedApp] = []
    seen: set[str] = set()
    for directory in _xdg_application_dirs():
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in entries:
            desktop_id = path.name
            if desktop_id in seen:
                continue
            try:
                meta = parse_desktop_file(path)
            except ValueError:
                continue
            seen.add(desktop_id)
            out.append(
                RoutedApp(
                    id=f"sys:{desktop_id}",
                    name=meta["name"],
                    command=meta["command"],
                    enabled=True,
                    desktop_file=meta["desktop_file"],
                    icon_name=meta.get("icon_name") or _DEFAULT_ICON,
                    mode="env",
                    notes="",
                    source=SOURCE_SYSTEM,
                    desktop_id=desktop_id,
                )
            )
    out.sort(key=lambda a: a.name.casefold())
    return out


class AppStore:
    """Custom apps (persisted) + system-discovered apps (live)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (user_data_dir() / "apps.json")
        self._apps: list[RoutedApp] = []  # custom only
        # Per system desktop_id: mode / enabled overrides
        self._overrides: dict[str, dict[str, object]] = {}
        self._system_cache: list[RoutedApp] | None = None
        self.load()

    def load(self) -> None:
        self._overrides = {}
        if not self._path.is_file():
            self._apps = []
            self.save()
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else raw.get("apps", [])
            known = {f.name for f in fields(RoutedApp)}
            self._apps = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                data = {k: v for k, v in item.items() if k in known}
                if "id" not in data or "name" not in data or "command" not in data:
                    continue
                data.setdefault("source", SOURCE_CUSTOM)
                # Never re-hydrate system rows from disk — rediscover live.
                if data.get("source") == SOURCE_SYSTEM:
                    continue
                data["source"] = SOURCE_CUSTOM
                try:
                    self._apps.append(RoutedApp(**data))
                except TypeError:
                    continue
            if isinstance(raw, dict):
                ov = raw.get("overrides") or {}
                if isinstance(ov, dict):
                    for k, v in ov.items():
                        if isinstance(k, str) and isinstance(v, dict):
                            self._overrides[k] = dict(v)
        except (OSError, json.JSONDecodeError, TypeError):
            self._apps = []
            self._overrides = {}
        self._system_cache = None

    def save(self) -> None:
        payload = {
            "apps": [asdict(a) for a in self._apps if a.is_custom],
            "overrides": self._overrides,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def invalidate_system_cache(self) -> None:
        self._system_cache = None

    def _system_apps(self) -> list[RoutedApp]:
        if self._system_cache is None:
            self._system_cache = discover_system_apps()
        # Apply overrides without mutating the cache template deeply
        out: list[RoutedApp] = []
        for app in self._system_cache:
            ov = self._overrides.get(app.desktop_id) or self._overrides.get(app.id)
            if not ov:
                out.append(app)
                continue
            # Shallow copy with overrides
            mode = ov.get("mode", app.mode)
            enabled = ov.get("enabled", app.enabled)
            out.append(
                RoutedApp(
                    id=app.id,
                    name=app.name,
                    command=app.command,
                    enabled=bool(enabled),
                    desktop_file=app.desktop_file,
                    icon_name=app.icon_name,
                    mode=str(mode) if str(mode) in ("env", "proxychains") else "env",
                    notes=app.notes,
                    source=SOURCE_SYSTEM,
                    desktop_id=app.desktop_id,
                )
            )
        return out

    def list(
        self,
        *,
        enabled_only: bool = False,
        include_system: bool = True,
        query: str = "",
    ) -> list[RoutedApp]:
        custom = list(self._apps)
        items: list[RoutedApp] = []

        if include_system:
            # Prefer custom entry when it shadows the same desktop file / id.
            custom_desk = {
                Path(a.desktop_file).name
                for a in custom
                if a.desktop_file
            }
            custom_desk |= {a.desktop_id for a in custom if a.desktop_id}
            custom_cmds = {a.command.strip() for a in custom}

            items.extend(custom)
            for app in self._system_apps():
                if app.desktop_id and app.desktop_id in custom_desk:
                    continue
                if app.desktop_file and Path(app.desktop_file).name in custom_desk:
                    continue
                if app.command.strip() in custom_cmds:
                    continue
                items.append(app)
        else:
            items = custom

        if enabled_only:
            items = [a for a in items if a.enabled]

        q = query.strip().casefold()
        if q:
            items = [
                a
                for a in items
                if q in a.name.casefold()
                or q in a.command.casefold()
                or q in (a.desktop_id or "").casefold()
            ]

        return sorted(items, key=lambda a: (0 if a.is_custom else 1, a.name.casefold()))

    def get(self, app_id: str) -> RoutedApp | None:
        for a in self._apps:
            if a.id == app_id:
                return a
        for a in self._system_apps():
            if a.id == app_id:
                return a
        return None

    def count_system(self) -> int:
        return len(self._system_apps())

    def create(
        self,
        *,
        name: str,
        command: str,
        desktop_file: str = "",
        icon_name: str = _DEFAULT_ICON,
        mode: str = "env",
        notes: str = "",
        enabled: bool = True,
        desktop_id: str = "",
    ) -> RoutedApp:
        name = name.strip()
        command = command.strip()
        if not name:
            raise ValueError("Name is required")
        if not command:
            raise ValueError("Command is required")
        if mode not in ("env", "proxychains"):
            mode = "env"
        app = RoutedApp(
            id=f"{_slug(name)}-{uuid.uuid4().hex[:6]}",
            name=name,
            command=command,
            enabled=enabled,
            desktop_file=desktop_file.strip(),
            icon_name=icon_name.strip() or _DEFAULT_ICON,
            mode=mode,
            notes=notes.strip(),
            source=SOURCE_CUSTOM,
            desktop_id=desktop_id.strip()
            or (Path(desktop_file).name if desktop_file else ""),
        )
        _ = app.argv()
        self._apps.append(app)
        self.save()
        return app

    def create_from_desktop(self, path: Path, *, mode: str = "env") -> RoutedApp:
        meta = parse_desktop_file(path)
        return self.create(
            name=meta["name"],
            command=meta["command"],
            desktop_file=meta["desktop_file"],
            icon_name=meta.get("icon_name") or _DEFAULT_ICON,
            mode=mode,
            desktop_id=meta.get("desktop_id") or path.name,
        )

    def update(self, app_id: str, **extra: object) -> RoutedApp | None:
        app = self.get(app_id)
        if app is None:
            return None

        # System apps: persist mode/enabled as overrides only
        if app.is_system:
            key = app.desktop_id or app.id
            ov = dict(self._overrides.get(key) or {})
            if "mode" in extra:
                mode = str(extra["mode"])
                ov["mode"] = mode if mode in ("env", "proxychains") else "env"
            if "enabled" in extra:
                ov["enabled"] = bool(extra["enabled"])
            self._overrides[key] = ov
            self.save()
            return self.get(app_id)

        known = {f.name for f in fields(RoutedApp)} - {"id", "source"}
        for key, value in extra.items():
            if key not in known:
                continue
            if key == "name":
                name = str(value).strip()
                if not name:
                    raise ValueError("Name is required")
                app.name = name
            elif key == "command":
                cmd = str(value).strip()
                if not cmd:
                    raise ValueError("Command is required")
                app.command = cmd
                _ = app.argv()
            elif key == "mode":
                mode = str(value)
                app.mode = mode if mode in ("env", "proxychains") else "env"
            elif key == "enabled":
                app.enabled = bool(value)
            else:
                setattr(app, key, value)
        self.save()
        return app

    def delete(self, app_id: str) -> bool:
        app = self.get(app_id)
        if app is None:
            return False
        if app.is_system:
            # Hiding a system app is "disable", not delete from disk.
            self.update(app_id, enabled=False)
            return True
        before = len(self._apps)
        self._apps = [a for a in self._apps if a.id != app_id]
        if len(self._apps) == before:
            return False
        self.save()
        return True
