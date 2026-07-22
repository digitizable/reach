"""Install / list / remove Reach plugins from GitHub or local paths."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_config import user_data_dir
from core.plugin_manifest import (
    MANIFEST_NAME,
    PluginManifest,
    find_manifest,
    load_manifest_file,
    parse_manifest,
)

# Official marketplace catalog (bundled packs + installable repos)
OFFICIAL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "com.digitizable.fingerprint",
        "name": "Path fingerprint",
        "tagline": "Built-in · ΔRTT / path latency on live SOCKS",
        "official": True,
        "builtin": True,
        "category": "lab",
        "repo": "",
    },
    {
        "id": "com.digitizable.lab",
        "name": "Lab companions",
        "tagline": "Built-in · Drift · Mirage · Sounding · Laminar",
        "official": True,
        "builtin": True,
        "category": "lab",
        "repo": "",
    },
    {
        "id": "com.digitizable.hogwarts",
        "name": "Hogwarts",
        "tagline": "C2 keep for Reach · Hogwarts · channel, agents, plane",
        "official": True,
        "builtin": False,
        "category": "operator",
        "repo": "digitizable/reach-plugin-hogwarts",
        "branch": "main",
    },
    {
        "id": "com.digitizable.hello",
        "name": "Hello",
        "tagline": "Plugin template · examples/reach-plugin-hello",
        "official": True,
        "builtin": False,
        "category": "tool",
        "repo": "",
        "local_example": "examples/reach-plugin-hello",
    },
)

_GITHUB_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def plugins_root() -> Path:
    p = Path(user_data_dir()) / "plugins"
    p.mkdir(parents=True, exist_ok=True)
    return p


def plugin_dir(plugin_id: str) -> Path:
    # Safe directory name from id
    safe = plugin_id.replace(".", "__")
    return plugins_root() / safe


@dataclass
class InstalledPlugin:
    manifest: PluginManifest
    path: Path

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def has_nav(self) -> bool:
        return self.manifest.nav is not None


def list_installed() -> list[InstalledPlugin]:
    out: list[InstalledPlugin] = []
    root = plugins_root()
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        man = find_manifest(child)
        if man is None:
            continue
        try:
            m = load_manifest_file(man, source=f"local:{child.name}")
            out.append(InstalledPlugin(manifest=m, path=child))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return out


def get_installed(plugin_id: str) -> InstalledPlugin | None:
    for p in list_installed():
        if p.id == plugin_id:
            return p
    return None


def parse_github_spec(spec: str) -> tuple[str, str] | None:
    s = (spec or "").strip()
    if not s:
        return None
    m = _GITHUB_RE.match(s)
    if m:
        return m.group(1), m.group(2).removesuffix(".git")
    if "/" in s and " " not in s and s.count("/") == 1:
        a, b = s.split("/", 1)
        if a and b:
            return a, b.removesuffix(".git")
    return None


def install_from_github(
    spec: str,
    *,
    branch: str = "",
    official: bool = False,
) -> tuple[bool, str, PluginManifest | None]:
    """Clone a GitHub repo that contains reach-plugin.json."""
    parsed = parse_github_spec(spec)
    if not parsed:
        return False, "Use owner/repo or a github.com URL", None
    owner, repo = parsed
    if not shutil.which("git"):
        return False, "git not found on PATH", None

    url = f"https://github.com/{owner}/{repo}.git"
    # Clone into a temp dir name first, then rename to id
    staging = plugins_root() / f".staging-{owner}-{repo}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([url, str(staging)])
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"git clone failed: {exc}", None
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "clone failed").strip()
        shutil.rmtree(staging, ignore_errors=True)
        return False, err[:400], None

    man_path = find_manifest(staging)
    if man_path is None:
        shutil.rmtree(staging, ignore_errors=True)
        return (
            False,
            f"No {MANIFEST_NAME} in repo root (see docs/PLUGIN_SPEC.md)",
            None,
        )
    try:
        manifest = load_manifest_file(
            man_path,
            source=f"github:{owner}/{repo}",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"Invalid manifest: {exc}", None

    # Force official flag only when catalog says so
    if official and not manifest.official:
        data = json.loads(man_path.read_text(encoding="utf-8"))
        data["official"] = True
        man_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        manifest = load_manifest_file(man_path, source=f"github:{owner}/{repo}")

    dest = plugin_dir(manifest.id)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    # If manifest is in a subfolder, move that subfolder
    plugin_root = man_path.parent
    if plugin_root.resolve() == staging.resolve():
        staging.rename(dest)
    else:
        shutil.move(str(plugin_root), str(dest))
        shutil.rmtree(staging, ignore_errors=True)

    # Drop .git to shrink install (optional; keep for update via reinstall)
    git_dir = dest / ".git"
    if git_dir.is_dir():
        shutil.rmtree(git_dir, ignore_errors=True)

    final = load_manifest_file(
        dest / MANIFEST_NAME
        if (dest / MANIFEST_NAME).is_file()
        else find_manifest(dest) or dest / MANIFEST_NAME,
        source=f"github:{owner}/{repo}",
    )
    return True, f"Installed {final.name} ({final.id})", final


def uninstall(plugin_id: str) -> tuple[bool, str]:
    inst = get_installed(plugin_id)
    if inst is None:
        return False, "Plugin not installed"
    try:
        shutil.rmtree(inst.path)
    except OSError as exc:
        return False, str(exc)
    return True, f"Removed {inst.manifest.name}"


def catalog_rows(*, disabled_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Official catalog merged with install + enabled state.

    *disabled_ids* — installed package ids that are turned off (still on disk).
    """
    disabled = {str(x).strip() for x in (disabled_ids or []) if str(x).strip()}
    installed = {p.id: p for p in list_installed()}
    rows: list[dict[str, Any]] = []
    for item in OFFICIAL_CATALOG:
        row = dict(item)
        pid = str(item["id"])
        # Map builtin ids to legacy plugins_enabled keys
        on_disk = pid in installed
        row["installed"] = on_disk or bool(item.get("builtin"))
        row["on_disk"] = on_disk
        # Built-ins are toggled in Settings; filesystem packages use disabled list.
        if item.get("builtin"):
            row["active"] = True  # refined by marketplace when services known
            row["toggleable"] = False
        elif on_disk:
            row["active"] = pid not in disabled
            row["toggleable"] = True
        else:
            row["active"] = False
            row["toggleable"] = False
        if on_disk:
            row["version"] = installed[pid].manifest.version
            row["path"] = str(installed[pid].path)
            row["icon_path"] = _plugin_icon_path(installed[pid])
        rows.append(row)
    # Community installs not in catalog
    known = {str(i["id"]) for i in OFFICIAL_CATALOG}
    for p in list_installed():
        if p.id not in known:
            rows.append(
                {
                    "id": p.id,
                    "name": p.manifest.name,
                    "tagline": p.manifest.description or p.manifest.version,
                    "official": p.manifest.official,
                    "builtin": False,
                    "category": p.manifest.category,
                    "repo": p.manifest.source.removeprefix("github:"),
                    "installed": True,
                    "on_disk": True,
                    "active": p.id not in disabled,
                    "toggleable": True,
                    "version": p.manifest.version,
                    "path": str(p.path),
                    "icon_path": _plugin_icon_path(p),
                    "community": True,
                }
            )
    return rows


def _plugin_icon_path(inst: InstalledPlugin) -> str:
    """Absolute path to full-color icon_file (marketplace)."""
    nav = inst.manifest.nav
    if nav is None or not nav.icon_file:
        return ""
    root = Path(inst.manifest.install_path or inst.path)
    cand = root / nav.icon_file
    return str(cand) if cand.is_file() else ""


def _plugin_symbolic_path(inst: InstalledPlugin) -> str:
    """Absolute path to rail-themed mark (icon_symbolic, else icon_file)."""
    nav = inst.manifest.nav
    if nav is None:
        return ""
    root = Path(inst.manifest.install_path or inst.path)
    if nav.icon_symbolic:
        cand = root / nav.icon_symbolic
        if cand.is_file():
            return str(cand)
    if nav.icon_file:
        cand = root / nav.icon_file
        if cand.is_file():
            return str(cand)
    return ""
