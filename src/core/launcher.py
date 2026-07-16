"""Launch applications excluded from Spectre / tunnel (clearnet).

Contract (exclude-list split tunnel):
  - Default product model: entire system uses Spectre; apps opened here run
    *outside* the path (clearnet).
  - Preferred isolation: clearnet network namespace via ``clearnet-run``
    (veth ``cn-host``; marks Spectre KS/sysroute already honor).
  - Fallback: ``mullvad-exclude`` (setuid mark-based exclusion; Spectre
    skips the same marks).
  - Each launch forces a **separate process/instance** so a normal
    (system-routed) copy can keep running while the excluded copy is clearnet.
  - Never runs ``clearnet-netns teardown`` (would kill every PID in the
    netns — including agents launched there).
  - Does not auto-``setup`` the netns from the GUI if missing (avoids
    destructive recreate); tells the user to set up once as root.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from core.apps import RoutedApp
from core.client import CoreClient, CoreState

# Match spectred default when status has no local_proxy yet.
DEFAULT_SOCKS_PORT = 10808

# Default clearnet netns name used by clearnet-run / clearnet-netns.
DEFAULT_CLEARNET_NS = "clearnet"

# Session / display vars to pass into clearnet-run --env (env -i inside).
_SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "PULSE_SERVER",
    "PIPEWIRE_RUNTIME_DIR",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "GTK_IM_MODULE",
    "QT_IM_MODULE",
    "XMODIFIERS",
)

_PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "FTP_PROXY",
    "ftp_proxy",
    "SOCKS_PROXY",
    "socks_proxy",
    "SOCKS5_PROXY",
    "socks5_proxy",
    "SPECTRE_PROXY",
    "SPECTRE_SOCKS",
)


@dataclass
class LaunchResult:
    ok: bool
    message: str
    pid: int | None = None
    method: str = ""  # clearnet-run | mullvad-exclude | socks | ""


@dataclass
class ExcludeTooling:
    """Read-only snapshot of exclude helpers (no side effects)."""

    clearnet_run: str | None = None
    mullvad_exclude: str | None = None
    netns_ready: bool = False
    netns_name: str = DEFAULT_CLEARNET_NS
    sudo_nopasswd: bool | None = None  # None = not probed

    @property
    def can_clearnet_run(self) -> bool:
        return bool(self.clearnet_run and self.netns_ready and self.sudo_nopasswd)

    @property
    def can_mullvad_exclude(self) -> bool:
        return bool(self.mullvad_exclude)

    @property
    def any_ready(self) -> bool:
        return self.can_clearnet_run or self.can_mullvad_exclude

    def summary(self) -> str:
        bits: list[str] = []
        if self.can_clearnet_run:
            bits.append(f"clearnet-run ({self.netns_name} netns)")
        elif self.clearnet_run and not self.netns_ready:
            bits.append(f"clearnet-run present · netns “{self.netns_name}” not ready")
        elif self.clearnet_run and self.sudo_nopasswd is False:
            bits.append("clearnet-run present · needs passwordless sudo")
        if self.can_mullvad_exclude:
            bits.append("mullvad-exclude")
        if not bits:
            return "no exclude helper"
        return " · ".join(bits)


@dataclass
class _Tracked:
    pid: int
    name: str


@dataclass
class LaunchSession:
    """Processes started via Apps (for status counts only — not killed)."""

    _items: list[_Tracked] = field(default_factory=list)

    def track(self, pid: int, name: str) -> None:
        if pid <= 0:
            return
        self._items.append(_Tracked(pid=pid, name=name or f"pid {pid}"))

    def active_count(self) -> int:
        self._reap()
        return len(self._items)

    def names(self) -> list[str]:
        self._reap()
        return [t.name for t in self._items]

    def _reap(self) -> None:
        self._items = [t for t in self._items if _pid_alive(t.pid)]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _first_executable(*candidates: str | None) -> str | None:
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def clearnet_ns_name() -> str:
    return (os.environ.get("CLEARNET_NS") or DEFAULT_CLEARNET_NS).strip() or DEFAULT_CLEARNET_NS


def netns_exists(name: str | None = None) -> bool:
    """Read-only: is the clearnet netns registered? Never creates or tears down."""
    ns = (name or clearnet_ns_name()).strip() or DEFAULT_CLEARNET_NS
    for base in (Path("/run/netns"), Path("/var/run/netns")):
        if (base / ns).exists():
            return True
    return False


def find_clearnet_run() -> str | None:
    env = (os.environ.get("SPECTRE_CLEARNET_RUN") or "").strip()
    which = shutil.which("clearnet-run")
    return _first_executable(
        env or None,
        "/usr/local/libexec/spectre/clearnet-run",
        "/usr/libexec/spectre/clearnet-run",
        "/usr/local/bin/clearnet-run",
        which,
    )


def find_mullvad_exclude() -> str | None:
    env = (os.environ.get("SPECTRE_MULLVAD_EXCLUDE") or "").strip()
    which = shutil.which("mullvad-exclude")
    return _first_executable(env or None, "/usr/bin/mullvad-exclude", which)


def probe_sudo_nopasswd(*, timeout_sec: float = 1.5) -> bool:
    """True if generic ``sudo -n true`` works (broad NOPASSWD).

    Prefer :func:`probe_clearnet_sudo` for Exclude — most installs only grant
    passwordless sudo for ``clearnet-run`` / ``clearnet-netns``, not ``true``.
    """
    sudo = shutil.which("sudo")
    if not sudo:
        return False
    try:
        r = subprocess.run(  # noqa: S603
            [sudo, "-n", "true"],
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def probe_clearnet_sudo(
    clearnet_run: str | None,
    *,
    timeout_sec: float = 3.0,
) -> bool:
    """True if ``sudo -n <clearnet-run> -- /bin/true`` succeeds.

    Matches real Exclude launches and the spectre-clearnet sudoers drop-in
    (NOPASSWD only for clearnet helpers, not arbitrary commands).
    """
    sudo = shutil.which("sudo")
    if not sudo or not clearnet_run:
        return False
    try:
        r = subprocess.run(  # noqa: S603
            [sudo, "-n", clearnet_run, "--", "/bin/true"],
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def probe_exclude_tooling(*, check_sudo: bool = True) -> ExcludeTooling:
    """Discover exclude helpers. Never runs netns setup/teardown."""
    t = ExcludeTooling(
        clearnet_run=find_clearnet_run(),
        mullvad_exclude=find_mullvad_exclude(),
        netns_ready=netns_exists(),
        netns_name=clearnet_ns_name(),
    )
    if check_sudo and t.clearnet_run and t.netns_ready:
        t.sudo_nopasswd = probe_clearnet_sudo(t.clearnet_run)
    elif t.clearnet_run:
        t.sudo_nopasswd = False if check_sudo else None
    return t


def _session_env_pairs() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key in _SESSION_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            out.append((key, val))
    return out


def _strip_proxy_env(env: dict[str, str]) -> dict[str, str]:
    for k in _PROXY_ENV_KEYS:
        env.pop(k, None)
    return env


# ── Separate instance (do not attach to already-open single-instance apps) ──

_FIREFOX_EXES = frozenset(
    {
        "firefox",
        "firefox-esr",
        "firefox-bin",
        "firefox-nightly",
        "librewolf",
        "waterfox",
        "icecat",
    }
)
_THUNDERBIRD_EXES = frozenset(
    {"thunderbird", "thunderbird-esr", "thunderbird-bin", "betterbird"}
)
_CHROMIUM_EXES = frozenset(
    {
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "google-chrome-beta",
        "google-chrome-unstable",
        "brave-browser",
        "brave",
        "microsoft-edge",
        "microsoft-edge-stable",
        "vivaldi",
        "vivaldi-stable",
        "opera",
        "ungoogled-chromium",
    }
)
_ELECTRON_EXES = frozenset(
    {
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
        "typora",
        "gitkraken",
    }
)


def _exe_basename(argv: list[str]) -> str:
    if not argv:
        return ""
    return Path(argv[0]).name.lower()


def _argv_has_prefix(argv: list[str], *prefixes: str) -> bool:
    for a in argv[1:]:
        for p in prefixes:
            if a == p or a.startswith(p + "=") or a.startswith(p):
                # careful: startswith(p) for --user-data-dir=/path
                if a == p or a.startswith(p + "="):
                    return True
                if p.startswith("-") and a.startswith(p):
                    return True
    return False


def _instance_dir(app: RoutedApp, *, tag: str = "exclude") -> Path:
    from app_config import user_data_dir

    key = (app.id or app.desktop_id or _slug_instance(app.name) or "app").strip()
    key = re.sub(r"[^\w.\-]+", "-", key)[:80] or "app"
    path = user_data_dir() / "instances" / tag / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug_instance(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "app"


def _mozilla_config_roots(exe: str) -> list[Path]:
    """Candidate dirs that contain profiles.ini for Firefox-family browsers."""
    home = Path.home()
    roots: list[Path] = []
    if exe in _THUNDERBIRD_EXES:
        roots.extend(
            [
                home / ".thunderbird",
                home / ".icedove",
                home / "snap" / "thunderbird" / "common" / ".thunderbird",
            ]
        )
    elif exe == "librewolf":
        roots.extend(
            [
                home / ".librewolf",
                home / ".var" / "app" / "io.gitlab.librewolf-community" / "config" / "librewolf",
            ]
        )
    elif exe == "waterfox":
        roots.append(home / ".waterfox")
    else:
        # firefox, firefox-esr, nightlies, …
        roots.extend(
            [
                home / ".mozilla" / "firefox",
                home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
                home
                / ".var"
                / "app"
                / "org.mozilla.firefox"
                / ".mozilla"
                / "firefox",
            ]
        )
    return roots


def resolve_mozilla_default_profile(exe: str) -> Path | None:
    """Return the user's default Firefox/Thunderbird profile directory, if found.

    Reads profiles.ini (Install* Default= or Profile* Default=1). Used so the
    clearnet instance keeps bookmarks, logins, and extensions.
    """
    for root in _mozilla_config_roots(exe):
        ini = root / "profiles.ini"
        if not ini.is_file():
            continue
        try:
            text = ini.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Parse loosely: track sections and keys.
        section: str | None = None
        profiles: list[dict[str, str]] = []
        install_default_path: str | None = None
        current: dict[str, str] = {}

        def flush() -> None:
            nonlocal current
            if current.get("_section", "").startswith("Profile"):
                profiles.append(current)
            current = {}

        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(";") or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                flush()
                section = line[1:-1]
                current = {"_section": section}
                continue
            if "=" not in line or section is None:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            current[key] = val
            # Modern Firefox: [Install…] Default=relative/path
            if section.startswith("Install") and key == "Default" and val:
                install_default_path = val

        flush()

        def resolve_path(rel_or_abs: str, *, is_relative: bool) -> Path:
            p = Path(rel_or_abs)
            if not is_relative or p.is_absolute():
                return p
            return root / p

        # Prefer Install* Default= (path relative to root)
        if install_default_path:
            cand = resolve_path(install_default_path, is_relative=True)
            if cand.is_dir():
                return cand

        # ProfileN with Default=1
        for prof in profiles:
            if prof.get("Default") in ("1", "True", "true"):
                path = prof.get("Path") or ""
                if not path:
                    continue
                rel = prof.get("IsRelative", "1") in ("1", "True", "true")
                cand = resolve_path(path, is_relative=rel)
                if cand.is_dir():
                    return cand

        # First profile with a Path
        for prof in profiles:
            path = prof.get("Path") or ""
            if not path:
                continue
            rel = prof.get("IsRelative", "1") in ("1", "True", "true")
            cand = resolve_path(path, is_relative=rel)
            if cand.is_dir():
                return cand

    return None


# Skip locks + bulky caches when cloning a Mozilla profile into Spectre's copy.
_MOZILLA_COPY_IGNORE_NAMES = frozenset(
    {
        "parent.lock",
        "lock",
        ".parentlock",
        "Cache",
        "cache2",
        "startupCache",
        "shader-cache",
        "OfflineCache",
        "thumbnails",
        "safebrowsing",
        "jumpListCache",
        "datareporting",
        "crashes",
        "minidumps",
        "gmp",
        "gmp-gmpopenh264",
        "gmp-widevinecdm",
    }
)
_SPECTRE_PROFILE_META = ".spectre-profile-source"

# DoH for exclude Firefox: TRR-only via Cloudflare IP URI so bootstrap never
# hits ISP/LAN resolvers (and never Mullvad 10.64.0.1 on the host).
_EXCLUDE_DOH_URI = "https://1.1.1.1/dns-query"
_EXCLUDE_DOH_MARKER_BEGIN = "// BEGIN spectre-desktop exclude DoH"
_EXCLUDE_DOH_MARKER_END = "// END spectre-desktop exclude DoH"
_EXCLUDE_DOH_USER_JS_BLOCK = f"""
{_EXCLUDE_DOH_MARKER_BEGIN}
// Clearnet exclude: force DNS over HTTPS (not ISP / not Mullvad tunnel DNS).
// mode 3 = TRR only; URI uses Cloudflare anycast IP so no system DNS bootstrap.
user_pref("network.trr.mode", 3);
user_pref("network.trr.uri", "{_EXCLUDE_DOH_URI}");
user_pref("network.trr.custom_uri", "{_EXCLUDE_DOH_URI}");
user_pref("network.trr.default_provider_uri", "{_EXCLUDE_DOH_URI}");
user_pref("network.trr.bootstrapAddress", "1.1.1.1");
user_pref("network.trr.confirmationNS", "skip");
user_pref("network.trr.excluded-domains", "localhost,local,lan,.local,.lan");
user_pref("doh-rollout.enabled", false);
user_pref("doh-rollout.disable-heuristics", true);
user_pref("doh-rollout.uri", "");
{_EXCLUDE_DOH_MARKER_END}
"""


def apply_exclude_mozilla_doh(profile: Path) -> None:
    """Force DoH on a Spectre exclude Mozilla profile (user.js + prefs.js).

    Safe to call every launch. Appends/replaces a marked block at the end of
    ``user.js`` so it wins over arkenfox/user copies that set ``trr.mode = 5``.
    """
    if not profile.is_dir():
        return
    block = _EXCLUDE_DOH_USER_JS_BLOCK.strip() + "\n"
    user_js = profile / "user.js"
    try:
        existing = ""
        if user_js.is_file():
            existing = user_js.read_text(encoding="utf-8", errors="replace")
        if _EXCLUDE_DOH_MARKER_BEGIN in existing:
            start = existing.index(_EXCLUDE_DOH_MARKER_BEGIN)
            end = existing.find(_EXCLUDE_DOH_MARKER_END, start)
            if end >= 0:
                end += len(_EXCLUDE_DOH_MARKER_END)
                # drop trailing newlines after old block
                while end < len(existing) and existing[end] in "\r\n":
                    end += 1
                existing = (existing[:start].rstrip() + "\n\n" + existing[end:].lstrip())
            else:
                existing = existing[:start].rstrip() + "\n"
        text = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
        user_js.write_text(text, encoding="utf-8")
    except OSError:
        pass

    # Also pin prefs.js so about:config matches before the first user.js apply
    # (and if something rewrites user.js mid-session next start still works).
    prefs_js = profile / "prefs.js"
    try:
        lines: list[str] = []
        if prefs_js.is_file():
            lines = prefs_js.read_text(encoding="utf-8", errors="replace").splitlines()
        drop_keys = (
            "network.trr.mode",
            "network.trr.uri",
            "network.trr.custom_uri",
            "network.trr.default_provider_uri",
            "network.trr.bootstrapAddress",
            "network.trr.confirmationNS",
            "network.trr.excluded-domains",
            "doh-rollout.enabled",
            "doh-rollout.disable-heuristics",
            "doh-rollout.uri",
        )

        def _is_drop(line: str) -> bool:
            for k in drop_keys:
                if f'user_pref("{k}"' in line or f"user_pref('{k}'" in line:
                    return True
            return False

        kept = [ln for ln in lines if not _is_drop(ln)]
        additions = [
            f'user_pref("network.trr.mode", 3);',
            f'user_pref("network.trr.uri", "{_EXCLUDE_DOH_URI}");',
            f'user_pref("network.trr.custom_uri", "{_EXCLUDE_DOH_URI}");',
            f'user_pref("network.trr.default_provider_uri", "{_EXCLUDE_DOH_URI}");',
            f'user_pref("network.trr.bootstrapAddress", "1.1.1.1");',
            f'user_pref("network.trr.confirmationNS", "skip");',
            f'user_pref("network.trr.excluded-domains", "localhost,local,lan,.local,.lan");',
            f'user_pref("doh-rollout.enabled", false);',
            f'user_pref("doh-rollout.disable-heuristics", true);',
            f'user_pref("doh-rollout.uri", "");',
        ]
        body = "\n".join(kept).rstrip() + "\n" + "\n".join(additions) + "\n"
        prefs_js.write_text(body, encoding="utf-8")
    except OSError:
        pass


def _mozilla_copy_ignore(directory: str, names: list[str]) -> set[str]:
    _ = directory
    skip = set()
    for n in names:
        if n in _MOZILLA_COPY_IGNORE_NAMES:
            skip.add(n)
        elif n.startswith("Cache") or n.endswith(".lock"):
            skip.add(n)
    return skip


def ensure_spectre_mozilla_profile(
    app: RoutedApp,
    exe: str,
    *,
    tag: str = "exclude",
) -> tuple[Path, str]:
    """Ensure a Spectre-owned Mozilla profile exists (copy of the user's default).

    - Lives only under Spectre Desktop user data (per user, not shipped defaults).
    - Never changes Firefox's default profile / profiles.ini.
    - First launch copies once; later launches reuse the Spectre copy so the
      clearnet instance can run beside a normal Firefox on the real profile.
    """
    inst = _instance_dir(app, tag=tag)
    dest = inst / "mozilla-profile"
    meta = dest / _SPECTRE_PROFILE_META
    source = resolve_mozilla_default_profile(exe)

    # Already cloned — reuse (user's ongoing clearnet bookmarks stay here).
    if dest.is_dir() and meta.is_file():
        try:
            src_recorded = meta.read_text(encoding="utf-8").strip().splitlines()[0]
        except OSError:
            src_recorded = ""
        note = "Spectre profile copy"
        if src_recorded:
            note = f"Spectre profile (from {Path(src_recorded).name})"
        if tag == "exclude":
            apply_exclude_mozilla_doh(dest)
            note = f"{note} · DoH"
        return dest, note

    dest.mkdir(parents=True, exist_ok=True)

    if source is None or not source.is_dir():
        # No source — empty dir; Firefox creates a fresh profile there.
        try:
            meta.write_text("# no source profile found\n", encoding="utf-8")
        except OSError:
            pass
        if tag == "exclude":
            apply_exclude_mozilla_doh(dest)
        return dest, "Spectre profile (empty — no source found)"

    # Fresh clone from the user's default profile into Spectre-only storage.
    # Do not modify *source* or profiles.ini.
    try:
        # If dest has partial junk from a failed copy, clear non-meta contents.
        for child in dest.iterdir():
            if child.name == _SPECTRE_PROFILE_META:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass

        # copytree into dest: copy contents of source
        for item in source.iterdir():
            if item.name in _MOZILLA_COPY_IGNORE_NAMES:
                continue
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    target,
                    symlinks=True,
                    ignore=_mozilla_copy_ignore,
                    dirs_exist_ok=True,
                )
            else:
                try:
                    shutil.copy2(item, target, follow_symlinks=False)
                except OSError:
                    try:
                        shutil.copy2(item, target)
                    except OSError:
                        pass

        meta.write_text(
            f"{source}\n"
            f"# Spectre Desktop clearnet profile copy\n"
            f"# Not registered as Firefox's default — only used with --profile\n",
            encoding="utf-8",
        )
    except OSError as exc:
        if tag == "exclude":
            apply_exclude_mozilla_doh(dest)
        return dest, f"Spectre profile (copy incomplete: {exc})"

    if tag == "exclude":
        apply_exclude_mozilla_doh(dest)
        return dest, f"Spectre profile (copied from {source.name}) · DoH"
    return dest, f"Spectre profile (copied from {source.name})"


def argv_for_separate_instance(
    argv: list[str],
    app: RoutedApp,
    *,
    tag: str = "exclude",
) -> tuple[list[str], str, bool]:
    """Rewrite *argv* so a second copy of the app starts instead of focusing
    an already-running (system-routed) instance.

    Returns (new_argv, note, private_xdg).
    """
    if not argv:
        return argv, "empty command", False

    out = list(argv)
    note = "new process"
    private_xdg = False
    inst = _instance_dir(app, tag=tag)
    # WM class is applied later by prepare_exclude_window_identity.

    # flatpak run <app-id> [args…] — still a new host process; instance
    # isolation inside the sandbox is limited without app-specific flags.
    if _exe_basename(out) == "flatpak" and len(out) >= 3 and out[1] == "run":
        return out, "flatpak process", False

    exe = _exe_basename(out)

    if exe in _FIREFOX_EXES or exe in _THUNDERBIRD_EXES:
        # Spectre-owned profile = one-time copy of the user's default profile.
        # Both can run at once (different profile paths). Menu Firefox stays on
        # the real default; we never rewrite profiles.ini.
        flags: list[str] = []
        if not _argv_has_prefix(out, "--no-remote", "-no-remote"):
            flags.append("--no-remote")
        if exe in _FIREFOX_EXES and not _argv_has_prefix(
            out, "--new-instance", "-new-instance"
        ):
            flags.append("--new-instance")
        if not _argv_has_prefix(out, "-profile", "--profile", "-P"):
            profile, note = ensure_spectre_mozilla_profile(app, exe, tag=tag)
            flags.extend(["--profile", str(profile)])
        else:
            note = "requested profile"
        out = [out[0], *flags, *out[1:]]
        return out, note, False

    if exe in _CHROMIUM_EXES or exe in _ELECTRON_EXES:
        udd = inst / "user-data"
        udd.mkdir(parents=True, exist_ok=True)
        flags = []
        if not _argv_has_prefix(out, "--user-data-dir"):
            flags.append(f"--user-data-dir={udd}")
        if exe in _CHROMIUM_EXES and not _argv_has_prefix(out, "--new-window"):
            flags.append("--new-window")
        out = [out[0], *flags, *out[1:]]
        # Electron often locks on XDG paths too
        private_xdg = exe in _ELECTRON_EXES
        return out, "new instance + private data dir", private_xdg

    # Generic multi-instance apps: new process is enough. Single-instance GTK
    # apps may still activate the existing window — no universal flag.
    return out, "new process (separate from menu launch)", False


def env_for_separate_instance(
    env: dict[str, str],
    app: RoutedApp,
    *,
    tag: str = "exclude",
    private_xdg: bool = False,
) -> dict[str, str]:
    """Env tweaks that mark this as a Spectre-launched instance.

    When *private_xdg* is True (browsers / Electron), use a dedicated
    XDG config/cache tree so single-instance locks do not collide with the
    normal system-routed copy. Generic apps keep the user's normal XDG paths
    so settings/themes still work.
    """
    env = dict(env)
    inst = _instance_dir(app, tag=tag)
    env["SPECTRE_LAUNCH_INSTANCE"] = tag
    env["SPECTRE_INSTANCE_DIR"] = str(inst)
    if private_xdg:
        xdg_config = inst / "config"
        xdg_cache = inst / "cache"
        xdg_data = inst / "share"
        xdg_config.mkdir(parents=True, exist_ok=True)
        xdg_cache.mkdir(parents=True, exist_ok=True)
        xdg_data.mkdir(parents=True, exist_ok=True)
        env["XDG_CONFIG_HOME"] = str(xdg_config)
        env["XDG_CACHE_HOME"] = str(xdg_cache)
        env["XDG_DATA_HOME"] = str(xdg_data)
    return env


def _build_clearnet_run_argv(
    clearnet_run: str,
    app_argv: list[str],
    *,
    extra_env: Mapping[str, str] | None = None,
) -> list[str]:
    """sudo -n clearnet-run [--env K=V ...] -- cmd...

    Never passes teardown. Never invokes clearnet-netns directly.
    """
    sudo = shutil.which("sudo") or "sudo"
    cmd: list[str] = [sudo, "-n", "--", clearnet_run]
    for key, val in _session_env_pairs():
        cmd.extend(["--env", f"{key}={val}"])
    if extra_env:
        for key, val in extra_env.items():
            if val:
                cmd.extend(["--env", f"{key}={val}"])
    cmd.append("--")
    cmd.extend(app_argv)
    return cmd


def _build_mullvad_exclude_argv(mullvad_exclude: str, app_argv: list[str]) -> list[str]:
    return [mullvad_exclude, *app_argv]


def launch_app(
    app: RoutedApp,
    core: CoreClient,
    *,
    session: LaunchSession | None = None,
    bind_address: str = "127.0.0.1",
    tooling: ExcludeTooling | None = None,
) -> LaunchResult:
    """Start a **new instance** of *app* excluded from Spectre / tunnel (clearnet).

    Does not focus or reuse an already-open system-routed copy when the app
    supports a separate-instance flag (browsers, many Electron apps).

    ``bind_address`` is accepted for API compatibility; exclude launch does
    not use SOCKS. SOCKS include is available via :func:`launch_app_socks`.
    """
    _ = core, bind_address
    if not app.enabled:
        return LaunchResult(False, f"“{app.name}” is disabled")

    try:
        app_argv = app.argv()
    except ValueError as exc:
        return LaunchResult(False, str(exc))

    app_argv, instance_note, private_xdg = argv_for_separate_instance(
        app_argv, app, tag="exclude"
    )
    # Unique WM class + .desktop (normal app icon, no badge overlay)
    try:
        from core.exclude_badge import prepare_exclude_window_identity

        app_argv, id_note = prepare_exclude_window_identity(app, app_argv)
        instance_note = f"{instance_note} · {id_note}"
    except Exception:
        pass
    env = _launch_env_for_exclude(app, private_xdg=private_xdg)

    tools = tooling if tooling is not None else probe_exclude_tooling(check_sudo=True)

    # 1) Preferred: clearnet netns
    if tools.clearnet_run and tools.netns_ready and tools.sudo_nopasswd:
        # Pass instance XDG + SPECTRE_* into the netns via clearnet-run --env
        cmd = _build_clearnet_run_argv(
            tools.clearnet_run,
            app_argv,
            extra_env={
                k: env[k]
                for k in (
                    "SPECTRE_EXCLUDED",
                    "SPECTRE_LAUNCH_INSTANCE",
                    "SPECTRE_INSTANCE_DIR",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_DATA_HOME",
                )
                if k in env
            },
        )
        return _popen_exclude(
            cmd,
            app_name=app.name,
            method="clearnet-run",
            session=session,
            env=env,
            detail=f"netns {tools.netns_name} · {instance_note}",
        )

    # 2) Fallback: mark-based mullvad-exclude (Spectre honors same marks)
    if tools.mullvad_exclude:
        cmd = _build_mullvad_exclude_argv(tools.mullvad_exclude, app_argv)
        return _popen_exclude(
            cmd,
            app_name=app.name,
            method="mullvad-exclude",
            session=session,
            env=env,
            detail=f"mark-based clearnet · {instance_note}",
        )

    # Explain why we failed (no silent SOCKS fallback — that would re-include).
    return LaunchResult(False, _exclude_unavailable_message(tools))


def _launch_env_for_exclude(
    app: RoutedApp | None = None,
    *,
    private_xdg: bool = False,
) -> dict[str, str]:
    env = _strip_proxy_env(dict(os.environ))
    env["SPECTRE_EXCLUDED"] = "1"
    if app is not None:
        env = env_for_separate_instance(
            env, app, tag="exclude", private_xdg=private_xdg
        )
    return env


def _exclude_unavailable_message(tools: ExcludeTooling) -> str:
    parts = [
        "Cannot exclude app: no working clearnet helper.",
    ]
    if not tools.clearnet_run and not tools.mullvad_exclude:
        parts.append(
            "Install clearnet-run (/usr/local/bin) or mullvad-exclude, "
            "then ensure the clearnet netns exists."
        )
    else:
        if tools.clearnet_run and not tools.netns_ready:
            parts.append(
                f"Netns “{tools.netns_name}” is missing — once: "
                f"spectre setup-clearnet  (do not teardown while agents use it)."
            )
        if tools.clearnet_run and tools.netns_ready and tools.sudo_nopasswd is False:
            parts.append(
                "Passwordless sudo missing for clearnet-run — run: spectre setup-clearnet"
            )
        if not tools.mullvad_exclude:
            parts.append("Optional fallback: install Mullvad CLI (mullvad-exclude).")
    return " ".join(parts)


def _popen_exclude(
    cmd: list[str],
    *,
    app_name: str,
    method: str,
    session: LaunchSession | None,
    env: dict[str, str],
    detail: str,
) -> LaunchResult:
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return LaunchResult(False, f"Command not found: {cmd[0]}", method=method)
    except OSError as exc:
        return LaunchResult(False, f"Launch failed: {exc}", method=method)

    # Brief poll: catch immediate sudo / helper failures without blocking
    # the GUI or killing anything. Do not wait() for long-lived apps.
    ret = None
    for _ in range(8):
        ret = proc.poll()
        if ret is not None:
            break
        time.sleep(0.05)

    if ret is not None and ret != 0:
        err = ""
        try:
            if proc.stderr is not None:
                err = (proc.stderr.read() or b"").decode("utf-8", errors="replace").strip()
        except OSError:
            err = ""
        msg = f"Exclude launch failed ({method}, exit {ret})"
        if err:
            msg = f"{msg}: {err[:200]}"
        return LaunchResult(False, msg, method=method)

    # Drop stderr handle so a chatty child cannot fill a pipe
    try:
        if proc.stderr is not None:
            proc.stderr.close()
    except OSError:
        pass

    if session is not None and proc.pid:
        session.track(proc.pid, app_name)

    msg = f"New instance “{app_name}” excluded · {method}"
    if detail:
        msg += f" · {detail}"
    return LaunchResult(True, msg, pid=proc.pid, method=method)


# ── Optional SOCKS include (CLI / advanced; not the Apps page default) ─────


def resolve_socks_hostport(
    core: CoreClient,
    *,
    bind_address: str = "127.0.0.1",
) -> tuple[str, bool]:
    """Return (host:port, path_up)."""
    st = core.status()
    proxy = (st.local_proxy or "").strip()
    path_up = st.state == CoreState.CONNECTED and bool(proxy)
    if proxy:
        if "://" in proxy:
            proxy = proxy.split("://", 1)[1]
        return proxy, path_up
    host = (bind_address or "127.0.0.1").strip() or "127.0.0.1"
    return f"{host}:{DEFAULT_SOCKS_PORT}", False


def socks_urls(local_proxy: str) -> dict[str, str]:
    """Environment variables that route cooperative apps via Spectre SOCKS."""
    hostport = local_proxy.strip()
    if "://" in hostport:
        url = hostport
    else:
        url = f"socks5h://{hostport}"
    no_proxy = "localhost,127.0.0.1,::1,.local"
    return {
        "ALL_PROXY": url,
        "all_proxy": url,
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "http_proxy": url,
        "https_proxy": url,
        "FTP_PROXY": url,
        "ftp_proxy": url,
        "SOCKS_PROXY": url,
        "socks_proxy": url,
        "SOCKS5_PROXY": url,
        "socks5_proxy": url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
        "SPECTRE_PROXY": url,
        "SPECTRE_SOCKS": hostport.split("://")[-1] if "://" in hostport else hostport,
    }


def build_launch_env(
    local_proxy: str,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env.update(socks_urls(local_proxy))
    return env


def launch_app_socks(
    app: RoutedApp,
    core: CoreClient,
    *,
    session: LaunchSession | None = None,
    bind_address: str = "127.0.0.1",
) -> LaunchResult:
    """Start *app* via Spectre SOCKS (include). Not used by the Apps exclude page."""
    if not app.enabled:
        return LaunchResult(False, f"“{app.name}” is disabled", method="socks")

    proxy, path_up = resolve_socks_hostport(core, bind_address=bind_address)

    try:
        argv = app.argv()
    except ValueError as exc:
        return LaunchResult(False, str(exc), method="socks")

    mode = app.mode if app.mode in ("env", "proxychains") else "env"
    env = build_launch_env(proxy)

    if mode == "proxychains":
        pc = shutil.which("proxychains4") or shutil.which("proxychains")
        if not pc:
            return LaunchResult(
                False,
                "proxychains not found — install proxychains-ng or use env mode",
                method="socks",
            )
        conf = _proxychains_conf(proxy)
        argv = [pc, "-q", "-f", conf, *argv]

    try:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return LaunchResult(False, f"Command not found: {argv[0]}", method="socks")
    except OSError as exc:
        return LaunchResult(False, f"Launch failed: {exc}", method="socks")

    if session is not None and proc.pid:
        session.track(proc.pid, app.name)

    if path_up:
        msg = f"On path “{app.name}” · SOCKS {proxy}"
    else:
        msg = (
            f"On path “{app.name}” · SOCKS {proxy} "
            f"(path down — network waits until you Connect)"
        )
    return LaunchResult(True, msg, pid=proc.pid, method="socks")


def launch_command(
    argv: list[str],
    core: CoreClient,
    *,
    mode: str = "env",
    session: LaunchSession | None = None,
    bind_address: str = "127.0.0.1",
) -> LaunchResult:
    """Launch an arbitrary command through Spectre SOCKS (CLI helper)."""
    proxy, _path_up = resolve_socks_hostport(core, bind_address=bind_address)
    env = build_launch_env(proxy)
    cmd = list(argv)
    if mode == "proxychains":
        pc = shutil.which("proxychains4") or shutil.which("proxychains")
        if not pc:
            return LaunchResult(False, "proxychains not found", method="socks")
        conf = _proxychains_conf(proxy)
        cmd = [pc, "-q", "-f", conf, *cmd]
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return LaunchResult(False, str(exc), method="socks")
    name = argv[0] if argv else "command"
    if session is not None and proc.pid:
        session.track(proc.pid, name)
    return LaunchResult(True, f"pid {proc.pid}", pid=proc.pid, method="socks")


def _proxychains_conf(local_proxy: str) -> str:
    from app_config import user_data_dir

    hostport = local_proxy
    if "://" in hostport:
        hostport = hostport.split("://", 1)[1]
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
    else:
        host, port = hostport, str(DEFAULT_SOCKS_PORT)
    path = user_data_dir() / "proxychains-spectre.conf"
    path.write_text(
        "strict_chain\n"
        "proxy_dns\n"
        "remote_dns_subnet 224\n"
        "tcp_read_time_out 15000\n"
        "tcp_connect_time_out 8000\n"
        "[ProxyList]\n"
        f"socks5 {host} {port}\n",
        encoding="utf-8",
    )
    return str(path)
