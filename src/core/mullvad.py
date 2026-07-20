"""Official Mullvad VPN Linux CLI integration for Reach.

The Mullvad desktop client is open source (GPL-3.0:
https://github.com/mullvad/mullvadvpn-app). Reach drives the installed
``mullvad`` CLI for status, connect/disconnect, and relay selection — it does
not reimplement the tunnel.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache

DEFAULT_SOCKS_HOST = "10.64.0.1"
DEFAULT_SOCKS_PORT = 1080
RELAYS_API = "https://api.mullvad.net/app/v1/relays"


@dataclass
class MullvadStatus:
    available: bool = False
    connected: bool = False
    relay: str = ""
    location: str = ""
    version: str = ""
    socks_host: str = DEFAULT_SOCKS_HOST
    socks_port: int = DEFAULT_SOCKS_PORT
    socks_reachable: bool = False
    summary: str = "Mullvad unknown"
    error: str = ""
    # Active constraint from `mullvad relay get`
    constraint_country: str = ""
    constraint_city: str = ""
    constraint_hostname: str = ""

    @property
    def ready_for_socks_hop(self) -> bool:
        return self.connected and self.socks_reachable


@dataclass(frozen=True)
class RelayCity:
    country_code: str
    country_name: str
    city_code: str
    city_name: str
    latitude: float
    longitude: float


@dataclass
class RelayCatalog:
    countries: list[tuple[str, str]] = field(default_factory=list)  # code, name
    cities: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    hosts: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    map_cities: list[RelayCity] = field(default_factory=list)


def cli_path() -> str | None:
    return shutil.which("mullvad")


def _tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(*args: str, timeout: float = 8.0) -> tuple[int, str]:
    exe = cli_path()
    if not exe:
        return 127, "mullvad not found"
    try:
        proc = subprocess.run(  # noqa: S603
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


def probe() -> MullvadStatus:
    st = MullvadStatus()
    if not cli_path():
        st.summary = "Mullvad CLI not installed"
        st.error = "mullvad not found in PATH"
        return st
    st.available = True
    code, out = _run("status", timeout=3.0)
    if code != 0 and not out:
        st.summary = "Mullvad status failed"
        st.error = "status failed"
        st.socks_reachable = _tcp_open(st.socks_host, st.socks_port)
        return st
    lines = out.splitlines()
    head = (lines[0] if lines else "").strip().lower()
    if head.startswith("connected"):
        st.connected = True
    elif head.startswith("disconnected"):
        st.connected = False
    for line in lines:
        line = line.strip()
        if line.startswith("Relay:"):
            st.relay = line.split(":", 1)[1].strip()
        if line.startswith("Visible location:"):
            st.location = line.split(":", 1)[1].strip()
    st.socks_reachable = _tcp_open(st.socks_host, st.socks_port)
    if st.connected and st.socks_reachable:
        st.summary = "Mullvad Connected"
        if st.relay:
            st.summary += f" · {st.relay}"
    elif st.connected:
        st.summary = "Mullvad Connected (SOCKS not ready)"
    else:
        st.summary = "Mullvad Disconnected"
    code_v, ver = _run("version", timeout=2.0)
    if code_v == 0 and ver:
        line = ver.splitlines()[0].strip()
        if ":" in line:
            st.version = line.rsplit(":", 1)[-1].strip()
        else:
            st.version = line
    # Constraints
    c_country, c_city, c_host = get_location_constraint()
    st.constraint_country = c_country
    st.constraint_city = c_city
    st.constraint_hostname = c_host
    return st


def get_location_constraint() -> tuple[str, str, str]:
    """Return (country, city, hostname) codes from `mullvad relay get`."""
    code, out = _run("relay", "get", timeout=4.0)
    if code != 0 or not out:
        return "", "", ""
    country = city = host = ""
    for line in out.splitlines():
        # Lines look like: "    Location:               country us"
        if "location:" not in line.lower():
            continue
        rest = line.split(":", 1)[1].strip().lower()
        parts = rest.split()
        if not parts:
            continue
        if parts[0] == "any":
            return "any", "", ""
        if parts[0] == "country" and len(parts) >= 2:
            country = parts[1]
        elif parts[0] == "city" and len(parts) >= 3:
            country, city = parts[1], parts[2]
        elif parts[0] in ("hostname", "host") and len(parts) >= 2:
            host = parts[1]
            bits = host.split("-")
            if len(bits) >= 2:
                country, city = bits[0], bits[1]
        elif len(parts[0]) == 2 and parts[0].isalpha():
            country = parts[0]
            if len(parts) >= 2 and len(parts[1]) == 3:
                city = parts[1]
    return country, city, host


def set_location(
    country: str,
    city: str | None = None,
    hostname: str | None = None,
    *,
    disconnect_if_connected: bool = True,
) -> tuple[bool, str]:
    """Set Mullvad relay constraints only — does not connect.

    Changing location while Mullvad is already connected makes the daemon
    reconnect to the new relay. By default we disconnect afterward so
    selection never auto-connects; the user must press Connect.
    """
    if not cli_path():
        return False, "Mullvad CLI not installed"
    country = (country or "any").strip().lower()
    city = (city or "").strip().lower() or None
    hostname = (hostname or "").strip().lower() or None

    # Snapshot tunnel state before the change (location change can reconnect).
    was_connected = False
    try:
        was_connected = probe().connected
    except Exception:
        pass

    args: list[str] = ["relay", "set", "location"]
    if hostname and hostname not in ("any", ""):
        args.append(hostname)
    elif country in ("", "any"):
        args.append("any")
    elif city and city not in ("any", ""):
        args.extend([country, city])
    else:
        args.append(country)
    code, out = _run(*args, timeout=12.0)
    if code != 0:
        return False, out or "mullvad relay set location failed"

    where = " ".join(args[3:])
    # Always leave the tunnel down after a pure location pick unless asked not to.
    if disconnect_if_connected:
        try:
            st = probe()
            if was_connected or st.connected:
                disconnect()
        except Exception:
            pass
        return True, f"Relay selected · {where} · press Connect when ready"

    return True, out or f"Relay location → {where}"


def connect() -> tuple[bool, str]:
    if not cli_path():
        return False, "Mullvad CLI not installed"
    code, out = _run("connect", timeout=15.0)
    if code != 0:
        return False, out or "mullvad connect failed"
    return True, "Mullvad connect requested"


def disconnect() -> tuple[bool, str]:
    if not cli_path():
        return False, "Mullvad CLI not installed"
    code, out = _run("disconnect", timeout=15.0)
    if code != 0:
        return False, out or "mullvad disconnect failed"
    return True, "Mullvad disconnect requested"


def ensure_connected(*, timeout_sec: float = 45.0) -> MullvadStatus:
    st = probe()
    if st.ready_for_socks_hop:
        return st
    if not st.available:
        return st

    if st.connected:
        wait = min(float(timeout_sec), 8.0)
        deadline = time.time() + wait
        while time.time() < deadline:
            st = probe()
            if st.ready_for_socks_hop:
                return st
            time.sleep(0.35)
        st = probe()
        if not st.ready_for_socks_hop:
            st.error = (
                f"Mullvad is Connected but SOCKS {st.socks_host}:{st.socks_port} "
                "is not accepting connections"
            )
            st.summary = st.error
        return st

    ok, msg = connect()
    if not ok:
        st = probe()
        st.error = msg
        st.summary = msg
        return st
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        st = probe()
        if st.ready_for_socks_hop:
            return st
        time.sleep(0.4)
    st = probe()
    if not st.error:
        if st.connected and not st.socks_reachable:
            st.error = (
                f"Mullvad connected but SOCKS {st.socks_host}:{st.socks_port} "
                "never became ready"
            )
        else:
            st.error = "Mullvad did not become ready in time"
        st.summary = st.error
    return st


def _parse_relay_list(text: str) -> RelayCatalog:
    cat = RelayCatalog()
    cur_country = ""
    cur_city = ""
    for line in text.splitlines():
        raw = line.rstrip()
        if not raw.strip():
            continue
        # Country: "United States (us)" at column 0
        m = re.match(r"^(.+?)\s+\(([a-z]{2})\)\s*$", raw)
        if m and " @ " not in raw and not re.match(r"^[a-z]{2}-", m.group(1).strip()):
            name, code = m.group(1).strip(), m.group(2)
            # Hostnames look like al-tia-wg-001 — skip
            if re.match(r"^[a-z]{2}-[a-z]{3}-", name):
                pass
            else:
                cat.countries.append((code, name))
                cat.cities.setdefault(code, [])
                cur_country = code
                cur_city = ""
                continue
        # City: "Seattle, WA (sea) @ 47.60°N, ..."
        m2 = re.match(r"^(.+?)\s+\(([a-z]{3})\)\s+@", raw)
        if m2 and cur_country:
            cname, ccode = m2.group(1).strip(), m2.group(2)
            cat.cities.setdefault(cur_country, []).append((ccode, cname))
            cur_city = ccode
            continue
        # Hostname: "us-sea-wg-404 (...)" (often tab-indented)
        stripped = raw.strip()
        m3 = re.match(r"^([a-z0-9-]+)\s+\(", stripped)
        if m3 and cur_country and cur_city:
            host = m3.group(1)
            if host.startswith(f"{cur_country}-{cur_city}-"):
                cat.hosts.setdefault((cur_country, cur_city), []).append(host)
    return cat


@lru_cache(maxsize=1)
def load_catalog() -> RelayCatalog:
    """Countries / cities / hosts from CLI (cached for process lifetime)."""
    if not cli_path():
        return RelayCatalog()
    code, out = _run("relay", "list", timeout=45.0)
    if code != 0 or not out:
        return RelayCatalog()
    cat = _parse_relay_list(out)
    # Map cities from public API (lat/lon) — GPL client; public relay data
    try:
        cat.map_cities = fetch_map_cities()
    except Exception:
        cat.map_cities = []
    return cat


def fetch_map_cities() -> list[RelayCity]:
    """City markers from Mullvad's public app API (for map display)."""
    req = urllib.request.Request(
        RELAYS_API,
        headers={"User-Agent": "Reach/0.4 (Mullvad map; open-source client)"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    locations = data.get("locations") or {}
    out: list[RelayCity] = []
    for key, loc in locations.items():
        if not isinstance(loc, dict):
            continue
        parts = str(key).split("-", 1)
        if len(parts) != 2:
            continue
        cc, city = parts[0].lower(), parts[1].lower()
        try:
            lat = float(loc.get("latitude"))
            lon = float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        out.append(
            RelayCity(
                country_code=cc,
                country_name=str(loc.get("country") or cc.upper()),
                city_code=city,
                city_name=str(loc.get("city") or city),
                latitude=lat,
                longitude=lon,
            )
        )
    return out


def clear_catalog_cache() -> None:
    load_catalog.cache_clear()
