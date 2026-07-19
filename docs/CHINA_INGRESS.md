# Reach China — implementation plan (Spectre)

**Status:** v1 complete (2026-07-19)  
**Study:** https://anguish.sh/studies/reaching-into-china-from-outside  
**Desktop:** Composition **I** + **III**; empty-state doors; VPN underlay required  
**Agent:** `docs/CHINA_AGENT.md` + `~/.local/share/reach/reverse/`  
**Smoke:** `scripts/test_reverse_smoke.sh`  
**Core:** readiness enforces `ingress_cn` / `ingress_cn_reverse`

This document turns the study’s architecture into **what we build** in Reach + spectred. It does not re-litigate GFW papers.

---

## 1. Product intent

**Reach China** is an **ingress** path: vantage **outside** mainland China, goal is a usable channel **toward a China-side endpoint the user operates** (or a peer device they control)—not egress “user in CN browsing the world,” and not a human broker in China.

User mental model:

> I am outside. I control (or have agreed access to) a host/process in CN. Spectre brings up a path so my machine (or selected apps) can use that channel safely under known constraints.

Spectre does **not** provision China VPS, IEPL, or airports. User brings the endpoint.

### Landing problem (research 2026-07-19)

Public free “China REALITY” nodes are not a solution. A general non-mainland VPS is outside CN (egress / outside-accept role, not a China landing). See anguish notes `research/landing-paths` and `research/identity-separated-ingress`.

| Path | What it is | Spectre mode |
|------|------------|--------------|
| **A (individual)** | **Americans/foreigners can often lease mainland VPS** via Aliyun/Tencent **international** + **passport real-name**, or resellers that KYC foreigners. Not anonymous; not always smooth. | Composition I: VPN → REALITY to that host |
| **A (enterprise)** | AWS China / partner-held account needs **Chinese legal entity** | Same |
| **B** | Peer / hardware you control in mainland | Same as A |
| **C** | **Reverse:** agent on CN side dials **out** to operator outside-accept host | Composition III when inbound/KYC fails |
| **D** | Refuse KYC and no peer | No true mainland landing |

Hong Kong VPS ≠ mainland landing (open net in HK; HK–mainland still crosses border controls).

**ICP:** required for public websites on mainland hosts; private endpoint still under cloud AUP / real-name law.

---

## 2. Locked v1 choices (from study checklist)

| Checklist item | v1 decision |
|----------------|-------------|
| Topology | **Composition I** — outside → **VPN underlay (required)** → TLS hop → China-side host |
| Public segments | VPN path first; China hop only **inside** the VPN underlay |
| Outer shape | **TLS-shaped cover** on the China hop (REALITY); no pure high-entropy |
| VPN rule | **Hard requirement** — never dial a China endpoint from clearnet |
| Name plane | User supplies endpoint as host/IP; desktop must not claim “unblocked for users inside CN” from outside dial success |
| Privilege | **None** in v1 — no IEPL / private haul product |
| Churn | User-managed (edit backend when IP/SNI burns); UI copy says so |
| Proof | Optional reachability probe from **this** machine only; labeled as outside vantage |
| Trust | User trusts the China-side host operator (often themselves) and the cover stack |

**Explicitly not v1**

- Topology 2 multi-hop middle machines as a first-class wizard  
- Topology 3 reverse dial-out (China host initiates) — **v1.1 candidate**  
- Pure Shadowsocks/AEAD-looking outers as “China mode” defaults  
- Claiming permanent connectivity or airport-class performance  
- Human-in-China broker workflows  

**Two open doors (both first-class)**

Research: anguish notes `landing-paths`, `identity-separated-ingress`.

| Door | Who | Spectre mode |
|------|-----|----------------|
| **Inbound** | Researchers/operators who **already have** (or obtain) a China-side host | Composition I: VPN → REALITY/Proxy to that host |
| **Reverse** | Operators who will not bind **their** identity to PRC cloud KYC | Composition III: china-agent dials out → outside accept |

- Do **not** demote inbound to a footnote. Labs and field researchers with a foothold are a primary audience.
- Reverse is the path when the barrier is operator PRC-cloud identity—not a moral ranking of landings.
- Empty state: **“Already have a China-side host?”** → inbound; else reverse. Package/document **china-agent** for reverse.
- Stance: **American project.** PRC censorship is the adversary environment we design against—not our definition of legitimate research. We do not ship identity-fraud tools or untrusted free-node marketplaces; we do not adopt Beijing’s “legal VPN” rhetoric as product ethics.

**Product boundaries (technical)**

- No free-node marketplace as infrastructure  
- No labeling non-mainland/HK hosts as China landings  
- No promising IEPL we do not provide

---

## 3. Map onto existing Spectre concepts

Today:

- **Backend** = concrete adapter instance (REALITY, Tor, VPN, Proxy)  
- **Profile** = ordered hops of backends + policy  
- **Connect** = desktop builds payload → `POST /v1/connect` → spectred runs adapters, optional system routing + kill switch  

Reach China v1 should **reuse** that model, not invent a parallel stack.

### 3.1 Required v1 path shape

```
Host (outside)
  → hop[0]: VPN underlay (required)
       WireGuard/VPN backend  OR  Mullvad app SOCKS (full-tunnel underlay)
  → hop[1]: TLS-camouflage to China-side host
       REALITY (preferred) or Proxy (user-terminated cover)
  → local SOCKS / system routing (existing policy)
```

**Hard rule:** Connect is blocked unless hop 0 is a VPN underlay. Never single-hop clearnet → China.

**Preferred China hop:** **REALITY** (TLS-shaped).  
**VPN options:** Spectre-owned WireGuard `.conf`, or Mullvad app (auto-connect already exists for Mullvad SOCKS hops).

### 3.2 Desktop surface

| Surface | Role |
|---------|------|
| **Reach China page** | Intent, topology explanation, guided backend/profile creation, readiness, link to study; Connect when ready |
| **Backends** | Store China-side REALITY (or Proxy) instance — same store as today |
| **Profiles** | One-hop (or documented multi-hop) profile tagged or named for ingress |
| **Home / Connect** | Same connect pipeline once profile is selected |

Do not keep Connect forever trapped only on Reach China if the profile is a normal profile—but Reach China is the **onboarding and honesty** surface (constraints, vantage warnings).

---

## 4. Work packages

### WP0 — Study & copy alignment (done when study is `active`)

- [x] Study architecture published  
- [ ] Update Reach China page copy: research dependency satisfied; implementation follows this doc  
- [ ] README: Reach China status = “design complete, implementation phased” (not “blocked on study order”)

### WP1 — Desktop: guided profile — **UI done (2026-07-19)**

Goal: full Reach China surface before wiring more core.

**Shipped in `src/pages/china_ingress.py`:**

1. Topology selector (I active; II/III full UX shells for v1.1)  
2. Path sketch chips  
3. Composition I: REALITY form + vless import, or Proxy form  
4. Live readiness checklist (SNI required for ingress)  
5. Save backend + profile (`path_intent=ingress_cn`)  
6. TCP probe (outside vantage)  
7. Connect / Disconnect via existing `connect_active` / `disconnect`  
8. Saved ingress profile list + Use  
9. Study / Backends / Profiles links  

Follow-up polish: tighter layout on 400px, load reverse/multi-hop when WP4 starts.

### WP2 — Core: treat ingress hop as first-class path metadata (minimal)

Goal: spectred/desktop agree this profile is **ingress-shaped** for status UX and policy defaults—not a new tunnel engine if REALITY already works.

Tasks:

1. Optional profile/connect flag: `path_intent: "ingress_cn" | "egress" | "generic"` (or desktop-only tag first)  
2. Status strings: “Reach China (outside vantage)” vs generic path summary  
3. Preflight (desktop or core): TCP/TLS dial to landing from local host; surface failure codes  
4. Document in `spectre/docs/API.md` if any payload field is added  

If REALITY→China already works end-to-end via normal profiles, WP2 is **labeling + preflight + UX**, not a rewrite of `internal/path`.

### WP3 — Core: harden cover defaults for ingress

Goal: refuse or warn configurations that violate Layer B for an ingress_cn profile.

Tasks:

1. When `path_intent == ingress_cn` (or Reach China profile):  
   - reject pure high-entropy-only hop kinds if we ever add them as defaults  
   - require TLS-related fields for REALITY (SNI present, port 443 recommended not forced)  
2. Desktop readiness mirrors the same rules  
3. Tests: unit tests on validation helpers  

### WP4 — Topology 3: reverse bring-up — **desktop wired (2026-07-19)**

Goal: China-side process dials **out** to outside accept; Spectre uses VPN → SOCKS map.

**Shipped:**

1. [x] Desktop Composition III form (accept, map SOCKS, pairing, optional REALITY fields)  
2. [x] Export: Python accept/agent scripts + `run-*.sh` + RUNBOOK + optional Xray JSON  
3. [x] Save profile `path_intent=ingress_cn_reverse` → hops VPN + Proxy(map)  
4. [x] Readiness rules for reverse  
5. [x] Connect via existing pipeline once map is up  
6. [x] Lab smoke `scripts/test_reverse_smoke.sh` (HTTP 200 through reverse SOCKS)  
7. [x] `docs/CHINA_AGENT.md`  

**Still later:**

- spectred native / REALITY-wrapped reverse (Xray reverse flaky on 26.x)  
- Auto-start accept from desktop  
- Multi-hop Composition II wizard

### WP5 — Observability & honesty

1. Never toast “China unblocked” on success—only “Path up (outside vantage)”  
2. Link study from Reach China and from first-run tip  
3. Log path_intent and hop kinds (no secrets) for debug  

---

## 5. Suggested implementation order

```
WP0 copy          ──► WP1 desktop guided config
                          │
                          ▼
                    WP2 connect + preflight (reuse REALITY)
                          │
                          ▼
                    WP3 validation for ingress intent
                          │
                          ▼
                    WP4 reverse topology (later)
```

**First vertical slice that feels real:** WP1 + WP2 with a user-owned China VPS running REALITY/VLESS already, outside Spectre Connect → local SOCKS works.

---

## 6. API / data sketch (v1)

### Backend (existing REALITY fields)

No new kind required for v1 if REALITY is the cover:

- `reality_server` = China-side host  
- `reality_port`, `reality_uuid`, `reality_public_key`, `reality_short_id`, `reality_sni`, …  

Optional later:

```text
backend.region_hint = "cn"          # display only
backend.notes = "operator-owned"
```

### Profile

```text
profile.name = "Reach China"
profile.hops = [ { backend_id: <reality-cn> } ]
profile.path_intent = "ingress_cn"   # new optional field (desktop JSON first)
```

### Connect payload

Extend only if core must know intent:

```json
{
  "profile_id": "…",
  "path_intent": "ingress_cn",
  "hops": [ /* existing */ ],
  "policy": { /* existing routing_mode, kill_switch, … */ }
}
```

If core ignores unknown fields today, desktop can ship `path_intent` in local profile store only until WP2.

---

## 7. Readiness rules (desktop)

For a Reach China profile to enable Connect:

| Check | Error if fail |
|-------|----------------|
| Exactly **two** hops in v1 | VPN underlay → China endpoint |
| Hop 0 is VPN underlay | WireGuard/VPN or Mullvad app SOCKS |
| Hop 1 kind ∈ { REALITY, Proxy } | TLS-shaped REALITY or covered Proxy |
| REALITY: server, uuid, pubkey, **sni** non-empty | Field-level / readiness errors |
| Proxy: host/port non-empty | Field-level errors |
| Both backends enabled + complete | Incomplete/disabled blocks Connect |
| Core healthy | Existing core checks |
| Optional: TCP probe of China host | Outside vantage only |

Warnings (non-blocking):

- Success is outside vantage only  
- User must operate/churn the China-side host  
- No path privilege  

---

## 8. Testing plan

| Level | What |
|-------|------|
| Unit | Readiness validation; profile_intent helpers |
| Integration (lab) | Outside VM → CN lab VPS with REALITY; Connect; curl via local SOCKS to a service only on CN host |
| Negative | Wrong SNI; entropy-only misconfig rejected; core down |
| UX | Copy review: no “unblock China” claims |

No requirement to probe from inside CN for v1 CI.

---

## 9. Security & scope notes

- Secrets stay in existing desktop backend store until core owns secret storage (same as other backends).  
- Kill switch + system routing behave as for any profile; document that CN landing sees traffic that exits the tunnel as whatever the landing routes—user responsibility.  
- Whonix: follow existing core rules (VPN restrictions, etc.); ingress REALITY hop may need explicit allow like other non-Tor first hops—check WHONIX.md when implementing.  

---

## 10. Success criteria

**Study:** architecture checklist answerable; status active on anguish.sh.  

**Spectre v1:**  

1. User creates China-side REALITY backend + Reach China profile from the page  
2. Connect brings up path via spectred  
3. Local SOCKS (or system routing) reaches a service via that path  
4. UI states outside vantage and operator-owned endpoint  
5. No pure-entropy default  

**Reverse (Composition III):** agent export + reverse profile + Connect via SOCKS map.

---

## 11. References

- Study: https://anguish.sh/studies/reaching-into-china-from-outside  
- Core API: `spectre/docs/API.md`  
- Desktop backends: `src/core/backends.py`  
- Preview UI: `src/pages/china_ingress.py`  
