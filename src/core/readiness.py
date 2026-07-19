"""Desktop-side readiness checks before the core is involved.

Structural checks: backends complete and bound.
Live preflight (connect): hop endpoints reachable, Mullvad connected, tools present.

Connect is allowed only when ok is True. Warnings are non-blocking product hints.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.backends import Backend, BackendStore
from core.profiles import Profile
from core.whonix import system_tor_socks


@dataclass
class Readiness:
    ok: bool
    issues: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok:
            return "Ready for core"
        return self.issues[0] if self.issues else "Not ready"

    @property
    def detail(self) -> str:
        if self.ok:
            if self.warnings:
                return self.warnings[0]
            return "All hops have complete backends."
        return " · ".join(self.issues)


# ── Structural helpers ─────────────────────────────────────────────


def is_mullvad_app_socks(backend: Backend) -> bool:
    """True if this backend is Mullvad's in-tunnel SOCKS (requires Mullvad app up)."""
    if backend.kind != "Proxy":
        return False
    host = (backend.proxy_host or "").strip()
    if host in ("10.64.0.1", "10.124.0.1"):
        return True
    name = (backend.name or "").lower()
    provider_hint = (getattr(backend, "vpn_provider", "") or "").lower()
    if host.startswith("10.64.") and ("mullvad" in name or "mullvad" in provider_hint):
        return True
    return False


def profile_uses_mullvad_app_socks(
    profile: Profile | None, backends: BackendStore
) -> bool:
    if profile is None:
        return False
    for hop in profile.hops:
        b = backends.get(hop.backend_id) if hop.backend_id else None
        if b is not None and is_mullvad_app_socks(b):
            return True
    return False


def routing_warnings(
    profile: Profile | None,
    backends: BackendStore,
    *,
    routing_mode: str = "system",
) -> list[str]:
    """Non-blocking product warnings about routing expectations."""
    warnings: list[str] = []
    mode = (routing_mode or "system").strip().lower()
    if mode not in ("system", "apps"):
        mode = "system"

    # Invalid nestings are hard errors in composition_issues / profile_readiness —
    # only emit soft hints for allowed underlay patterns.
    if not profile_uses_mullvad_app_socks(profile, backends):
        return warnings

    if profile is not None and profile_is_reality_then_mullvad(profile, backends):
        # Should already be blocked as a readiness issue; no soft rewrite story.
        return warnings

    if mode == "apps":
        warnings.append(
            "Mullvad app SOCKS: while Mullvad is Connected the whole system is "
            "already on Mullvad — Spectre apps-only does not undo that. "
            "Use Exclude apps (clearnet netns / mullvad-exclude) for carve-outs, "
            "or a WireGuard .conf hop without the Mullvad app."
        )
    else:
        warnings.append(
            "Mullvad app is full-tunnel when Connected; Spectre adds path/kill-switch "
            "on top. Connect Mullvad first, then Spectre."
        )

    # Host → Mullvad → local Tor/REALITY: allowed; Mullvad is OS underlay.
    if profile is not None and profile_is_mullvad_then_local_exit(profile, backends):
        if profile_is_mullvad_then_local_tor(profile, backends):
            warnings.append(
                "Exit is Tor. Mullvad is the OS tunnel underlay — not SOCKS-chained into Tor."
            )
        else:
            warnings.append(
                "Exit is REALITY. Mullvad is the OS tunnel underlay — not SOCKS-chained into REALITY."
            )

    return warnings


def profile_is_mullvad_then_local_tor(
    profile: Profile, backends: BackendStore
) -> bool:
    """True when path is Mullvad tunnel SOCKS followed later by local Tor."""
    return _profile_is_mullvad_then_kinds(profile, backends, {"Tor"})


def profile_is_mullvad_then_local_exit(
    profile: Profile, backends: BackendStore
) -> bool:
    """Mullvad app SOCKS first, then local Tor and/or REALITY (underlay pattern)."""
    return _profile_is_mullvad_then_kinds(profile, backends, {"Tor", "REALITY"})


def _profile_is_mullvad_then_kinds(
    profile: Profile, backends: BackendStore, kinds: set[str]
) -> bool:
    saw_mullvad = False
    for hop in profile.hops:
        b = backends.get(hop.backend_id) if hop.backend_id else None
        if b is None:
            continue
        if is_mullvad_app_socks(b):
            saw_mullvad = True
            continue
        if not saw_mullvad:
            continue
        if hop.kind == "Tor" and "Tor" in kinds:
            host = (b.tor_socks_host or "").strip().lower()
            if b.tor_use_system or host in ("", "127.0.0.1", "localhost", "::1"):
                return True
        if hop.kind == "REALITY" and "REALITY" in kinds:
            return True
    return False


def profile_is_reality_then_mullvad(
    profile: Profile, backends: BackendStore
) -> bool:
    """True when a REALITY hop is later followed by Mullvad app SOCKS."""
    saw_reality = False
    for hop in profile.hops:
        b = backends.get(hop.backend_id) if hop.backend_id else None
        if b is None:
            continue
        if hop.kind == "REALITY":
            saw_reality = True
            continue
        if saw_reality and is_mullvad_app_socks(b):
            return True
    return False


# Back-compat aliases (older imports)
_profile_is_mullvad_then_local_tor = profile_is_mullvad_then_local_tor
_profile_is_reality_then_mullvad = profile_is_reality_then_mullvad


# ── Live probes (fast, connect-time) ───────────────────────────────

_PROBE_TIMEOUT = 0.6  # seconds — keep UI snappy


def _tcp_open(host: str, port: int, *, timeout: float = _PROBE_TIMEOUT) -> bool:
    host = (host or "").strip()
    if not host or port <= 0:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def mullvad_cli_connected() -> bool | None:
    """True/False if `mullvad status` works; None if CLI missing/unusable."""
    exe = shutil.which("mullvad")
    if not exe:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [exe, "status"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Typical: "Connected" on first line when up; "Disconnected" when down.
    low = text.lower()
    if "disconnected" in low.split("\n", 1)[0]:
        return False
    if low.strip().startswith("connected") or "\nconnected" in low:
        return True
    # Fallback: look for explicit states
    first = (proc.stdout or "").strip().splitlines()
    if first:
        head = first[0].strip().lower()
        if head == "connected" or head.startswith("connected"):
            return True
        if head == "disconnected" or head.startswith("disconnected"):
            return False
    return None


def expand_vpn_config_path(raw: str) -> Path | None:
    cfg = (raw or "").strip()
    if not cfg or "[Interface]" in cfg:
        return None
    if cfg.startswith("~/"):
        cfg = str(Path.home() / cfg[2:])
    path = Path(cfg).expanduser()
    return path


def live_backend_issues(backend: Backend, *, hop_index: int) -> list[str]:
    """Runtime probes for one backend. Empty list = looks good."""
    label = backend.name or backend.kind
    prefix = f"Hop {hop_index} (“{label}”)"
    issues: list[str] = []

    if backend.kind == "Tor":
        host = backend.tor_socks_host
        port = backend.tor_socks_port
        if backend.tor_use_system or not host:
            host, sys_port = system_tor_socks()
            if port <= 0:
                port = sys_port
        if port <= 0:
            port = 9050
        if not host:
            host = "127.0.0.1"
        if not _tcp_open(host, port):
            issues.append(
                f"{prefix}: Tor SOCKS not reachable at {host}:{port} — "
                "start Tor (or Whonix Gateway) before Connect"
            )

    elif backend.kind == "Proxy":
        host = (backend.proxy_host or "").strip()
        port = int(backend.proxy_port or 0)
        if is_mullvad_app_socks(backend):
            # Prefer CLI truth when available
            mv = mullvad_cli_connected()
            if mv is False:
                issues.append(
                    f"{prefix}: Mullvad VPN is disconnected — "
                    "open Mullvad and Connect, then try Spectre again"
                )
            elif mv is None:
                # No CLI: require SOCKS to answer
                if not _tcp_open(host, port):
                    issues.append(
                        f"{prefix}: Mullvad tunnel SOCKS {host}:{port} is down — "
                        "Connect the Mullvad app first"
                    )
            else:
                # CLI says connected; still verify SOCKS (tunnel can lag)
                if not _tcp_open(host, port):
                    issues.append(
                        f"{prefix}: Mullvad reports Connected but SOCKS "
                        f"{host}:{port} is not accepting connections yet — wait a moment"
                    )
        else:
            if not _tcp_open(host, port):
                issues.append(
                    f"{prefix}: proxy {host}:{port} not reachable — "
                    "start the proxy service or fix host/port"
                )

    elif backend.kind == "VPN":
        proto = (backend.vpn_protocol or "WireGuard").strip()
        if proto == "WireGuard":
            path = expand_vpn_config_path(backend.vpn_config)
            if path is None:
                issues.append(
                    f"{prefix}: WireGuard needs a .conf file path (not inline config)"
                )
            elif not path.is_file():
                issues.append(
                    f"{prefix}: WireGuard config not found: {path}"
                )
            elif not os.access(path, os.R_OK):
                issues.append(f"{prefix}: cannot read WireGuard config: {path}")
            if not shutil.which("wg-quick"):
                issues.append(
                    f"{prefix}: wg-quick not found — install wireguard-tools"
                )
        # Other VPN protocols: no live probe yet

    elif backend.kind == "REALITY":
        if not shutil.which("xray"):
            # Also check common install location
            home_xray = Path.home() / ".local" / "bin" / "xray"
            if not home_xray.is_file():
                issues.append(
                    f"{prefix}: xray not found in PATH — install Xray-core "
                    "(spectre scripts/install-xray.sh) for REALITY hops"
                )
        server = (backend.reality_server or "").strip()
        port = int(backend.reality_port or 443)
        # Optional soft reachability — server may be firewalled to UDP/REALITY only;
        # TCP connect is a weak signal. Only warn if completely unresolvable?
        # Skip hard TCP fail for REALITY (protocol may not answer plain TCP).
        if server and not _host_resolves(server):
            issues.append(
                f"{prefix}: cannot resolve REALITY server “{server}”"
            )

    return issues


def _host_resolves(host: str) -> bool:
    host = (host or "").strip()
    if not host:
        return False
    if _looks_like_ip(host):
        return True
    try:
        socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def _looks_like_ip(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, host)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return True
    except OSError:
        return False


def live_policy_issues(*, routing_mode: str, kill_switch: bool) -> list[str]:
    """Policy-level preflight (privileges for system routing / kill switch)."""
    issues: list[str] = []
    mode = (routing_mode or "system").strip().lower()
    # System routing (and its kill switch) need passwordless spectre-nft.
    if mode == "system" and not _spectre_nft_ready():
        issues.append(
            "System routing needs the Spectre nft helper — run once: "
            "spectre setup-killswitch  (or set Routing mode to Selected apps only)"
        )
    _ = kill_switch  # reserved: apps-mode KS is ignored by core
    return issues


def _spectre_nft_ready() -> bool:
    """True if passwordless spectre-nft can run (or we are soft)."""
    if os.environ.get("SPECTRE_KILL_SWITCH_SOFT", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return True
    helpers = [
        "/usr/local/libexec/spectre/spectre-nft",
        "/usr/libexec/spectre/spectre-nft",
    ]
    which = shutil.which("spectre-nft")
    if which:
        helpers.insert(0, which)
    for h in helpers:
        if not Path(h).is_file():
            continue
        try:
            proc = subprocess.run(  # noqa: S603
                ["sudo", "-n", h, "version"],
                capture_output=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    # Direct nft without helper won't work for unprivileged user typically
    return False


# ── Ingress (Reach China) ─────────────────────────────────────────


def is_vpn_underlay(backend: Backend) -> bool:
    """True if this backend is a VPN underlay hop (WireGuard/VPN or Mullvad app)."""
    if backend.kind == "VPN":
        return True
    # Mullvad app full-tunnel + in-tunnel SOCKS counts as VPN underlay.
    return is_mullvad_app_socks(backend)


def is_ingress_cn_profile(profile: Profile | None) -> bool:
    if profile is None:
        return False
    from core.reverse_agent import is_any_ingress_intent

    return is_any_ingress_intent(
        profile.path_intent, profile.notes, profile.name
    )


def is_ingress_cn_reverse_profile(profile: Profile | None) -> bool:
    if profile is None:
        return False
    from core.reverse_agent import is_reverse_intent

    return is_reverse_intent(profile.path_intent, profile.notes)


def ingress_cn_issues(profile: Profile, backends: BackendStore) -> list[str]:
    """Hard rules for Reach China: VPN first, then landing or reverse map."""
    if is_ingress_cn_reverse_profile(profile):
        return _ingress_cn_reverse_issues(profile, backends)
    return _ingress_cn_inbound_issues(profile, backends)


def _ingress_cn_underlay_issues(
    profile: Profile, backends: BackendStore
) -> list[str]:
    issues: list[str] = []
    hops = profile.hops
    if len(hops) < 2:
        issues.append(
            "Reach China requires a VPN underlay hop before the China path "
            "(at least two hops: VPN → landing or reverse map)"
        )
        return issues

    first = backends.get(hops[0].backend_id) if hops[0].backend_id else None
    if first is None:
        issues.append("Reach China: first hop (VPN underlay) is missing a backend")
    elif not is_vpn_underlay(first):
        issues.append(
            "Reach China: first hop must be a VPN underlay "
            "(WireGuard/VPN backend or Mullvad app SOCKS) — "
            "do not dial a China endpoint from clearnet"
        )
    elif not first.enabled:
        issues.append(f"Reach China: VPN underlay “{first.name}” is disabled")
    elif not first.is_configured():
        issues.append(
            f"Reach China: VPN underlay “{first.name}” is incomplete "
            "(set WireGuard .conf, or use a configured Mullvad SOCKS backend)"
        )

    if len(hops) > 2:
        issues.append(
            "Reach China allows exactly two hops: VPN underlay → China path"
        )
    return issues


def _ingress_cn_inbound_issues(
    profile: Profile, backends: BackendStore
) -> list[str]:
    """Composition I: VPN → REALITY/Proxy to China host."""
    issues = _ingress_cn_underlay_issues(profile, backends)
    if any("at least two hops" in m for m in issues):
        return issues

    hops = profile.hops
    last = backends.get(hops[-1].backend_id) if hops[-1].backend_id else None
    if last is None:
        issues.append("Reach China: China-side hop is missing a backend")
    elif is_vpn_underlay(last):
        issues.append(
            "Reach China: last hop must be the China-side endpoint (REALITY or Proxy), "
            "not another VPN"
        )
    elif last.kind not in ("REALITY", "Proxy"):
        issues.append(
            "Reach China: last hop must be REALITY (recommended) or Proxy to the "
            "China-side host"
        )
    elif last.kind == "REALITY" and not (last.reality_sni or "").strip():
        issues.append(
            f"Reach China: REALITY backend “{last.name}” needs SNI "
            "(TLS-shaped cover toward the China host)"
        )
    elif last.kind == "Proxy" and is_mullvad_app_socks(last):
        issues.append(
            "Reach China: last hop cannot be Mullvad SOCKS — use REALITY/Proxy "
            "to the China host after the VPN underlay"
        )
    return issues


def _ingress_cn_reverse_issues(
    profile: Profile, backends: BackendStore
) -> list[str]:
    """Composition III: VPN → Proxy SOCKS map (agent already dialed out)."""
    issues = _ingress_cn_underlay_issues(profile, backends)
    if any("at least two hops" in m for m in issues):
        return issues

    hops = profile.hops
    last = backends.get(hops[-1].backend_id) if hops[-1].backend_id else None
    if last is None:
        issues.append(
            "Reach China reverse: SOCKS map hop is missing a backend "
            "(local or SSH-forwarded map from outside accept)"
        )
    elif last.kind != "Proxy":
        issues.append(
            "Reach China reverse: last hop must be a Proxy SOCKS map "
            "(from outside accept after agent dials out) — not REALITY to China"
        )
    elif is_mullvad_app_socks(last):
        issues.append(
            "Reach China reverse: last hop cannot be Mullvad SOCKS — "
            "use the reverse map SOCKS (usually 127.0.0.1)"
        )
    elif not last.enabled:
        issues.append(f"Reach China reverse: map backend “{last.name}” is disabled")
    elif not last.is_configured():
        issues.append(
            f"Reach China reverse: map backend “{last.name}” is incomplete "
            "(set SOCKS host/port where the reverse tunnel maps)"
        )
    return issues


def ingress_cn_warnings(profile: Profile, backends: BackendStore) -> list[str]:
    if is_ingress_cn_reverse_profile(profile):
        warns: list[str] = [
            "Inverse Snowflake: client must be dialing out (and map up) before SOCKS works. "
            "Python path is cleartext+token until REALITY wrap ships.",
            "Inverse Snowflake: success is outside vantage only — "
            "not proof for users inside CN.",
            "Inverse Snowflake: foothold is peer/lab/field — Spectre exports the client; "
            "it does not provision M.",
        ]
    else:
        warns = [
            "Reach China: success is from this outside vantage only — "
            "not proof for users inside CN.",
            "Reach China: China-side host is operator-owned; Spectre does not provision it.",
        ]
    if profile.hops:
        first = backends.get(profile.hops[0].backend_id) if profile.hops[0].backend_id else None
        if first is not None and is_mullvad_app_socks(first):
            warns.append(
                "VPN underlay is Mullvad app: connect Mullvad first (or allow auto-connect), "
                "then Spectre dials the China path inside that underlay."
            )
    return warns


# ── Public API ─────────────────────────────────────────────────────


def profile_readiness(
    profile: Profile | None,
    backends: BackendStore,
    *,
    routing_mode: str = "system",
    kill_switch: bool = True,
    live: bool = False,
) -> Readiness:
    """Return readiness. Use live=True before Connect (endpoint probes)."""
    issues: list[str] = []
    if profile is None:
        return Readiness(False, ["No profile selected"])
    if not profile.hops:
        return Readiness(False, ["Profile has no hops"])

    for i, hop in enumerate(profile.hops, start=1):
        if not hop.backend_id:
            issues.append(f"Hop {i} ({hop.kind}) has no backend")
            continue
        backend = backends.get(hop.backend_id)
        if backend is None:
            issues.append(f"Hop {i} ({hop.kind}) points to a missing backend")
            continue
        if backend.kind != hop.kind:
            issues.append(
                f"Hop {i}: backend “{backend.name}” is {backend.kind}, expected {hop.kind}"
            )
            continue
        if not backend.enabled:
            issues.append(f"Hop {i}: backend “{backend.name}” is disabled")
            continue
        if not backend.is_configured():
            issues.append(f"Hop {i}: backend “{backend.name}” is incomplete")
            continue
        if live:
            issues.extend(live_backend_issues(backend, hop_index=i))

    # Hard composition rules (invalid nestings block ready/Connect).
    if not any("has no backend" in x or "missing backend" in x or "incomplete" in x for x in issues):
        from core.path_compose import composition_issues

        for ci in composition_issues(profile, backends):
            issues.append(ci.message)

    # Reach China: VPN underlay required before any China-side hop.
    if is_ingress_cn_profile(profile):
        for msg in ingress_cn_issues(profile, backends):
            if msg not in issues:
                issues.append(msg)

    if live:
        issues.extend(
            live_policy_issues(
                routing_mode=routing_mode,
                kill_switch=kill_switch,
            )
        )

    warns = routing_warnings(profile, backends, routing_mode=routing_mode)
    if is_ingress_cn_profile(profile):
        for w in ingress_cn_warnings(profile, backends):
            if w not in warns:
                warns.append(w)
    return Readiness(ok=not issues, issues=issues, warnings=warns)


def profile_status_tag(profile: Profile | None, backends: BackendStore) -> str:
    """Short tag for list rows: ready / incomplete / unbound / invalid."""
    ready = profile_readiness(profile, backends, live=False)
    if ready.ok:
        return "ready"
    if profile is None or not profile.hops:
        return "empty"
    unbound = any(not h.backend_id for h in profile.hops)
    if unbound:
        return "unbound"
    from core.path_compose import composition_issues

    if composition_issues(profile, backends):
        return "invalid"
    return "incomplete"
