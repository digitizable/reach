"""Reach plugin manifest (``reach-plugin.json``) — schema parse & validate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
# Reverse-DNS style id: com.example.myplugin
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MANIFEST_NAME = "reach-plugin.json"


@dataclass(frozen=True)
class PluginNav:
    title: str
    icon: str = "application-x-addon-symbolic"
    tooltip: str = ""
    # Full-color mark (marketplace / detail). Relative to plugin root.
    icon_file: str = ""
    # Optional monochrome/transparent mark for the left rail (themed).
    # If empty, rail falls back to recoloring icon_file (may look blocky if filled).
    icon_symbolic: str = ""


@dataclass(frozen=True)
class PluginEntry:
    """How Reach loads the plugin UI."""

    kind: str = "python"  # only python in v1
    # Module path relative to plugin root (no .py): "ui" or "src.page"
    module: str = "ui"
    # Callable name: create_page(ctx) -> Gtk.Widget
    create: str = "create_page"


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    homepage: str = ""
    license: str = ""
    category: str = "tool"  # lab | operator | tool
    official: bool = False
    entry: PluginEntry = field(default_factory=PluginEntry)
    nav: PluginNav | None = None
    permissions: tuple[str, ...] = ()
    requires_reach: str = ">=0.5.0"
    schema: int = SCHEMA_VERSION
    # Source after install
    source: str = ""  # github:owner/repo or path
    install_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema": self.schema,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "homepage": self.homepage,
            "license": self.license,
            "category": self.category,
            "official": self.official,
            "entry": {
                "kind": self.entry.kind,
                "module": self.entry.module,
                "create": self.entry.create,
            },
            "permissions": list(self.permissions),
            "requires_reach": self.requires_reach,
        }
        if self.nav is not None:
            d["nav"] = {
                "title": self.nav.title,
                "icon": self.nav.icon,
                "tooltip": self.nav.tooltip,
                "icon_file": self.nav.icon_file,
                "icon_symbolic": self.nav.icon_symbolic,
            }
        return d


def _as_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v).strip()


def parse_manifest(data: dict[str, Any], *, source: str = "") -> PluginManifest:
    """Parse and validate a manifest dict. Raises ValueError on bad input."""
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    schema = int(data.get("schema") or SCHEMA_VERSION)
    if schema != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema {schema} (need {SCHEMA_VERSION})")

    pid = _as_str(data.get("id"))
    if not pid or not _ID_RE.match(pid):
        raise ValueError(
            "id must be reverse-DNS (e.g. com.example.myplugin)"
        )
    name = _as_str(data.get("name"))
    version = _as_str(data.get("version"))
    if not name or not version:
        raise ValueError("name and version are required")

    entry_raw = data.get("entry") or {}
    if not isinstance(entry_raw, dict):
        entry_raw = {}
    entry = PluginEntry(
        kind=_as_str(entry_raw.get("kind"), "python") or "python",
        module=_as_str(entry_raw.get("module"), "ui") or "ui",
        create=_as_str(entry_raw.get("create"), "create_page") or "create_page",
    )
    if entry.kind != "python":
        raise ValueError("only entry.kind=python is supported in Reach 0.5+")

    nav: PluginNav | None = None
    nav_raw = data.get("nav")
    if isinstance(nav_raw, dict) and _as_str(nav_raw.get("title")):
        nav = PluginNav(
            title=_as_str(nav_raw.get("title")),
            icon=_as_str(nav_raw.get("icon"), "application-x-addon-symbolic")
            or "application-x-addon-symbolic",
            tooltip=_as_str(nav_raw.get("tooltip")) or _as_str(nav_raw.get("title")),
            icon_file=_as_str(nav_raw.get("icon_file")),
            icon_symbolic=_as_str(nav_raw.get("icon_symbolic")),
        )

    perms = data.get("permissions") or []
    if not isinstance(perms, list):
        perms = []
    permissions = tuple(str(p).strip() for p in perms if str(p).strip())

    cat = _as_str(data.get("category"), "tool") or "tool"
    if cat not in ("lab", "operator", "tool"):
        cat = "tool"

    return PluginManifest(
        id=pid,
        name=name,
        version=version,
        description=_as_str(data.get("description")),
        author=_as_str(data.get("author")),
        homepage=_as_str(data.get("homepage")),
        license=_as_str(data.get("license")),
        category=cat,
        official=bool(data.get("official")),
        entry=entry,
        nav=nav,
        permissions=permissions,
        requires_reach=_as_str(data.get("requires_reach"), ">=0.5.0") or ">=0.5.0",
        schema=schema,
        source=source,
    )


def load_manifest_file(path: Path, *, source: str = "") -> PluginManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest root must be object")
    m = parse_manifest(raw, source=source or str(path))
    return PluginManifest(
        **{**m.__dict__, "install_path": str(path.parent.resolve())}
    )


def find_manifest(root: Path) -> Path | None:
    """Locate reach-plugin.json in repo root or one level down."""
    direct = root / MANIFEST_NAME
    if direct.is_file():
        return direct
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            cand = child / MANIFEST_NAME
            if cand.is_file():
                return cand
    return None
