"""Detect accidental launch inside the clearnet exclude netns.

Reach must run on the host network namespace. If it is started
inside ``clearnet`` (e.g. via clearnet-run), Mullvad SOCKS at 10.64.0.1 is
unreachable and Connect appears frozen or fails after long timeouts.
"""

from __future__ import annotations

import os
from pathlib import Path


def in_clearnet_netns() -> bool:
    """True when this process is inside the Spectre clearnet exclude netns."""
    # Explicit env from clearnet-run / helpers
    if os.environ.get("CLEARNET_NS") or os.environ.get("SPECTRE_CLEARNET") == "1":
        return True

    ns = Path("/proc/self/ns/net")
    clearnet = Path("/run/netns/clearnet")
    try:
        if clearnet.exists() and ns.exists() and ns.stat().st_ino == clearnet.stat().st_ino:
            return True
    except OSError:
        pass

    # Fallback: default route only via cn-ns and no real uplink / mullvad iface
    try:
        route = Path("/proc/self/net/route").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    has_default_cn = False
    for line in route.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        iface, dest = parts[0], parts[1]
        # default destination is 00000000
        if dest == "00000000" and iface in ("cn-ns", "cn-host"):
            has_default_cn = True
            break
    if not has_default_cn:
        return False
    # Confirmed exclude-style netns if wg/mullvad uplink is absent here
    try:
        dev = Path("/proc/self/net/dev").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return has_default_cn
    if "wg0-mullvad" in dev or "wls" in dev or "wlan" in dev or "eth" in dev or "enp" in dev or "eno" in dev:
        # Host can also have cn-host; presence of real uplink means host.
        if "wg0-mullvad" in dev or any(
            f":" in line and any(x in line for x in ("wls", "wlan", "eth", "enp", "eno", "wlp"))
            for line in dev.splitlines()
        ):
            # If we have real ifaces, not clearnet-only.
            lines = [
                ln.split(":", 1)[0].strip()
                for ln in dev.splitlines()[2:]
                if ":" in ln
            ]
            real = [
                n
                for n in lines
                if n
                not in ("lo", "cn-ns", "cn-host")
                and not n.startswith(("veth", "br-", "virbr", "docker"))
            ]
            if real:
                return False
    return has_default_cn


def clearnet_netns_block_message() -> str:
    return (
        "Reach is running inside the clearnet exclude network "
        "(wrong place for Connect). Quit and open Spectre from the menu — "
        "not via Exclude / clearnet-run."
    )
