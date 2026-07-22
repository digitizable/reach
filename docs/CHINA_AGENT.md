# China agent — Inverse Snowflake (Composition III)

**Spectre role:** VPN underlay → SOCKS map.  
**Inverse Snowflake client role:** process on a willing host that **dials out** to outside accept.

## Name

| Name | Meaning |
|------|---------|
| **Tor Snowflake** | Volunteers help censored users *leave* |
| **Spectre Inverse Snowflake** | Volunteer dial-out under useful routing *maps SOCKS for the researcher* |

Same wire protocol as `spectre-reverse-agent.py` (`SPECTRE-REV1` / `SPECTRE-REV2`). Product packaging + ephemeral ids match population-relay design.

## Lab-proven bring-up

```bash
# Outside accept (SOCKS map for Spectre)
python3 scripts/spectre-reverse-accept.py \
  --token 'YOUR_TOKEN' \
  --listen 0.0.0.0:18443 \
  --socks 127.0.0.1:10808 \
  --data-port-min 18500 --data-port-max 18599

# Inverse Snowflake client (foothold)
python3 scripts/spectre-inverse-snowflake.py \
  --token 'YOUR_TOKEN' \
  --accept YOUR_OUTSIDE_HOST:18443

# Stable multi-agent peer
python3 scripts/spectre-inverse-snowflake.py \
  --token 'YOUR_TOKEN' \
  --accept YOUR_OUTSIDE_HOST:18443 \
  --persistent-id peer-lab-1

curl -x socks5h://127.0.0.1:10808 https://example.com
```

### UI Export

**Reach China → Reverse / Inverse Snowflake → Export Inverse Snowflake package**

Writes under `~/.local/share/reach/reverse/`:

- `pairing.json` · `TOKEN` · `spectre-inverse-snowflake.py` · `spectre-reverse-agent.py`
- `run-inverse-snowflake.sh` · `INVERSE_SNOWFLAKE.md` · `RUNBOOK.md`
- optional Xray JSON · `run-accept.sh`

```bash
cd ~/.local/share/reach/reverse
./run-accept.sh              # outside
./run-inverse-snowflake.sh   # on M
```

## Cover note — GFW PRR (Probe-Resistant Reverse)

Optional REALITY face in front of reverse accept (see **Mirage** /
`scripts/gfw-prr-gen.py`). Operator-generated Xray client/server JSON and
meta stay **out of git** (use a private deploy dir or `*.local.json`).

| Port | Role |
|------|------|
| cover (e.g. `18444`) | Public REALITY face (no SPECTRE banner to random probes) |
| accept (e.g. `18443`) | Naked SPECTRE accept (lab; prefer loopback-only later) |
| DATA range | DATA dial-back ports on origin |

Probe: `python3 scripts/gfw-prr-probe.py --host ORIGIN`.

Lab without cover: `./run-inverse-snowflake.sh` straight to the accept port.

## Related

- `docs/CHINA_INGRESS.md`
- Research: population-relay, wild-drop-assistance (anguish notes)
- Study: https://anguish.sh/studies/reaching-into-china-from-outside
