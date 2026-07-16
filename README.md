# Spectre Desktop

<p align="center">
  <img src="data/assets/banner-readme.png" alt="Spectre Desktop" width="980"/>
</p>

GTK 4 + libadwaita frontend for [Spectre](https://github.com/digitizable/spectre): manage **backends** and **path profiles**, then Connect through the local **spectred** core.

Linux only for now. macOS/Windows would be separate frontends against the same core API.

## Status (0.3.7)

Official **Mullvad VPN** integration, **tray applet** (lock icons, right-click Connect / Disconnect / Quit), routing modes, connect preflight, exclude-list split tunnel (clearnet netns), update checks, and core handoff for kill switch + system routing.

**0.3.7:** **Exclude apps** — clearnet netns / `mullvad-exclude` carve-outs under system routing; pair with **spectred ≥ 0.3.10** (`spectre setup-clearnet`). **0.3.6:** Strict hop composition. **0.3.5:** Stealth/REALITY copy.

## What it is

Spectre Desktop is a **thin operator shell**, not the tunnel itself:

| Responsibility | Owner |
|----------------|--------|
| Backend catalog (VPN, REALITY, Tor, Proxy drafts) | Desktop |
| Profiles (ordered hops + which backend each hop uses) | Desktop |
| Readiness (“can we hand this to the core?”) | Desktop |
| Settings / policy (routing mode, kill switch, DNS, …) | Desktop stores; core enforces system routing + kill switch when connected |
| Live path, SOCKS entry, adapter processes | **spectred** core |

That split answers “which VPN do I use?”: define it under **Backends**, bind it on a hop under **Profiles**, then **Connect**.

## Pages

| Page | Role |
|------|------|
| **Home** | Status, path diagram, Connect / Disconnect, local SOCKS when up |
| **Profiles** | Path recipes; hop order; bind each hop to a backend |
| **Backends** | Concrete adapters (fill provider/config/UUID/etc.) |
| **Exclude apps** | Exclude-list split tunnel: run apps on clearnet (netns / marks) |
| **Settings** | Core socket, API token, network/privacy policy, **Mullvad**, **updates**, tray, logging |
| **Tray applet** | Panel lock icon (StatusNotifier) — right-click Connect / Disconnect / Disconnect and quit / Quit; left-click shows the window |

### App routing (exclude-list split tunnel)

**Settings → Routing mode** chooses how traffic uses the path:

| Mode | Behavior |
|------|----------|
| **Entire system** (default) | Core redirects machine TCP/DNS through the path after Connect; optional kill switch |
| **Selected apps only (SOCKS)** | No system redirect; only processes that use local SOCKS use Spectre |

**Exclude apps** is the clearnet carve-out for **Entire system** mode (same idea as Mullvad split tunneling):

1. Preferred: launch via **`clearnet-run`** into the **clearnet network namespace** (`cn-host` veth). Spectre kill switch / system routing already allow `cn-host` and Mullvad exclusion marks.
2. Fallback: **`mullvad-exclude`** (setuid mark-based exclusion). Spectre skips the same marks so apps are not pulled back into the path.

Desktop **never** runs `clearnet-netns teardown` (that kills every PID in the netns).

**One-time install (all users)** — part of the **Spectre core**, not a Desktop-only hack:

```bash
spectre setup-clearnet
# or:  ./scripts/setup-clearnet-privs.sh   # from the spectre repo
```

That installs `/usr/local/libexec/spectre/clearnet-{run,netns}`, sudoers for passwordless exclude, creates the `clearnet` netns, and enables `spectre-clearnet-netns.service` on boot. Desktop `./install.sh` prompts for this when the helper is missing.

Installed desktop applications are detected automatically. **Exclude (clearnet)** launches the selected app through the helper above (proxy env stripped).

### Mullvad and exclude

- **Mullvad app Connected** ⇒ whole system is on Mullvad unless you exclude apps out of the tunnel.
- Spectre system routing / KS **do not** re-capture Mullvad exclusions (`ct mark 0x00000f41` / `meta mark 0x6d6f6c65`) or clearnet-netns traffic on `cn-host`.
- Spectre **apps-only** mode only skips Spectre’s own system redirect; it does not undo Mullvad’s full tunnel.

### Connect preflight

**Connect** runs live checks before talking to the core (so the UI fails fast with a clear toast instead of a hung/half-applied path):

| Hop / policy | Blocked when |
|--------------|----------------|
| **Mullvad SOCKS** (`10.64.0.1`) | Mullvad CLI reports Disconnected, or tunnel SOCKS is closed |
| **Tor** | SOCKS host:port not accepting connections |
| **Proxy** | Host:port unreachable |
| **WireGuard VPN** | `.conf` missing/unreadable, or `wg-quick` not installed |
| **REALITY** | Incomplete fields, `xray` missing, or server name won’t resolve |
| **System routing** | `spectre-nft` helper not set up (`spectre setup-killswitch`) |

**Mullvad → local Tor:** the core does **not** SOCKS-nest `10.64.0.1` into `127.0.0.1:9050` (that dials loopback on the remote side and fails). Mullvad stays the full-tunnel underlay; Spectre dials system Tor. Traffic is still Host → Mullvad → Tor. Requires **spectred ≥ 0.3.4** for underlay routing, **≥ 0.3.6** so local SOCKS does not abort slow Tor circuit builds, and **≥ 0.3.7** so system-routing DNS uses Tor SOCKS RESOLVE (public DNS over TCP `:53` through Tor is blocked by most exits — browsers could not resolve names).

**Hop composition is strict (desktop + spectred ≥ 0.3.9):** invalid nestings are blocked (e.g. REALITY → Mullvad app SOCKS). Mullvad app SOCKS may only be **first** (optional underlay before Tor/REALITY). REALITY and local Tor must be **last**. Self-check (personal Hetzner node): run `~/Downloads/check-hetzner-reality.sh` (or `SOCKS=127.0.0.1:PORT` through Spectre).

Structural checks (backend bound, enabled, complete) always run; list “ready” tags stay structural-only so scrolling Profiles stays snappy.

CLI:

```bash
spectre run -- curl https://am.i.mullvad.net/json
```

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

See the core README and [API docs](https://github.com/digitizable/spectre/blob/main/docs/API.md) for hop behavior. Apps use the local SOCKS entry; with **kill switch** on, spectred installs nftables rules so clearnet cannot bypass the path (needs `nft` privileges once — `spectre` repo `scripts/setup-killswitch-privs.sh`).

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
| `~/.local/share/spectre-desktop/apps.json` | Custom apps + mode/hide overrides for discovered apps |
| `~/.config/spectre-desktop/config.json` | Settings + last active profile + update-check prefs |

### Updates

Settings → **Updates** (on by default) polls [GitHub Releases](https://github.com/digitizable/spectre-desktop/releases) on a schedule (default every 24 hours). There is no auto-install — you get a dialog with a link to the release page. Manual check: menu **Check for updates…** or Settings → **Check**.
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
