# Spectre Desktop

<p align="center">
  <img src="data/assets/banner.png" alt="Spectre Desktop" width="980"/>
</p>

GTK 4 + libadwaita frontend for [Spectre](https://github.com/digitizable/spectre): manage **backends** and **path profiles**, then Connect through the local **spectred** core.

Linux only for now. macOS/Windows would be separate frontends against the same core API.

## What it is

Spectre Desktop is a **thin operator shell**, not the tunnel itself:

| Responsibility | Owner |
|----------------|--------|
| Backend catalog (VPN, REALITY, Tor, Proxy drafts) | Desktop |
| Profiles (ordered hops + which backend each hop uses) | Desktop |
| Readiness (“can we hand this to the core?”) | Desktop |
| Settings / policy hints (kill switch, DNS, …) | Desktop (stored; core enforces later) |
| Live path, SOCKS entry, adapter processes | **spectred** core |

That split answers “which VPN do I use?”: define it under **Backends**, bind it on a hop under **Profiles**, then **Connect**.

## Pages

| Page | Role |
|------|------|
| **Home** | Status, path diagram, Connect / Disconnect, local SOCKS when up |
| **Profiles** | Path recipes; hop order; bind each hop to a backend |
| **Backends** | Concrete adapters (fill provider/config/UUID/etc.) |
| **Settings** | Core socket, API token, network/privacy policy hints, logging |

Defaults seed example backends (draft VPN, draft REALITY, **System Tor**) and sample profiles. **Tor only** is ready out of the box if Tor is listening on `9050`.

## How Connect works

1. Desktop checks readiness (every hop has an **enabled, complete** backend).
2. If the core is offline, it tries `systemctl --user start spectred`, then `spectre start`, then a sibling/repo `spectred` binary.
3. `POST /v1/connect` with the full payload: profile, hops, backend objects, policy.
4. UI shows core state; when connected, the detail line includes **SOCKS `host:port`**.

Incomplete backends (e.g. WireGuard without a `.conf`, REALITY without UUID) stay drafts — Connect stays blocked until fixed.

### Completeness rules (desktop)

| Kind | Ready when |
|------|------------|
| **Tor** | System Tor, or custom SOCKS host/port |
| **Proxy** | Host + port |
| **VPN (WireGuard)** | Path to a `.conf` (Browse… in the editor) |
| **REALITY** | Server + public key + **UUID** |

## Core dependency

Install and run the core first (or let the desktop start it):

```bash
cd ../spectre   # or clone digitizable/spectre
./install.sh    # → ~/.local/bin + systemd --user unit
spectre health
```

| Default | Value |
|---------|--------|
| Socket | `$XDG_RUNTIME_DIR/spectre/spectre.sock` |
| Override | Settings → Socket path, or `SPECTRE_SOCKET` |

See the core README and [API docs](https://github.com/digitizable/spectre/blob/main/docs/API.md) for hop behavior and limitations (local SOCKS only; no system-wide kill switch yet).

## Whonix

Install and run Spectre Desktop on **Whonix-Workstation** (not the Gateway):

1. Gateway / `sys-whonix` online with Tor connected  
2. Core installed on the Workstation (`spectre start`)  
3. Desktop seed backend becomes **Whonix Gateway Tor** (Gateway SOCKS)  
4. Use the **Tor only** profile (or any path that binds that Tor backend)

VPN hops are blocked by the core on Workstation unless `SPECTRE_ALLOW_VPN_ON_WHONIX=1` (not recommended). Home shows a Whonix hint when the core reports `environment.whonix`.

More: [Whonix notes in spectre](https://github.com/digitizable/spectre/blob/main/docs/WHONIX.md).

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
spectre-desktop
```

Dev without install:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
python src/main.py
```

`./install.sh --check` only verifies GTK/Adw bindings.

## Data

| Path | Contents |
|------|----------|
| `~/.local/share/spectre-desktop/backends.json` | Backend catalog |
| `~/.local/share/spectre-desktop/profiles.json` | Profiles + hop bindings |
| `~/.config/spectre-desktop/config.json` | Settings + last active profile |
| `~/.local/share/spectre-desktop/desktop.log` | Optional desktop log |

Uninstall: `./uninstall.sh`.

## Project layout

```
src/
  main.py application.py window.py   # app shell
  services.py                        # config + stores + connect_active
  core/                              # backends, profiles, readiness, client
  pages/                             # home, profiles, backends, settings
  widgets/                           # editors, path graph, chrome
data/assets/                         # brand + Tor/REALITY marks
```

## Related

- [Project page](https://anguish.sh/projects/spectre-desktop) — anguish.sh  
- [Spectre core](https://github.com/digitizable/spectre) — `spectred` + CLI  

## Donate

Optional support for development:

| | Address |
|--|---------|
| **BTC** | `sp1qqgly4w3je7m64xh72047u04hwyflyqqf3rfmxchmyht5dndpas4txqsuj9e0jc9l3yql9c3k5el3quyxfh6kr6c9zplmaavj59kuk5kny5jf7cjr` |
| **XMR** | `89SWJrVXxEgHNiVNSEdjWsXMtRzpzoUX7ebToit9x7iuQADTZh5BGVjTywoQ4gn3SuSEzDhXpCEybi17HpwgYs7v2Xfjdue` |

## License

[GNU GPLv3](LICENSE)
