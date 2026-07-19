"""Unit tests: Reach China requires VPN underlay before China hop."""

from __future__ import annotations

from core.backends import Backend, BackendStore
from core.profiles import Hop, Profile
from core.readiness import (
    ingress_cn_issues,
    is_ingress_cn_profile,
    is_ingress_cn_reverse_profile,
    is_vpn_underlay,
    profile_readiness,
)
from core.reverse_agent import (
    PATH_INTENT_REVERSE,
    ReversePairing,
    china_agent_xray_config,
    is_reverse_intent,
    outside_accept_xray_config,
)


def _vpn() -> Backend:
    return Backend(
        id="vpn-1",
        kind="VPN",
        name="WG",
        enabled=True,
        vpn_protocol="WireGuard",
        vpn_config="/tmp/fake.conf",
    )


def _reality(*, sni: str = "www.example.com") -> Backend:
    return Backend(
        id="r-1",
        kind="REALITY",
        name="CN",
        enabled=True,
        reality_server="1.2.3.4",
        reality_port=443,
        reality_uuid="00000000-0000-0000-0000-000000000001",
        reality_public_key="pk",
        reality_sni=sni,
    )


def _store(*backends: Backend) -> BackendStore:
    # Bypass disk: construct empty and inject
    s = BackendStore.__new__(BackendStore)
    s._path = None  # type: ignore[assignment]
    s._backends = list(backends)
    return s  # type: ignore[return-value]


def test_vpn_underlay_kinds() -> None:
    assert is_vpn_underlay(_vpn())
    mullvad = Backend(
        id="m",
        kind="Proxy",
        name="Mullvad SOCKS",
        proxy_host="10.64.0.1",
        proxy_port=1080,
        enabled=True,
    )
    assert is_vpn_underlay(mullvad)
    assert not is_vpn_underlay(_reality())


def test_single_hop_ingress_blocked() -> None:
    r = _reality()
    p = Profile(
        id="p",
        name="Reach China",
        path_intent="ingress_cn",
        hops=[Hop("REALITY", r.id)],
    )
    issues = ingress_cn_issues(p, _store(r))
    assert any("VPN underlay" in m or "two hops" in m for m in issues)


def test_vpn_then_reality_ok_structurally() -> None:
    v, r = _vpn(), _reality()
    p = Profile(
        id="p",
        name="Reach China",
        path_intent="ingress_cn",
        hops=[Hop("VPN", v.id), Hop("REALITY", r.id)],
    )
    issues = ingress_cn_issues(p, _store(v, r))
    assert issues == []


def test_reality_without_sni_blocked() -> None:
    v, r = _vpn(), _reality(sni="")
    p = Profile(
        id="p",
        name="Reach China",
        path_intent="ingress_cn",
        hops=[Hop("VPN", v.id), Hop("REALITY", r.id)],
    )
    issues = ingress_cn_issues(p, _store(v, r))
    assert any("SNI" in m for m in issues)


def test_is_ingress_cn_profile() -> None:
    p = Profile(id="x", name="Reach China lab", hops=[])
    assert is_ingress_cn_profile(p)
    p2 = Profile(id="y", name="Other", path_intent="ingress_cn", hops=[])
    assert is_ingress_cn_profile(p2)


def test_profile_readiness_enforces_vpn() -> None:
    r = _reality()
    p = Profile(
        id="p",
        name="Reach China",
        path_intent="ingress_cn",
        hops=[Hop("REALITY", r.id)],
    )
    ready = profile_readiness(p, _store(r), live=False)
    assert not ready.ok
    assert any("VPN" in i or "underlay" in i or "two hops" in i for i in ready.issues)


def _map_socks() -> Backend:
    return Backend(
        id="map-1",
        kind="Proxy",
        name="Reverse map",
        enabled=True,
        proxy_protocol="SOCKS5",
        proxy_host="127.0.0.1",
        proxy_port=10808,
    )


def test_reverse_vpn_then_map_ok() -> None:
    v, m = _vpn(), _map_socks()
    p = Profile(
        id="p",
        name="Reach China reverse",
        path_intent=PATH_INTENT_REVERSE,
        hops=[Hop("VPN", v.id), Hop("Proxy", m.id)],
        notes="composition=reverse",
    )
    assert is_ingress_cn_reverse_profile(p)
    assert is_ingress_cn_profile(p)
    issues = ingress_cn_issues(p, _store(v, m))
    assert issues == []


def test_reverse_rejects_reality_last_hop() -> None:
    v, r = _vpn(), _reality()
    p = Profile(
        id="p",
        name="bad reverse",
        path_intent=PATH_INTENT_REVERSE,
        hops=[Hop("VPN", v.id), Hop("REALITY", r.id)],
    )
    issues = ingress_cn_issues(p, _store(v, r))
    assert any("SOCKS map" in m or "Proxy" in m for m in issues)


def test_reverse_intent_helpers() -> None:
    assert is_reverse_intent(PATH_INTENT_REVERSE, "")
    assert is_reverse_intent("ingress_cn", "composition=reverse · foo")
    assert not is_reverse_intent("ingress_cn", "composition=inbound")


def test_agent_config_json_shape() -> None:
    p = ReversePairing(
        accept_host="accept.example",
        accept_port=443,
        uuid="00000000-0000-0000-0000-000000000099",
        public_key="pub",
        private_key="priv",
        short_id="abcd",
        dest_sni="www.example.com",
        dest_addr="www.example.com:443",
        map_socks_host="127.0.0.1",
        map_socks_port=10808,
    )
    acc = outside_accept_xray_config(p)
    ag = china_agent_xray_config(p)
    assert any(i.get("tag") == "socks-map" for i in acc["inbounds"])
    agent_in = next(i for i in acc["inbounds"] if i.get("tag") == "agent-in")
    assert agent_in["settings"]["clients"][0]["reverse"]["tag"] == "reverse-out"
    to_accept = next(o for o in ag["outbounds"] if o.get("tag") == "to-accept")
    assert to_accept["settings"]["address"] == "accept.example"
    assert to_accept["settings"]["reverse"]["tag"] == "reverse-in"


if __name__ == "__main__":
    test_vpn_underlay_kinds()
    test_single_hop_ingress_blocked()
    test_vpn_then_reality_ok_structurally()
    test_reality_without_sni_blocked()
    test_is_ingress_cn_profile()
    test_profile_readiness_enforces_vpn()
    test_reverse_vpn_then_map_ok()
    test_reverse_rejects_reality_last_hop()
    test_reverse_intent_helpers()
    test_agent_config_json_shape()
    print("ok")
