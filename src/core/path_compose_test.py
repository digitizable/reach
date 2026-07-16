"""Unit tests for hop composition rules (no GTK)."""

from __future__ import annotations

from pathlib import Path

from core.backends import Backend, BackendStore
from core.path_compose import can_append_hop, composition_issues
from core.profiles import Hop, Profile


def _store(*backends: Backend) -> BackendStore:
    s = BackendStore.__new__(BackendStore)
    s._path = Path("/tmp/spectre-compose-test-backends.json")
    s._backends = list(backends)
    return s


def _reality() -> Backend:
    return Backend(
        id="r1",
        kind="REALITY",
        name="Hetzner REALITY",
        reality_server="1.2.3.4",
        reality_uuid="u",
        reality_public_key="k",
    )


def _mullvad() -> Backend:
    return Backend(
        id="m1",
        kind="Proxy",
        name="Mullvad SOCKS",
        proxy_host="10.64.0.1",
        proxy_port=1080,
        proxy_protocol="socks5",
    )


def _tor() -> Backend:
    return Backend(
        id="t1",
        kind="Tor",
        name="System Tor",
        tor_use_system=True,
        tor_socks_host="127.0.0.1",
        tor_socks_port=9050,
    )


def _remote_proxy() -> Backend:
    return Backend(
        id="p1",
        kind="Proxy",
        name="Remote SOCKS",
        proxy_host="203.0.113.9",
        proxy_port=1080,
        proxy_protocol="socks5",
    )


def test_reality_then_mullvad_blocked() -> None:
    store = _store(_reality(), _mullvad())
    p = Profile(
        id="p",
        name="bad",
        hops=[Hop("REALITY", "r1"), Hop("Proxy", "m1")],
    )
    issues = composition_issues(p, store)
    assert issues, "REALITY → Mullvad must be blocked"
    blob = " ".join(i.message for i in issues).lower()
    assert "mullvad" in blob and (
        "first hop" in blob or "cannot follow" in blob or "reality" in blob
    )


def test_mullvad_then_tor_allowed() -> None:
    store = _store(_mullvad(), _tor())
    p = Profile(
        id="p",
        name="ok",
        hops=[Hop("Proxy", "m1"), Hop("Tor", "t1")],
    )
    assert not composition_issues(p, store)


def test_mullvad_then_reality_allowed() -> None:
    store = _store(_mullvad(), _reality())
    p = Profile(
        id="p",
        name="ok",
        hops=[Hop("Proxy", "m1"), Hop("REALITY", "r1")],
    )
    assert not composition_issues(p, store)


def test_reality_alone_ok() -> None:
    store = _store(_reality())
    p = Profile(id="p", name="ok", hops=[Hop("REALITY", "r1")])
    assert not composition_issues(p, store)


def test_remote_then_tor_blocked() -> None:
    store = _store(_remote_proxy(), _tor())
    p = Profile(
        id="p",
        name="bad",
        hops=[Hop("Proxy", "p1"), Hop("Tor", "t1")],
    )
    assert composition_issues(p, store)


def test_can_append_blocks_mullvad_after_reality() -> None:
    store = _store(_reality(), _mullvad())
    issue = can_append_hop(
        [Hop("REALITY", "r1")], "Proxy", "m1", store
    )
    assert issue is not None


if __name__ == "__main__":
    test_reality_then_mullvad_blocked()
    test_mullvad_then_tor_allowed()
    test_mullvad_then_reality_allowed()
    test_reality_alone_ok()
    test_remote_then_tor_blocked()
    test_can_append_blocks_mullvad_after_reality()
    print("path_compose_test: OK")
