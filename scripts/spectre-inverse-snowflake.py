#!/usr/bin/env python3
"""
Spectre Inverse Snowflake client — Composition III dial-out.

Snowflake (Tor): volunteers in open nets help censored users leave.
Inverse: a host under (or useful toward) CN routing dials *out* to your
outside accept and maps SOCKS for the researcher. Same wire protocol as
spectre-reverse-agent; defaults and packaging match population-relay design.

  python3 spectre-inverse-snowflake.py --token SECRET --accept HOST:18443
  python3 spectre-inverse-snowflake.py --config pairing.json

Requires spectre-reverse-agent.py in the same directory.
Pairs with spectre-reverse-accept.py (multi-agent pool).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import secrets
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_session():
    agent_path = _SCRIPT_DIR / "spectre-reverse-agent.py"
    if not agent_path.is_file():
        raise SystemExit(
            f"missing {agent_path.name} next to this script — re-export from Spectre"
        )
    spec = importlib.util.spec_from_file_location("spectre_reverse_agent", agent_path)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load spectre-reverse-agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.session


def _ephemeral_id() -> str:
    return f"isf-{secrets.token_hex(4)}"


def _load_config(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("config must be a JSON object")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Spectre Inverse Snowflake client (dial-out reverse agent)"
    )
    ap.add_argument("--token", default="", help="pairing token (or config)")
    ap.add_argument("--accept", default="", help="outside accept host:port")
    ap.add_argument(
        "--agent-id",
        default="",
        help="pool id (default: ephemeral isf-********)",
    )
    ap.add_argument(
        "--config",
        default="",
        help="pairing.json from Spectre Export",
    )
    ap.add_argument(
        "--persistent-id",
        default="",
        help="stable id instead of ephemeral (e.g. peer-lab-1)",
    )
    ap.add_argument(
        "--data-host",
        default="",
        help="DATA dial-back host (public origin when using REALITY wrap on loopback)",
    )
    args = ap.parse_args()

    token = (args.token or "").strip()
    accept = (args.accept or "").strip()
    agent_id = (args.persistent_id or args.agent_id or "").strip()
    data_host = (args.data_host or "").strip()

    if args.config:
        cfg = _load_config(args.config)
        token = token or str(cfg.get("token") or cfg.get("pairing_token") or "").strip()
        accept = accept or str(cfg.get("accept") or "").strip()
        if not agent_id:
            agent_id = str(cfg.get("agent_id") or cfg.get("agentId") or "").strip()
        if not data_host:
            data_host = str(cfg.get("data_host") or cfg.get("dataHost") or "").strip()

    if not token or not accept:
        ap.error("need --token and --accept (or --config pairing.json)")

    if not agent_id:
        agent_id = _ephemeral_id()

    print(
        "Spectre Inverse Snowflake client\n"
        f"  accept={accept}\n"
        f"  agent_id={agent_id}\n"
        f"  data_host={data_host or '(same as accept host)'}\n"
        "  role=dial-out reverse (maps SOCKS on accept)\n"
        "  reconnect=on failure (pool churn tolerance)\n",
        flush=True,
    )
    session = _load_session()
    try:
        session(accept, token, agent_id, data_host=data_host or None)
    except KeyboardInterrupt:
        print("stopped", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
