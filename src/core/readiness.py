"""Desktop-side readiness checks before the core is involved.

These checks answer: “Is this profile fully backed by configured backends?”
Connect is allowed only when ok is True; the core (when present) still owns
the live tunnel.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.backends import BackendStore
from core.profiles import Profile


@dataclass
class Readiness:
    ok: bool
    issues: list[str]

    @property
    def summary(self) -> str:
        if self.ok:
            return "Ready for core"
        return self.issues[0] if self.issues else "Not ready"

    @property
    def detail(self) -> str:
        if self.ok:
            return "All hops have complete backends."
        return " · ".join(self.issues)


def profile_readiness(profile: Profile | None, backends: BackendStore) -> Readiness:
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
    return Readiness(ok=not issues, issues=issues)


def profile_status_tag(profile: Profile | None, backends: BackendStore) -> str:
    """Short tag for list rows: ready / incomplete / unbound."""
    ready = profile_readiness(profile, backends)
    if ready.ok:
        return "ready"
    if profile is None or not profile.hops:
        return "empty"
    unbound = any(not h.backend_id for h in profile.hops)
    if unbound:
        return "unbound"
    return "incomplete"
