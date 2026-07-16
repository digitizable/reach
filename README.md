# Spectre Desktop

Desktop UI for [Spectre](https://github.com/digitizable/spectre): profiles, backends, connect/disconnect, and path status over the local core.

Linux only for now (GTK 4 + libadwaita).

## Shape

| Piece | Role |
|-------|------|
| **spectre-desktop** | GTK shell — profiles, backends, settings, Connect |
| **spectred** (sibling repo) | Headless core — brings the path up, local SOCKS |

The desktop owns configuration (CRUD, readiness). The core owns the live tunnel.

## Pages

| Page | Role |
|------|------|
| Home | Status, path map, Connect / Disconnect |
| Profiles | Path recipes + hop ↔ backend binding |
| Backends | Adapter catalog (VPN, REALITY, Tor, Proxy) |
| Settings | Core socket, policy hints, logging |

## Core wiring

Default socket: `$XDG_RUNTIME_DIR/spectre/spectre.sock`  
(override in Settings or `SPECTRE_SOCKET`)

On Connect, the desktop:

1. Validates the active profile (every hop bound + complete)
2. Starts `spectred` if needed (`spectre start` / neighbour `programs/spectre/bin`)
3. `POST /v1/connect` with the full payload (hops + backends + policy)
4. Shows core status and the local SOCKS address

Install the core first:

```bash
cd ../spectre && ./install.sh    # → ~/.local/bin/spectred
spectre start
```

## Data

| Path | Contents |
|------|----------|
| `~/.local/share/spectre-desktop/backends.json` | Backend catalog |
| `~/.local/share/spectre-desktop/profiles.json` | Profiles + hop bindings |
| `~/.config/spectre-desktop/config.json` | Settings + last profile |
| `~/.local/share/spectre-desktop/desktop.log` | Optional desktop log |

## Run (Linux)

```bash
./install.sh
spectre-desktop
```

Or without install:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
python src/main.py
```

## License

[GNU GPLv3](LICENSE)
