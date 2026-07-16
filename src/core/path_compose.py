"""Path hop composition rules — what may follow what.

Spectre does not allow free SOCKS nesting. Profiles must only list
combinations the dialer can actually implement.

Allowed patterns (high level)
-----------------------------
- Single hop: REALITY | Tor | Proxy | VPN
- Mullvad app SOCKS (10.64.0.1) **first only**, then optional:
  - local Tor or REALITY → treated as OS underlay + dial the local hop
  - remote Proxy → true SOCKS chain through Mullvad
- VPN (WireGuard) **first only**, then optional Tor / REALITY / Proxy
- Remote Proxy chains (remote → remote)
- REALITY **last only** (local xray client; cannot nest further locals)
- Tor **last only** (local/system Tor SOCKS)

Blocked examples
----------------
- REALITY → Mullvad SOCKS (10.64 only exists on this host)
- REALITY → Tor (VPS cannot dial this host's Tor)
- Tor → REALITY / Mullvad
- Remote Proxy → local Tor / REALITY / Mullvad
- Mullvad SOCKS anywhere except first hop
"""

from __future__ import annotations

from dataclasses import dataclass

from core.backends import Backend, BackendStore
from core.profiles import Hop, Profile
from core.readiness import is_mullvad_app_socks


@dataclass(frozen=True)
class ComposeIssue:
    """One composition problem (blocks Connect / ready)."""

    message: str
    hop_index: int = 0  # 1-based; 0 = whole path


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("", "localhost", "127.0.0.1", "::1") or h.startswith("127.")


def _is_tunnel_socks(backend: Backend) -> bool:
    return is_mullvad_app_socks(backend)


def _is_local_tor(backend: Backend) -> bool:
    if backend.kind != "Tor":
        return False
    if backend.tor_use_system:
        return True
    return _is_loopback_host(backend.tor_socks_host or "")


def _is_remote_proxy(backend: Backend) -> bool:
    if backend.kind != "Proxy":
        return False
    if _is_tunnel_socks(backend):
        return False
    return not _is_loopback_host(backend.proxy_host or "")


def _is_local_proxy(backend: Backend) -> bool:
    if backend.kind != "Proxy":
        return False
    if _is_tunnel_socks(backend):
        return False
    return _is_loopback_host(backend.proxy_host or "")


def _classify(backend: Backend) -> str:
    """Return a coarse class for composition checks."""
    if backend.kind == "VPN":
        return "vpn"
    if backend.kind == "REALITY":
        return "reality"
    if backend.kind == "Tor":
        return "tor_local" if _is_local_tor(backend) else "tor_remote"
    if backend.kind == "Proxy":
        if _is_tunnel_socks(backend):
            return "mullvad_socks"
        if _is_remote_proxy(backend):
            return "proxy_remote"
        if _is_local_proxy(backend):
            return "proxy_local"
        return "proxy_remote"
    return "unknown"


def _name(backend: Backend | None, hop: Hop) -> str:
    if backend is not None and backend.name:
        return backend.name
    return hop.kind


def validate_hop_pair(
    prev: Backend,
    nxt: Backend,
    *,
    prev_hop: Hop,
    next_hop: Hop,
    next_index: int,
) -> ComposeIssue | None:
    """Return an issue if nxt cannot follow prev; else None."""
    a = _classify(prev)
    b = _classify(nxt)
    pa = _name(prev, prev_hop)
    pb = _name(nxt, next_hop)
    i = next_index

    # --- Mullvad app SOCKS may only be first ---
    if b == "mullvad_socks":
        return ComposeIssue(
            f"Hop {i}: “{pb}” (Mullvad app SOCKS) can only be the first hop — "
            f"10.64.0.1 exists only on this machine while Mullvad is Connected, "
            f"so it cannot follow “{pa}”. "
            f"Put Mullvad first (underlay) or use REALITY/Tor alone.",
            hop_index=i,
        )

    # --- REALITY is exit-only (local xray → remote VPS) ---
    if a == "reality":
        return ComposeIssue(
            f"Hop {i}: nothing can follow REALITY (“{pa}”). "
            f"The next hop would be dialed from the REALITY server, not this host — "
            f"local Tor/Mullvad/loopback are unreachable there. "
            f"Use REALITY alone, or Mullvad/VPN first then REALITY last.",
            hop_index=i,
        )

    # --- Local Tor is exit-only ---
    if a == "tor_local":
        return ComposeIssue(
            f"Hop {i}: nothing can follow local Tor (“{pa}”). "
            f"A Tor exit cannot dial this host’s next hop. "
            f"Use Tor last (optionally after Mullvad/VPN underlay).",
            hop_index=i,
        )

    # --- After Mullvad SOCKS ---
    if a == "mullvad_socks":
        if b in ("tor_local", "reality", "proxy_remote", "proxy_local", "tor_remote"):
            return None  # underlay + local, or SOCKS chain to remote
        if b == "vpn":
            return ComposeIssue(
                f"Hop {i}: VPN cannot follow Mullvad SOCKS (“{pa}”). "
                f"Use VPN first or Mullvad alone.",
                hop_index=i,
            )
        return ComposeIssue(
            f"Hop {i}: “{pb}” cannot follow Mullvad SOCKS (“{pa}”).",
            hop_index=i,
        )

    # --- After VPN (system route underlay) ---
    if a == "vpn":
        if b in ("tor_local", "reality", "proxy_remote", "proxy_local", "mullvad_socks", "tor_remote"):
            # Mullvad after VPN is odd; still blocked by mullvad-first rule above
            # when b == mullvad_socks.
            if b == "mullvad_socks":
                return ComposeIssue(
                    f"Hop {i}: Mullvad app SOCKS cannot follow VPN — "
                    f"use Mullvad first or VPN alone.",
                    hop_index=i,
                )
            return None
        return ComposeIssue(
            f"Hop {i}: “{pb}” cannot follow VPN (“{pa}”).",
            hop_index=i,
        )

    # --- After remote proxy ---
    if a == "proxy_remote":
        if b == "proxy_remote":
            return None
        if b in ("tor_local", "reality", "proxy_local", "mullvad_socks"):
            return ComposeIssue(
                f"Hop {i}: cannot chain remote proxy “{pa}” into local hop “{pb}” — "
                f"a remote SOCKS cannot dial this host’s loopback or Mullvad tunnel. "
                f"Use a remote endpoint, or put Mullvad/VPN first as underlay.",
                hop_index=i,
            )
        return ComposeIssue(
            f"Hop {i}: “{pb}” cannot follow remote proxy “{pa}”.",
            hop_index=i,
        )

    # --- After local (non-tunnel) proxy ---
    if a == "proxy_local":
        if b in ("proxy_local", "tor_local", "proxy_remote"):
            return None
        if b == "reality":
            return ComposeIssue(
                f"Hop {i}: REALITY should not follow a local proxy chain — "
                f"use REALITY alone or after Mullvad/VPN underlay.",
                hop_index=i,
            )
        if b == "mullvad_socks":
            return ComposeIssue(
                f"Hop {i}: Mullvad app SOCKS can only be first.",
                hop_index=i,
            )
        return ComposeIssue(
            f"Hop {i}: “{pb}” cannot follow local proxy “{pa}”.",
            hop_index=i,
        )

    # --- Remote Tor (unusual) ---
    if a == "tor_remote":
        if b == "proxy_remote":
            return None
        return ComposeIssue(
            f"Hop {i}: “{pb}” cannot follow Tor “{pa}”.",
            hop_index=i,
        )

    return ComposeIssue(
        f"Hop {i}: cannot place “{pb}” after “{pa}”.",
        hop_index=i,
    )


def composition_issues(
    profile: Profile | None,
    backends: BackendStore,
) -> list[ComposeIssue]:
    """All composition errors for a profile (empty if OK or incomplete bindings)."""
    if profile is None or not profile.hops:
        return []

    resolved: list[tuple[Hop, Backend]] = []
    for hop in profile.hops:
        if not hop.backend_id:
            return []  # unbound — structural readiness handles this
        b = backends.get(hop.backend_id)
        if b is None:
            return []
        resolved.append((hop, b))

    issues: list[ComposeIssue] = []

    # Adjacent pair rules (includes Mullvad-not-first, REALITY-must-be-last, …)
    for i in range(len(resolved) - 1):
        hop_a, ba = resolved[i]
        hop_b, bb = resolved[i + 1]
        issue = validate_hop_pair(
            ba, bb, prev_hop=hop_a, next_hop=hop_b, next_index=i + 2
        )
        if issue is not None:
            issues.append(issue)

    return issues


def composition_ok(profile: Profile | None, backends: BackendStore) -> bool:
    return not composition_issues(profile, backends)


def can_append_hop(
    existing: list[Hop],
    new_kind: str,
    new_backend_id: str,
    backends: BackendStore,
) -> ComposeIssue | None:
    """If adding this hop would break composition, return the issue."""
    if not existing:
        # First hop: Mullvad/REALITY/Tor/VPN/Proxy all fine alone
        return None
    draft = Profile(
        id="draft",
        name="draft",
        hops=[Hop(h.kind, h.backend_id) for h in existing]
        + [Hop(new_kind, new_backend_id)],
    )
    issues = composition_issues(draft, backends)
    return issues[0] if issues else None
