"""Path fingerprint lab measure (O1 / Laminar F2).

Runs cross-layer RTT against the live Spectre SOCKS proxy when available.
Uses the workspace or lab Laminar checkout without requiring a global install.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_config import project_root


@dataclass(frozen=True)
class FingerprintReport:
    ok: bool
    summary: str
    lines: list[str]
    socks: str = ""
    rtt_gap_score: float | None = None
    delta_ms: float | None = None
    hop_count: int = 0
    path_summary: str = ""
    fingerprint_note: str = ""
    error: str = ""


def _laminar_src() -> Path | None:
    """Return directory that contains package ``laminar`` (…/src)."""
    # 1) Sibling workspace checkout (dev)
    try:
        parent = project_root().parent
    except Exception:
        parent = None
    if parent is not None:
        for cand in (parent / "laminar" / "src", parent / "laminar"):
            if (cand / "laminar" / "rtt.py").is_file():
                return cand
            if cand.name == "src" and (cand / "laminar" / "__init__.py").is_file():
                return cand

    # 2) Reach lab install
    try:
        from core.lab_companions import lab_dir

        lab = lab_dir("laminar")
        for cand in (lab / "src", lab):
            if (cand / "laminar" / "rtt.py").is_file() or (
                cand / "laminar" / "__init__.py"
            ).is_file():
                return cand
    except Exception:
        pass

    # 3) PATH launcher → resolve script location
    import shutil

    which = shutil.which("laminar") or shutil.which("laminar-rtt")
    if which:
        p = Path(which).resolve()
        # launcher may be a shell wrapper; try common layouts
        for up in (p.parent, p.parent.parent, p.parent.parent.parent):
            for cand in (up / "src", up):
                if (cand / "laminar" / "rtt.py").is_file():
                    return cand
    return None


def _parse_socks(local_proxy: str) -> tuple[str, int] | None:
    raw = (local_proxy or "").strip()
    if not raw:
        return None
    # socks5://127.0.0.1:10808 or 127.0.0.1:10808
    raw = re.sub(r"^socks5h?://", "", raw, flags=re.I)
    raw = raw.split("/")[0]
    if ":" not in raw:
        return None
    host, port_s = raw.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not host or port <= 0:
        return None
    return host, port


def ensure_laminar_importable() -> str | None:
    """Add Laminar src to sys.path. Returns error message or None."""
    src = _laminar_src()
    if src is None:
        return (
            "Laminar not found.\n"
            "Install from Tools → Lab companions → Laminar, or keep a "
            "sibling checkout at programs/laminar."
        )
    s = str(src)
    if s not in sys.path:
        sys.path.insert(0, s)
    try:
        import laminar.rtt  # noqa: F401
    except ImportError as exc:
        return f"Could not import laminar from {src}: {exc}"
    return None


def _sanitize_core_note(note: str) -> str:
    """Drop self-referential “use Path fingerprint” bits when already measuring."""
    note = (note or "").strip()
    if not note:
        return ""
    # Remove trailing operator UI pointers; keep the technical half.
    for sep in (
        "; measure with Reach Tools → Path fingerprint",
        "; optional measure: Reach Tools → Path fingerprint",
        "optional measure: Reach Tools → Path fingerprint",
        "measure with Reach Tools → Path fingerprint",
    ):
        note = note.replace(sep, "")
    return note.strip(" ;")


def _fmt_ms(v: float | None) -> str:
    return f"{v:.1f} ms" if v is not None else "—"


def measure_path_fingerprint(
    *,
    local_proxy: str,
    path_summary: str = "",
    hops: list[str] | None = None,
    hop_count: int | None = None,
    fingerprint_note: str = "",
    target_host: str = "1.1.1.1",
    target_port: int = 443,
    timeout: float = 18.0,
    with_direct_baseline: bool = True,
) -> FingerprintReport:
    """Measure F2 cross-layer RTT gap through current SOCKS."""
    hops = hops or []
    n_hops = hop_count if hop_count is not None else len(hops)
    note = _sanitize_core_note(fingerprint_note)

    err = ensure_laminar_importable()
    if err:
        return FingerprintReport(
            ok=False,
            summary="Laminar missing",
            lines=err.splitlines(),
            path_summary=path_summary,
            hop_count=n_hops,
            fingerprint_note=note,
            error=err,
        )

    parsed = _parse_socks(local_proxy)
    if parsed is None:
        return FingerprintReport(
            ok=False,
            summary="No SOCKS proxy",
            lines=[
                "Connect a path on Home first (Spectre local SOCKS).",
                f"local_proxy={local_proxy!r}",
            ],
            path_summary=path_summary,
            hop_count=n_hops,
            fingerprint_note=note,
            error="no socks",
        )

    sh, sp = parsed
    from laminar.rtt import (
        is_loopback_host,
        measure_cross_layer_rtt,
        measure_direct_tls_rtt,
    )
    from laminar.score import score_rtt_gap

    loopback = is_loopback_host(sh)
    rtt = measure_cross_layer_rtt(
        sh, sp, target_host, target_port, timeout=timeout
    )
    gap = score_rtt_gap(rtt)

    lines: list[str] = [
        "Path fingerprint (lab) — Laminar F2",
        "",
        f"Path: {path_summary or '—'}",
        f"Hops: {n_hops}" + (f" ({' → '.join(hops)})" if hops else ""),
        f"SOCKS: {sh}:{sp}" + ("  (loopback)" if loopback else ""),
        f"Target: {target_host}:{target_port}",
        "",
    ]
    if rtt.error:
        lines.append(f"Measure error: {rtt.error}")
        if note:
            lines.extend(["", f"Core note: {note}"])
        lines.extend(
            [
                "",
                "Advisory only — not a full Xue-class classifier.",
            ]
        )
        return FingerprintReport(
            ok=False,
            summary=f"Measure failed: {rtt.error}",
            lines=lines,
            socks=f"{sh}:{sp}",
            rtt_gap_score=gap,
            delta_ms=rtt.delta_ms,
            hop_count=n_hops,
            path_summary=path_summary,
            fingerprint_note=note,
            error=rtt.error,
        )

    lines.extend(
        [
            "Through path (via SOCKS)",
            f"  tcp_to_socks_ms      {_fmt_ms(rtt.tcp_to_socks_ms)}",
            f"  app_through_path_ms {_fmt_ms(rtt.app_through_path_ms)}",
            f"  delta_ms            {_fmt_ms(rtt.delta_ms)}",
            f"  rtt_gap_score       {gap:.3f}  (raw F2; 0=direct-like … 1=proxy-like)",
            "",
        ]
    )

    direct_ms: float | None = None
    direct_err = ""
    if with_direct_baseline:
        direct_ms, direct_err = measure_direct_tls_rtt(
            target_host, target_port, timeout=min(timeout, 12.0)
        )
        if direct_ms is not None:
            lines.append("Clearnet baseline (no SOCKS — may still hit system routing)")
            lines.append(f"  direct_tls_ms       {_fmt_ms(direct_ms)}")
            if rtt.app_through_path_ms is not None:
                overhead = rtt.app_through_path_ms - direct_ms
                lines.append(f"  path_overhead_ms    {overhead:+.1f} ms  (path − direct)")
            lines.append("")
        elif direct_err:
            lines.append(f"Clearnet baseline: unavailable ({direct_err})")
            lines.append("")

    # Operator verdict — honest about loopback SOCKS (Spectre local proxy)
    if loopback:
        path_ms = rtt.app_through_path_ms
        if direct_ms is not None and path_ms is not None and direct_ms > 1.0:
            ratio = path_ms / direct_ms
            if ratio >= 3.0 or (path_ms - direct_ms) >= 150:
                verdict = (
                    f"TUNNEL COST HIGH — path ~{ratio:.1f}× direct "
                    f"({_fmt_ms(path_ms)} vs {_fmt_ms(direct_ms)})"
                )
            elif ratio >= 1.5 or (path_ms - direct_ms) >= 50:
                verdict = (
                    f"TUNNEL COST MODERATE — path ~{ratio:.1f}× direct "
                    f"({_fmt_ms(path_ms)} vs {_fmt_ms(direct_ms)})"
                )
            else:
                verdict = (
                    f"TUNNEL COST LOW — path close to direct "
                    f"({_fmt_ms(path_ms)} vs {_fmt_ms(direct_ms)})"
                )
            lines.append(f"Verdict: {verdict}")
            lines.append(
                "Loopback SOCKS: raw F2 gap is always high (tcp_to_socks ≈ 0). "
                "Overhead vs direct is the useful client-side number."
            )
        else:
            # No usable direct baseline — don't claim Xue midpath detection
            if path_ms is not None and path_ms >= 200:
                verdict = (
                    f"PATH LATENCY {_fmt_ms(path_ms)} — expected for remote exit "
                    f"(Mullvad/Tor/etc.); raw F2=1.0 is not a multi-hop tell here"
                )
            elif path_ms is not None:
                verdict = (
                    f"PATH LATENCY {_fmt_ms(path_ms)} — modest; "
                    f"raw F2 inflated by loopback SOCKS"
                )
            else:
                verdict = "INCONCLUSIVE"
            lines.append(f"Verdict: {verdict}")
            lines.append(
                "Loopback SOCKS (127.0.0.1): tcp_to_socks is local-only, so "
                "rtt_gap_score saturates near 1.0 for any remote tunnel. "
                "That is not the midpath Xue25 vantage (censor on the wire)."
            )
            if not direct_ms and not direct_err:
                lines.append(
                    "Tip: apps-only routing (or clearnet netns) enables a direct baseline."
                )
    else:
        # Remote SOCKS — classic F2 interpretation
        if gap >= 0.75:
            verdict = "HIGH gap — remote SOCKS looks proxy-like on timing"
        elif gap >= 0.35:
            verdict = "MODERATE gap — multi-hop / remote proxy likely"
        else:
            verdict = "LOW gap — SOCKS near target (or nearby proxy)"
        lines.append(f"Verdict: {verdict}")

    if n_hops >= 2:
        lines.append(
            "Multi-hop: outer camouflage does not hide nested size/direction "
            "or midpath ΔRTT (Xue24/25) — needs pcap/midpath capture next."
        )
    elif n_hops == 1 and loopback:
        lines.append(
            "Single local hop: composition (nest) fingerprint needs ≥2 protocol "
            "layers or a midpath tap — F1 pcap measure later."
        )

    if note:
        lines.extend(["", f"Core note: {note}"])

    lines.extend(
        [
            "",
            "Lab measure only — does not change your path.",
        ]
    )

    delta = rtt.delta_ms
    # Short toast/summary
    if loopback and rtt.app_through_path_ms is not None:
        if direct_ms is not None:
            summary = (
                f"path {_fmt_ms(rtt.app_through_path_ms)} · "
                f"direct {_fmt_ms(direct_ms)} · {verdict.split(' — ')[0]}"
            )
        else:
            summary = (
                f"path {_fmt_ms(rtt.app_through_path_ms)} · "
                f"raw F2 {gap:.2f} (loopback) · {verdict.split(' — ')[0]}"
            )
    else:
        summary = (
            f"ΔRTT {_fmt_ms(delta)} · gap {gap:.2f} · {verdict.split(' — ')[0]}"
        )
    return FingerprintReport(
        ok=True,
        summary=summary,
        lines=lines,
        socks=f"{sh}:{sp}",
        rtt_gap_score=gap,
        delta_ms=delta,
        hop_count=n_hops,
        path_summary=path_summary,
        fingerprint_note=note,
    )


def format_report(rep: FingerprintReport) -> str:
    return "\n".join(rep.lines)


def status_fields_from_core(st: Any) -> dict[str, Any]:
    """Pull hop_count / fingerprint_note from CoreStatus if present."""
    hops = list(getattr(st, "hops", None) or [])
    hop_count = getattr(st, "hop_count", None)
    if hop_count is None:
        hop_count = len(hops)
    note = str(getattr(st, "fingerprint_note", None) or "")
    return {
        "local_proxy": str(getattr(st, "local_proxy", "") or ""),
        "path_summary": str(getattr(st, "path_summary", "") or ""),
        "hops": hops,
        "hop_count": int(hop_count or 0),
        "fingerprint_note": note,
        "state": getattr(
            getattr(st, "state", None), "value", str(getattr(st, "state", ""))
        ),
    }
