# Reach

<p align="center">
  <img src="data/assets/app-icon.png" alt="Reach" width="160"/>
</p>

**Reach** is the GTK 4 + libadwaita **operator shell** for composing privacy paths and territory doors. Today it drives the [Spectre](https://github.com/digitizable/spectre) core (`spectred`). The product vision is broader: one control surface for paths, reverse footholds, cover, and measurement — with Spectre, [Drift](https://github.com/digitizable/drift), [Mirage](https://github.com/digitizable/mirage), and [Sounding](https://github.com/digitizable/sounding) as tools underneath.

Linux only for now.

**Formerly Spectre Desktop** (config under `~/.config/spectre-desktop` and `~/.local/share/spectre-desktop` migrates to `reach` on first run).

## Status (0.4.4)

Desktop-sized shell (default **720×780**, resizable, remembers size). **Paths** and **Adapters** use master–detail; **Doors** is two-pane; **Tools** card grid; **Settings** readable column. Mullvad, tray, preflight, exclude apps, territories.

## What it is

Reach is the **operator shell**, not the tunnel itself:

| Responsibility | Owner |
|----------------|--------|
| Backend catalog (VPN, REALITY, Tor, Proxy) | Reach |
| Profiles (ordered hops + which backend each hop uses) | Reach |
| Readiness (“can we hand this to the core?”) | Reach |
| Settings / policy (routing mode, kill switch, DNS, …) | Reach stores; Spectre enforces when connected |
| Live path, SOCKS entry, adapter processes | **spectred** (Spectre core) |
| Reverse / cover / measure (lab tools) | [Drift](https://github.com/digitizable/drift) · [Mirage](https://github.com/digitizable/mirage) · [Sounding](https://github.com/digitizable/sounding) |

Define backends, bind them on profile hops, then **Connect**.

## Pages

| Page | Role |
|------|------|
| **Home** | Status, path, path picker, **Mullvad server** (country/city/host), Connect |
| **Paths** | Path recipes (profiles); hop order; bind each hop to an adapter |
| **Adapters** | VPN, REALITY, Tor, proxy backends hops can use |
| **Doors** | Territory ingress — inbound host or dial-out (Inverse Snowflake) |
| **Apps** | Exclude-list split tunnel: selected apps on clearnet |
| **Tools** | Drift · Mirage · Sounding (lab companions) |
| **Settings** | Core socket, routing, Mullvad, tray, updates |
| **Tray** | StatusNotifier lock — Connect / Disconnect / Quit |

### App routing (exclude-list split tunnel)

**Settings → Routing mode** chooses how traffic uses the path:

| Mode | Behavior |
|------|----------|
| **Entire system** (default) | Core redirects machine TCP/DNS through the path after Connect; optional kill switch |
| **Selected apps only (SOCKS)** | No system redirect; only processes that use local SOCKS use the path |

**Exclude apps** is the clearnet carve-out for **Entire system** mode:

1. Preferred: launch via **`clearnet-run`** into the **clearnet network namespace** (`cn-host` veth).
2. Fallback: **`mullvad-exclude`** (setuid mark-based exclusion).

**One-time install** — part of the **Spectre core**:

```bash
spectre setup-clearnet
```

### Connect preflight

**Connect** runs live checks before talking to the core (fail fast with a clear toast):

| Hop / policy | Blocked when |
|--------------|----------------|
| **Mullvad SOCKS** (`10.64.0.1`) | Mullvad disconnected, or tunnel SOCKS closed |
| **Tor** | SOCKS host:port not accepting connections |
| **Proxy** | Host:port unreachable |
| **WireGuard VPN** | `.conf` missing/unreadable, or `wg-quick` not installed |
| **REALITY** | Incomplete fields, `xray` missing, or server name won’t resolve |
| **System routing** | `spectre-nft` helper not set up (`spectre setup-killswitch`) |

## Core dependency

Install and run Spectre first (or let Reach start it):

```bash
cd ../spectre   # or clone digitizable/spectre
./install.sh
spectre health
```

| Default | Value |
|---------|--------|
| Socket | `$XDG_RUNTIME_DIR/spectre/spectre.sock` |
| Override | Settings → Socket path, or `SPECTRE_SOCKET` |

## Install & run

Dependencies (Debian/Ubuntu/Mint-style):

```bash
sudo apt install -y \
  python3 python3-venv python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  libadwaita-1-0 libgtk-4-1
```

```bash
./install.sh
reach
```

(`spectre-desktop` remains a compatibility launcher name.)

Dev without install:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
python src/main.py
```

## Data

| Path | Contents |
|------|----------|
| `~/.local/share/reach/backends.json` | Backend catalog |
| `~/.local/share/reach/profiles.json` | Profiles + hop bindings |
| `~/.local/share/reach/apps.json` | Custom apps + mode/hide overrides |
| `~/.config/reach/config.json` | Settings + last active profile + update prefs |
| `~/.local/share/reach/desktop.log` | Optional log |

Legacy `spectre-desktop` directories are renamed to `reach` on first launch when possible.

### Updates

Settings → **Updates** polls [GitHub Releases](https://github.com/digitizable/reach/releases) (default every 24 hours). No auto-install — dialog links to the release page.

Uninstall: `./uninstall.sh`.

## Brand

App icon and in-app mark: Creation of Adam hands (fresco plate + white outline). See [THIRD_PARTY_NOTICES.md](data/THIRD_PARTY_NOTICES.md).

## Related

- [Spectre](https://github.com/digitizable/spectre) — path core (`spectred` + CLI)
- [Drift](https://github.com/digitizable/drift) · [Mirage](https://github.com/digitizable/mirage) · [Sounding](https://github.com/digitizable/sounding)

## Donate

Optional support for development:

| | Address |
|--|---------|
| **BTC** | `sp1qqgly4w3je7m64xh72047u04hwyflyqqf3rfmxchmyht5dndpas4txqsuj9e0jc9l3yql9c3k5el3quyxfh6kr6c9zplmaavj59kuk5kny5jf7cjr` |
| **XMR** | `89SWJrVXxEgHNiVNSEdjWsXMtRzpzoUX7ebToit9x7iuQADTZh5BGVjTywoQ4gn3SuSEzDhXpCEybi17HpwgYs7v2Xfjdue` |

## License

[GNU GPLv3](LICENSE)
