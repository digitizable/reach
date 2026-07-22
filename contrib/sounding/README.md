> **Merged into Reach** as `contrib/sounding/` (measurement lab; not a standalone product).

# Sounding

> **Experimental.**

**Sounding** measures residual paths and public faces: open HTTP CONNECT exits, invite handshakes, and Mirage cover probes. It is **not** a tunnel client and **not** a reverse agent.

| Project | Role |
|---------|------|
| [Drift](https://github.com/digitizable/drift) | Dial-out reverse agent |
| [Mirage](https://github.com/digitizable/mirage) | Probe-resistant reverse cover |
| **Sounding** (this repo) | Measurement lab |

See [ECOSYSTEM.md](./ECOSYSTEM.md).

## Tools

| Script | Purpose |
|--------|---------|
| `harvest-cn-connect.py` | Harvest HTTP CONNECT peers; score CN-capable winners |
| `cn-connect-proxy.py` | Local HTTP proxy → upstream CONNECT pool |
| `measure-cn-exit.py` | Exit geo + HTTPS matrix via proxy URL |
| `probe-invite-measure.py` | Invite listener + self/external YES classify |
| `gfw-prr-probe.py` | Naked vs Mirage cover face comparison |

## Example

```bash
python3 scripts/harvest-cn-connect.py
python3 scripts/cn-connect-proxy.py --upstream-file scripts/cn-connect-live.json --listen 127.0.0.1:18080
python3 scripts/measure-cn-exit.py --proxy http://127.0.0.1:18080

python3 scripts/gfw-prr-probe.py --host ORIGIN --naked-port 18443 --cover-port 18444
```

## License

GPL-3.0-or-later — see [LICENSE](./LICENSE).

## Scope

Operator-controlled vantage points and research measurement. Residual open proxies are untrusted (MITM risk). Do not present residual paths as a privacy product default without labeling risk.
