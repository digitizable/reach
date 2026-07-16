"""Dashboard path information: defaults, custom text, user overrides."""

from __future__ import annotations

from core.backends import BackendStore
from core.profiles import DEFAULT_PROFILES, Profile

# Built-in copy for seed profile IDs (first-run defaults).
# Shown when profile.info is empty. Custom profiles fall back to CUSTOM_INFO.
# Every description includes an "ISP sees:" line.
DEFAULT_PROFILE_INFO: dict[str, str] = {
    "stealth-entry": (
        "Stealth entry — REALITY only.\n\n"
        "Traffic goes through a local Xray client (VLESS + REALITY + Vision) to "
        "your REALITY server, then out to the internet.\n\n"
        "• Public exit: your REALITY server’s IP\n"
        "• ISP sees: encrypted, HTTPS-like traffic to that server (not a classic "
        "open SOCKS fingerprint); SNI may look like a normal site name\n"
        "• Websites: see the REALITY server, not your home IP\n\n"
        "Fill in the REALITY backend (or paste a vless:// link) under Backends "
        "before Connect. REALITY must be the only hop (or last hop) on a path."
    ),
    "mullvad-only": (
        "Mullvad only — app SOCKS exit.\n\n"
        "Uses Mullvad’s in-tunnel SOCKS (10.64.0.1:1080) while the Mullvad app "
        "is Connected. The app full-tunnels the system.\n\n"
        "• Public exit: a Mullvad exit\n"
        "• ISP sees: a tunnel to Mullvad (WireGuard/OpenVPN-style), not your "
        "final websites\n"
        "• Requires: Mullvad app Connected (Spectre can auto-connect it)\n"
        "• Not true “apps only”: while Mullvad is up, the whole machine is on Mullvad"
    ),
    "mullvad-reality": (
        "Mullvad into REALITY — underlay then stealth exit.\n\n"
        "Mullvad app SOCKS is first (OS underlay). Spectre dials REALITY only; "
        "it cannot SOCKS-nest 10.64.0.1 through the VPS.\n\n"
        "• Public exit: REALITY server (not Mullvad)\n"
        "• ISP sees: the Mullvad tunnel first (not Hetzner REALITY as the outer hop); "
        "REALITY runs inside that underlay\n"
        "• Mullvad Connection Check often shows “not Mullvad” — expected"
    ),
    "mullvad-tor": (
        "Mullvad into Tor — underlay then Tor exit.\n\n"
        "Mullvad is the OS underlay; Spectre dials system Tor only (no "
        "SOCKS-chain of 10.64.0.1 into 127.0.0.1:9050).\n\n"
        "• Public exit: a Tor exit relay\n"
        "• ISP sees: a tunnel to Mullvad; Tor runs inside that underlay "
        "(Host → Mullvad → Tor)\n"
        "• Requires: Mullvad Connected + local Tor on 9050 (or Whonix Gateway)"
    ),
    "tor-only": (
        "Tor only — system Tor exit.\n\n"
        "Spectre dials the system (or Whonix Gateway) Tor SOCKS.\n\n"
        "• Public exit: a Tor exit\n"
        "• ISP sees: connections toward Tor, not your final websites\n"
        "• Requires: Tor running (or Whonix Gateway SOCKS reachable)"
    ),
    "vpn-only": (
        "VPN only — Spectre-owned WireGuard.\n\n"
        "Spectre brings up the WireGuard .conf on the VPN backend (wg-quick) "
        "and uses that tunnel as the path.\n\n"
        "• Public exit: the VPN endpoint\n"
        "• ISP sees: WireGuard (or similar) to the VPN endpoint, not your final websites\n"
        "• Does not require the Mullvad app (unless the .conf is from Mullvad)\n"
        "• Enable the VPN backend and set vpn_config to a .conf path first"
    ),
    "vpn-tor": (
        "VPN into Tor — WireGuard underlay, then Tor exit.\n\n"
        "Spectre brings up WireGuard, then dials Tor so the public exit is Tor.\n\n"
        "• Public exit: a Tor exit\n"
        "• ISP sees: WireGuard to the VPN endpoint; Tor runs inside that underlay\n"
        "• Requires: working WireGuard backend + system Tor"
    ),
}

CUSTOM_INFO_PLACEHOLDER = (
    "Custom configuration.\n\n"
    "• ISP sees: (edit this text — describe what your home ISP observes on the wire)"
)

DEFAULT_PROFILE_IDS: frozenset[str] = frozenset(p.id for p in DEFAULT_PROFILES)


def is_default_profile_id(profile_id: str) -> bool:
    return profile_id in DEFAULT_PROFILE_IDS


def resolve_profile_info(profile: Profile | None) -> str:
    """Text shown in the dashboard info dialog."""
    if profile is None:
        return (
            "No profile selected.\n\n"
            "Choose a path under Profiles. Use the info button to read what the "
            "selected configuration does."
        )
    custom = (profile.info or "").strip()
    if custom:
        return custom
    built_in = DEFAULT_PROFILE_INFO.get(profile.id)
    if built_in:
        return built_in
    return CUSTOM_INFO_PLACEHOLDER


def path_info_text(
    profile: Profile | None,
    backends: BackendStore | None = None,
    *,
    routing_mode: str = "system",
    connected: bool = False,
) -> tuple[str, str]:
    """Return (heading, body) for the dashboard info dialog.

    backends / routing_mode / connected are accepted for call-site compatibility;
    body content is the stored or default profile info text.
    """
    _ = backends, routing_mode, connected
    if profile is None:
        return "Path information", resolve_profile_info(None)
    name = profile.name.strip() or "Profile"
    return f"About “{name}”", resolve_profile_info(profile)
