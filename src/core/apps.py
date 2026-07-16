"""Routed applications — which apps use the Spectre path."""

from __future__ import annotations

import json
import re
import shlex
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app_config import user_data_dir


@dataclass
class RoutedApp:
    """An application that can be launched through the active Spectre path."""

    id: str
    name: str
    command: str  # executable + args (shell-safe via shlex)
    enabled: bool = True
    desktop_file: str = ""  # optional .desktop path
    icon_name: str = "application-x-executable-symbolic"
    mode: str = "env"  # env | proxychains
    notes: str = ""

    def argv(self) -> list[str]:
        try:
            parts = shlex.split(self.command, posix=True)
        except ValueError:
            parts = self.command.split()
        if not parts:
            raise ValueError("Command is empty")
        return parts


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "app"


def parse_desktop_file(path: Path) -> dict[str, str]:
    """Minimal .desktop parser for Name / Exec / Icon."""
    name = path.stem
    exec_cmd = ""
    icon = "application-x-executable-symbolic"
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
            # Strip field codes %u %f etc.
            exec_cmd = re.sub(r"\s+%[a-zA-Z@]", "", val).strip()
            exec_cmd = re.sub(r"%[a-zA-Z@]", "", exec_cmd).strip()
        elif key == "Icon" and val:
            icon = val
    if not exec_cmd:
        raise ValueError("Desktop file has no Exec=")
    return {"name": name, "command": exec_cmd, "icon_name": icon, "desktop_file": str(path)}


class AppStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (user_data_dir() / "apps.json")
        self._apps: list[RoutedApp] = []
        self.load()

    def load(self) -> None:
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
                try:
                    self._apps.append(RoutedApp(**data))
                except TypeError:
                    continue
        except (OSError, json.JSONDecodeError, TypeError):
            self._apps = []

    def save(self) -> None:
        payload = {"apps": [asdict(a) for a in self._apps]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def list(self, *, enabled_only: bool = False) -> list[RoutedApp]:
        items = list(self._apps)
        if enabled_only:
            items = [a for a in items if a.enabled]
        return sorted(items, key=lambda a: a.name.lower())

    def get(self, app_id: str) -> RoutedApp | None:
        for a in self._apps:
            if a.id == app_id:
                return a
        return None

    def create(
        self,
        *,
        name: str,
        command: str,
        desktop_file: str = "",
        icon_name: str = "application-x-executable-symbolic",
        mode: str = "env",
        notes: str = "",
        enabled: bool = True,
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
            icon_name=icon_name.strip() or "application-x-executable-symbolic",
            mode=mode,
            notes=notes.strip(),
        )
        # validate argv
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
            icon_name=meta.get("icon_name") or "application-x-executable-symbolic",
            mode=mode,
        )

    def update(self, app_id: str, **extra: object) -> RoutedApp | None:
        app = self.get(app_id)
        if app is None:
            return None
        known = {f.name for f in fields(RoutedApp)} - {"id"}
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
        before = len(self._apps)
        self._apps = [a for a in self._apps if a.id != app_id]
        if len(self._apps) == before:
            return False
        self.save()
        return True
