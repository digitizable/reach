"""Detect and install Drift / Mirage / Sounding / Laminar lab companions.

These are separate digitizable repos (mostly Python scripts). Reach treats a
companion as installed when:

  * a launcher is on PATH, or
  * the repo is under ``$XDG_DATA_HOME/reach/lab/<name>/``, or
  * a sibling checkout exists next to the Reach project root (dev).

Install clones (or updates) into the lab dir and drops a small launcher in
``~/.local/bin`` so the tool is on PATH when that directory is configured.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app_config import project_root, user_data_dir


@dataclass(frozen=True)
class Companion:
    id: str  # drift | mirage | sounding | laminar
    title: str
    role: str
    binary: str  # expected PATH name
    github: str  # owner/repo
    # Relative path of the primary script inside the clone (for the launcher)
    primary_script: str
    # Extra binaries that count as "present" (PATH names)
    also_bins: tuple[str, ...] = ()


COMPANIONS: tuple[Companion, ...] = (
    Companion(
        id="drift",
        title="Drift",
        role="Inverse Snowflake",
        binary="drift",
        github="digitizable/drift",
        primary_script="scripts/spectre-inverse-snowflake.py",
        also_bins=("spectre-inverse-snowflake",),
    ),
    Companion(
        id="mirage",
        title="Mirage",
        role="Probe-resistant cover",
        binary="mirage",
        github="digitizable/mirage",
        primary_script="scripts/gfw-prr-gen.py",
        also_bins=("gfw-prr-gen",),
    ),
    Companion(
        id="sounding",
        title="Sounding",
        role="Measurement lab",
        binary="sounding",
        github="digitizable/sounding",
        primary_script="scripts/gfw-prr-probe.py",
        also_bins=("gfw-prr-probe",),
    ),
    Companion(
        id="laminar",
        title="Laminar",
        role="Composition fingerprint measure (O1)",
        binary="laminar",
        github="digitizable/laminar",
        primary_script="scripts/measure_rtt.py",
        also_bins=("laminar-rtt", "laminar-score"),
    ),
)


@dataclass
class CompanionState:
    companion: Companion
    installed: bool
    location: str = ""  # human-readable where found
    source: str = ""  # path | lab | workspace | ""
    version_hint: str = ""


def lab_root() -> Path:
    return Path(user_data_dir()) / "lab"


def lab_dir(companion_id: str) -> Path:
    return lab_root() / companion_id


def _local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def _which_any(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _workspace_clone(companion_id: str) -> Path | None:
    """Sibling checkout next to Reach (dev layout)."""
    try:
        parent = project_root().parent
    except Exception:
        return None
    cand = parent / companion_id
    if (cand / "README.md").is_file() or (cand / "scripts").is_dir():
        return cand
    return None


def probe_companion(c: Companion) -> CompanionState:
    # 1) PATH launchers
    hit = _which_any(c.binary, *c.also_bins)
    if hit:
        return CompanionState(
            companion=c,
            installed=True,
            location=hit,
            source="path",
        )

    # 2) Reach lab install dir
    lab = lab_dir(c.id)
    script = lab / c.primary_script
    if lab.is_dir() and (script.is_file() or (lab / "README.md").is_file()):
        return CompanionState(
            companion=c,
            installed=True,
            location=str(lab),
            source="lab",
        )

    # 3) Sibling workspace clone
    ws = _workspace_clone(c.id)
    if ws is not None:
        return CompanionState(
            companion=c,
            installed=True,
            location=str(ws),
            source="workspace",
        )

    return CompanionState(companion=c, installed=False)


def probe_all() -> list[CompanionState]:
    return [probe_companion(c) for c in COMPANIONS]


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: float = 180.0) -> tuple[int, str]:
    git = shutil.which("git")
    if not git:
        return 1, "git not found — install git to fetch lab companions"
    try:
        r = subprocess.run(  # noqa: S603
            [git, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return 1, "git timed out"
    except OSError as exc:
        return 1, str(exc)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def _write_launcher(c: Companion, repo: Path) -> Path | None:
    """Write ~/.local/bin/<binary> → primary script (or help if missing)."""
    bin_dir = _local_bin()
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    target = bin_dir / c.binary
    script = repo / c.primary_script
    # Prefer absolute path to the script
    if script.is_file():
        body = f"""#!/usr/bin/env bash
# Reach lab launcher for {c.title} ({c.github})
set -euo pipefail
exec python3 "{script}" "$@"
"""
    else:
        body = f"""#!/usr/bin/env bash
# Reach lab launcher for {c.title} — repo at {repo}
set -euo pipefail
echo "{c.title}: installed at {repo}" >&2
echo "Primary script missing ({c.primary_script}). See README." >&2
ls -la "{repo}/scripts" 2>/dev/null || true
exit 1
"""
    try:
        target.write_text(body, encoding="utf-8")
        mode = target.stat().st_mode
        target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        return None
    return target


def install_companion(companion_id: str) -> tuple[bool, str]:
    """Clone or update one companion into the lab dir; write PATH launcher."""
    c = next((x for x in COMPANIONS if x.id == companion_id), None)
    if c is None:
        return False, f"Unknown companion: {companion_id}"

    git = shutil.which("git")
    if not git:
        return False, "git is required to install lab companions"

    dest = lab_dir(c.id)
    url = f"https://github.com/{c.github}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if (dest / ".git").is_dir():
        code, out = _run_git(["-C", str(dest), "pull", "--ff-only"], timeout=180.0)
        action = "Updated"
        if code != 0:
            # fall back to fetch + reset soft message
            return False, f"git pull failed for {c.title}:\n{out or 'unknown error'}"
    elif dest.exists() and any(dest.iterdir()):
        return False, f"{dest} exists but is not a git clone — move it aside and retry"
    else:
        if dest.exists():
            try:
                dest.rmdir()
            except OSError:
                pass
        code, out = _run_git(
            ["clone", "--depth", "1", url, str(dest)],
            timeout=240.0,
        )
        action = "Installed"
        if code != 0:
            return False, f"git clone failed for {c.title}:\n{out or 'unknown error'}"

    launcher = _write_launcher(c, dest)
    bits = [f"{action} {c.title} → {dest}"]
    if launcher is not None:
        bits.append(f"Launcher: {launcher}")
        local_bin = str(_local_bin())
        path_env = os.environ.get("PATH", "")
        if local_bin not in path_env.split(os.pathsep):
            bits.append(
                f"Note: add {local_bin} to PATH to run `{c.binary}` from a terminal."
            )
    else:
        bits.append(f"Could not write launcher under {_local_bin()} (check permissions).")
    bits.append(f"Repo: https://github.com/{c.github}")
    return True, "\n".join(bits)


def companion_by_id(companion_id: str) -> Companion | None:
    return next((c for c in COMPANIONS if c.id == companion_id), None)
