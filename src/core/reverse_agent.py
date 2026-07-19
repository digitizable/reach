"""Composition III — reverse rendezvous configs and helpers.

China-side agent dials **out** with TCP + REALITY using Xray **VLESS reverse**.
Outside accept exposes a SOCKS map for Spectre:

  VPN underlay → Proxy(SOCKS on accept map)

Docs: Xray “VLESS reverse proxy” (level-2) — not legacy reverse portals.
Generate keys: ``xray x25519``.

Research: reverse-rendezvous, transport-cover-stack, method-selection.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any


# Generic territory ingress (China remains first-class; codes live in notes territory=XX)
PATH_INTENT_REVERSE = "ingress_territory_reverse"
PATH_INTENT_INBOUND = "ingress_territory"
# Legacy aliases (profiles saved before territory generalization)
PATH_INTENT_REVERSE_LEGACY = "ingress_cn_reverse"
PATH_INTENT_INBOUND_LEGACY = "ingress_cn"


@dataclass
class ReversePairing:
    """Shared secrets / endpoints for accept + agent."""

    accept_host: str
    accept_port: int
    uuid: str
    public_key: str  # accept REALITY public (agent client)
    private_key: str  # accept REALITY private (portal server)
    short_id: str
    dest_sni: str
    dest_addr: str
    map_socks_host: str
    map_socks_port: int
    pairing_token: str = ""

    def token(self) -> str:
        return (self.pairing_token or self.uuid).strip()


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_short_id() -> str:
    return secrets.token_hex(4)


def new_pairing_token() -> str:
    return secrets.token_urlsafe(18)


def is_reverse_intent(path_intent: str | None, notes: str | None = None) -> bool:
    intent = (path_intent or "").strip()
    if intent in (PATH_INTENT_REVERSE, PATH_INTENT_REVERSE_LEGACY):
        return True
    n = (notes or "").lower()
    return (
        "composition=reverse" in n
        or "path_intent=ingress_cn_reverse" in n
        or "path_intent=ingress_territory_reverse" in n
        or "inverse snowflake" in n
    )


def is_any_ingress_intent(
    path_intent: str | None, notes: str | None = None, name: str | None = None
) -> bool:
    intent = (path_intent or "").strip()
    if intent in (
        PATH_INTENT_INBOUND,
        PATH_INTENT_REVERSE,
        PATH_INTENT_INBOUND_LEGACY,
        PATH_INTENT_REVERSE_LEGACY,
    ):
        return True
    n = (notes or "").lower()
    if (
        "path_intent=ingress_cn" in n
        or "ingress_cn" in n
        or "path_intent=ingress_territory" in n
        or "ingress_territory" in n
        or "territory=" in n
    ):
        return True
    name_l = (name or "").strip().lower()
    return name_l.startswith("reach china") or name_l.startswith("reach ·")


def outside_accept_xray_config(p: ReversePairing) -> dict[str, Any]:
    """Outside **accept** host (public side).

    - VLESS+REALITY inbound: client with reverse.tag → becomes outbound ``reverse-out``
    - SOCKS map inbound: Spectre uses this after reverse is up
    - Route socks-map → reverse-out
    - Always keep a freedom default outbound
    """
    dest = (p.dest_addr or "").strip() or f"{p.dest_sni}:443"
    private_key = (p.private_key or "").strip() or "REPLACE_WITH_XRAY_X25519_PRIVATE"
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "agent-in",
                "listen": "0.0.0.0",
                "port": int(p.accept_port),
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": [
                        {
                            "id": p.uuid,
                            "email": "spectre-bridge",
                            "flow": "",
                            "reverse": {
                                "tag": "reverse-out",
                            },
                        }
                    ],
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": dest,
                        "xver": 0,
                        "serverNames": [p.dest_sni],
                        "privateKey": private_key,
                        "shortIds": [p.short_id or ""],
                    },
                },
            },
            {
                "tag": "socks-map",
                "listen": p.map_socks_host or "127.0.0.1",
                "port": int(p.map_socks_port),
                "protocol": "socks",
                "settings": {
                    "udp": False,
                    "auth": "noauth",
                },
            },
        ],
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "freedom",
            },
            {
                "tag": "block",
                "protocol": "blackhole",
            },
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["socks-map"],
                    "outboundTag": "reverse-out",
                }
            ],
        },
        "_spectre": {
            "role": "outside_accept",
            "composition": "III",
            "mechanism": "vless-reverse",
            "note": (
                "xray x25519 → privateKey here, publicKey in china-agent.json. "
                "Start china-agent first (or restart accept after agent is up). "
                "SSH -L map port if accept is remote."
            ),
            "pairing_token": p.token(),
        },
    }


def china_agent_xray_config(p: ReversePairing) -> dict[str, Any]:
    """China-side **agent** (internal side).

    VLESS outbound with reverse.tag appears as local inbound ``reverse-in``.
    Route reverse-in → freedom (egress via CN network).
    """
    pub = (p.public_key or "").strip() or "REPLACE_WITH_ACCEPT_REALITY_PUBLIC_KEY"
    # Xray requires *simplified* VLESS outbound style for reverse (not vnext).
    return {
        "log": {"loglevel": "warning"},
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "freedom",
            },
            {
                "tag": "reverse-direct",
                "protocol": "freedom",
                "settings": {},
            },
            {
                "tag": "to-accept",
                "protocol": "vless",
                "settings": {
                    "address": p.accept_host,
                    "port": int(p.accept_port),
                    "id": p.uuid,
                    "encryption": "none",
                    "flow": "",
                    "reverse": {
                        "tag": "reverse-in",
                    },
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "fingerprint": "chrome",
                        "serverName": p.dest_sni,
                        "publicKey": pub,
                        "shortId": p.short_id or "",
                        "spiderX": "",
                    },
                },
            },
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["reverse-in"],
                    "outboundTag": "reverse-direct",
                }
            ],
        },
        "_spectre": {
            "role": "china_agent",
            "composition": "III",
            "mechanism": "vless-reverse",
            "note": (
                "Dial-out only. TCP REALITY cover. Start this before or with accept; "
                "restart accept if map was up before agent connected."
            ),
            "pairing_token": p.token(),
            "accept": f"{p.accept_host}:{p.accept_port}",
        },
    }


def dumps_config(cfg: dict[str, Any], *, runtime: bool = False) -> str:
    """Pretty JSON. runtime=True drops _spectre metadata."""
    out = dict(cfg)
    if runtime:
        out.pop("_spectre", None)
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def inverse_snowflake_readme(p: ReversePairing) -> str:
    """Volunteer-facing inverse Snowflake client instructions."""
    token = p.token()
    accept = f"{p.accept_host}:{p.accept_port}"
    return f"""# Inverse Snowflake client (Spectre)

## What this is

**Tor Snowflake:** volunteers help people *leave* a censored network.  
**Inverse Snowflake (this package):** you run a small client that *dials out* to
the researcher's outside accept. Your network path becomes the map for their
SOCKS traffic (Composition III reverse).

You are volunteering **dial-out capacity**, not installing a mystery VPN.

## Requirements

- Python 3
- Outbound TCP to `{accept}` (and DATA ports if accept uses a fixed range)

## Run (foothold / peer / lab)

```bash
chmod +x run-inverse-snowflake.sh
./run-inverse-snowflake.sh

# or:
python3 spectre-inverse-snowflake.py --config pairing.json

# stable id (recommended for always-on box):
python3 spectre-inverse-snowflake.py --config pairing.json --persistent-id peer-lab-1
```

Default without `--persistent-id`: ephemeral id `isf-********` (pool-friendly churn).

## Pairing (do not post publicly)

- Accept: `{accept}`
- Token: `{token}`
- Files: `pairing.json`, `spectre-inverse-snowflake.py`, `spectre-reverse-agent.py`

## Operator side (not you)

Researcher runs accept + Spectre VPN → SOCKS map. See RUNBOOK.md.

## Cover note

Control channel is token + cleartext TCP unless wrapped. Fine for lab / trusted
path; public residential paths should add REALITY/SSH wrap later.

## Stop

Ctrl+C. No leftover system service unless you installed one yourself.
"""


def agent_runbook_markdown(p: ReversePairing, *, scripts_dir: str = "scripts") -> str:
    token = p.token()
    return f"""# Spectre Composition III — reverse rendezvous

## Inverse Snowflake client (recommended package name)

The China-side dial-out process is productized as **Inverse Snowflake**:

```bash
# On foothold M
python3 spectre-inverse-snowflake.py --config pairing.json
# or ./run-inverse-snowflake.sh
```

Same wire protocol as `spectre-reverse-agent.py` (SPECTRE-REV1/2).  
Design notes: population-relay / wild-drop-assistance (anguish research).

## Lab-proven path

Python reverse tunnel (smoke-tested: SOCKS → agent → internet).

```bash
# Outside (accept host — often this machine or origin VPS)
python3 {scripts_dir}/spectre-reverse-accept.py \\
  --token '{token}' \\
  --listen 0.0.0.0:{p.accept_port} \\
  --socks {p.map_socks_host}:{p.map_socks_port}

# Inverse Snowflake client (willing foothold)
python3 {scripts_dir}/spectre-inverse-snowflake.py \\
  --token '{token}' \\
  --accept {p.accept_host}:{p.accept_port}

# Multi-agent: unique --persistent-id per peer (accept round-robins)
# python3 … --persistent-id peer-1
```

Then:

```bash
curl -x socks5h://{p.map_socks_host}:{p.map_socks_port} https://example.com
# Spectre: Connect reverse profile (VPN → SOCKS map)
```

If accept is remote:

```bash
ssh -N -L {p.map_socks_port}:127.0.0.1:{p.map_socks_port} user@accept-host
```

## Pairing

- Token: `{token}`
- Agent dials: `{p.accept_host}:{p.accept_port}`
- SOCKS map: `{p.map_socks_host}:{p.map_socks_port}`

## Optional: Xray VLESS reverse + REALITY (experimental)

`outside-accept.json` / `china-agent.json` are generated for Xray 26.x reverse.
Prefer Python Inverse Snowflake for bring-up.

## Cover / identity

- Production cover on agent→accept should be TLS/REALITY-class when on public internet.
- Python tunnel is cleartext control — use only on trusted underlay or wrap.
- Reverse avoids operator PRC-cloud KYC when foothold is peer/lab/field.

See: docs/CHINA_AGENT.md
"""
