"""Parse vless:// REALITY share links for the desktop editor."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse


def parse_vless_uri(raw: str) -> dict:
    """
    Parse a vless:// share link into backend field names.

    Example:
      vless://uuid@host:443?encryption=none&security=reality&sni=...&fp=chrome&pbk=...&sid=...&flow=xtls-rprx-vision#Name
    """
    raw = raw.strip()
    if not raw.lower().startswith("vless://"):
        raise ValueError("Not a vless:// link")
    u = urlparse(raw)
    uuid = u.username or ""
    if not uuid:
        raise ValueError("Link missing UUID")
    host = u.hostname or ""
    if not host:
        raise ValueError("Link missing host")
    port = u.port or 443
    q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
    sec = (q.get("security") or "").lower()
    if sec and sec != "reality":
        raise ValueError(f"Only security=reality is supported (got {sec!r})")
    pbk = q.get("pbk") or q.get("publicKey") or q.get("public_key") or ""
    if not pbk:
        raise ValueError("Link missing public key (pbk)")
    name = unquote(u.fragment) if u.fragment else f"REALITY {host}"
    return {
        "name": name,
        "kind": "REALITY",
        "reality_server": host,
        "reality_port": int(port),
        "reality_uuid": uuid,
        "reality_public_key": pbk,
        "reality_short_id": q.get("sid") or q.get("shortId") or q.get("short_id") or "",
        "reality_sni": q.get("sni") or q.get("serverName") or q.get("server_name") or "",
        "reality_flow": q.get("flow") or "xtls-rprx-vision",
        "reality_fingerprint": q.get("fp") or q.get("fingerprint") or "chrome",
        "reality_spider_x": q.get("spx") or q.get("spiderX") or q.get("spider_x") or "",
    }
