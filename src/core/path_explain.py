"""Human-readable path roles: who is the exit vs underlay / not dialed.

The profile hop list is a recipe. After SOCKS-chain normalization the live
dial path can drop hops. This module labels each hop so Home and Profiles
do not imply the last listed hop is always the public exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.backends import BackendStore
from core.profiles import Profile
from core.readiness import (
    is_mullvad_app_socks,
    profile_is_mullvad_then_local_exit,
    profile_is_mullvad_then_local_tor,
    profile_is_reality_then_mullvad,
)


@dataclass
class HopDisplay:
    """One node in the path diagram."""

    kind: str  # icon key (REALITY, Proxy, Tor, …)
    label: str  # main name under the icon
    role: str = "hop"  # entry | hop | exit | underlay | not-exit
    sublabel: str = ""  # short role text under the name


@dataclass
class PathExplain:
    """Planned or live path presentation."""

    hops: list[HopDisplay] = field(default_factory=list)
    # One line under the diagram — plain English about the public exit.
    caption: str = ""
    # Compact list line for Profiles rows.
    hops_line: str = ""
    # True when the diagram is not a literal SOCKS chain to the last hop.
    rewritten: bool = False

    @property
    def kinds(self) -> list[str]:
        return [h.kind for h in self.hops]

    @property
    def labels(self) -> list[str]:
        return [h.label for h in self.hops]

    @property
    def roles(self) -> list[str]:
        return [h.role for h in self.hops]

    @property
    def sublabels(self) -> list[str]:
        return [h.sublabel for h in self.hops]


def _names(profile: Profile, backends: BackendStore) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for hop in profile.hops:
        b = backends.get(hop.backend_id) if hop.backend_id else None
        label = b.name if b is not None and b.name else hop.kind
        out.append((hop.kind, label))
    return out


def _default_roles(n: int) -> list[tuple[str, str]]:
    """role, sublabel for each index assuming last hop is exit."""
    if n <= 0:
        return []
    if n == 1:
        return [("exit", "exit")]
    roles: list[tuple[str, str]] = [("entry", "entry")]
    for _ in range(1, n - 1):
        roles.append(("hop", ""))
    roles.append(("exit", "exit"))
    return roles


def explain_profile(profile: Profile | None, backends: BackendStore) -> PathExplain:
    """Label a planned profile (before / independent of core rewrite)."""
    if profile is None or not profile.hops:
        return PathExplain(caption="Choose a profile", hops_line="No hops")

    names = _names(profile, backends)
    n = len(names)
    roles = _default_roles(n)
    hops = [
        HopDisplay(kind=kind, label=label, role=roles[i][0], sublabel=roles[i][1])
        for i, (kind, label) in enumerate(names)
    ]

    rewritten = False
    caption = f"Public exit is {hops[-1].label}."

    # Invalid nestings (e.g. REALITY → Mullvad) are blocked in path_compose /
    # readiness — do not present them as a rewritten live path.
    from core.path_compose import composition_issues

    compose_errs = composition_issues(profile, backends)
    if compose_errs:
        caption = compose_errs[0].message
        rewritten = False
        # Mark conflicting hops so the diagram still shows the problem.
        if profile_is_reality_then_mullvad(profile, backends):
            for i, hop in enumerate(profile.hops):
                b = backends.get(hop.backend_id) if hop.backend_id else None
                if hop.kind == "REALITY":
                    hops[i].role = "exit"
                    hops[i].sublabel = "exit"
                elif b is not None and is_mullvad_app_socks(b):
                    hops[i].role = "not-exit"
                    hops[i].sublabel = "invalid"
        return PathExplain(
            hops=hops,
            caption=caption,
            hops_line=_format_hops_line(hops) + " · invalid",
            rewritten=False,
        )

    if profile_is_mullvad_then_local_exit(profile, backends):
        rewritten = True
        exit_label = ""
        for i, hop in enumerate(profile.hops):
            b = backends.get(hop.backend_id) if hop.backend_id else None
            if b is not None and is_mullvad_app_socks(b):
                hops[i].role = "underlay"
                hops[i].sublabel = "underlay"
            elif hop.kind in ("Tor", "REALITY"):
                hops[i].role = "exit"
                hops[i].sublabel = "exit"
                exit_label = hops[i].label
        if not exit_label:
            exit_label = next((h.label for h in hops if h.role == "exit"), "exit")
        via = "Tor" if profile_is_mullvad_then_local_tor(profile, backends) else "REALITY"
        caption = (
            f"Public exit is {exit_label}. "
            f"Mullvad is the OS tunnel underlay — not SOCKS-chained into {via}."
        )

    return PathExplain(
        hops=hops,
        caption=caption,
        hops_line=_format_hops_line(hops),
        rewritten=rewritten,
    )


def explain_live(
    hop_names: list[str],
    *,
    hop_details: list[dict] | None = None,
    profile: Profile | None = None,
    backends: BackendStore | None = None,
) -> PathExplain:
    """Label a live path. Prefer profile-based explain when hops match the recipe."""
    if profile is not None and backends is not None and profile.hops:
        planned = explain_profile(profile, backends)
        if hop_names and len(hop_names) == len(planned.hops):
            for i, name in enumerate(hop_names):
                if name:
                    planned.hops[i].label = str(name)
            planned.hops_line = _format_hops_line(planned.hops)
            if planned.rewritten:
                return planned
        elif not hop_names:
            return planned

    if not hop_names:
        return PathExplain(caption="", hops_line="")

    n = len(hop_names)
    roles = _default_roles(n)
    hops = [
        HopDisplay(
            kind=str(name),
            label=str(name),
            role=roles[i][0],
            sublabel=roles[i][1],
        )
        for i, name in enumerate(hop_names)
    ]

    if hop_details:
        for d in hop_details:
            if not isinstance(d, dict):
                continue
            name = str(d.get("name") or "")
            detail = str(d.get("detail") or "").lower()
            for h in hops:
                if name and h.label != name:
                    continue
                if (
                    "not socks-chained after prior hop" in detail
                    or "exit is not mullvad" in detail
                ):
                    h.role = "not-exit"
                    h.sublabel = "not exit"
                elif "system tunnel underlay" in detail or (
                    "underlay" in detail and "not socks" in detail
                ):
                    h.role = "underlay"
                    h.sublabel = "underlay"
                break
        # REALITY before a not-exit hop is the public exit.
        for i, h in enumerate(hops):
            if h.role == "not-exit":
                for j in range(i - 1, -1, -1):
                    prev = hops[j]
                    if (
                        prev.kind.upper() == "REALITY"
                        or "reality" in prev.label.lower()
                    ):
                        prev.role = "exit"
                        prev.sublabel = "exit"
                        break
            if h.role == "underlay":
                for j in range(i + 1, n):
                    nxt = hops[j]
                    if nxt.kind.upper() == "TOR" or "tor" in nxt.label.lower():
                        nxt.role = "exit"
                        nxt.sublabel = "exit"
                        break

    exit_hops = [h for h in hops if h.role == "exit"]
    not_exit = [h for h in hops if h.role == "not-exit"]
    underlay = [h for h in hops if h.role == "underlay"]
    rewritten = bool(not_exit or underlay)

    if not_exit and exit_hops:
        caption = (
            f"Public exit is {exit_hops[0].label}. "
            f"{not_exit[0].label} is not dialed after the prior hop."
        )
    elif underlay and exit_hops:
        caption = (
            f"Public exit is {exit_hops[0].label}. "
            f"{underlay[0].label} is OS underlay only."
        )
    elif exit_hops:
        caption = f"Public exit is {exit_hops[0].label}."
    else:
        caption = ""

    return PathExplain(
        hops=hops,
        caption=caption,
        hops_line=_format_hops_line(hops),
        rewritten=rewritten,
    )


def _format_hops_line(hops: list[HopDisplay]) -> str:
    if not hops:
        return "No hops"
    parts: list[str] = []
    for h in hops:
        if h.role == "exit":
            parts.append(f"{h.label} (exit)")
        elif h.role == "not-exit":
            parts.append(f"{h.label} (not exit)")
        elif h.role == "underlay":
            parts.append(f"{h.label} (underlay)")
        else:
            parts.append(h.label)
    return " → ".join(parts)
