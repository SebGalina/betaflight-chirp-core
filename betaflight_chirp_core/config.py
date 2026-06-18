"""Tuning config extracted from the blackbox header `H key:value` lines.

Ported from the skill's `_parse_header_config` / `_config_fields` / `_config_diff`,
bytes-based: `parse_header_config` takes the raw log bytes, not a path.
"""
from __future__ import annotations

from .signal import AXES

_LPF_TYPES = {"0": "PT1", "1": "BIQUAD", "2": "PT2", "3": "PT3"}


def parse_header_config(raw: bytes) -> dict:
    """Pull PID + filter settings from the blackbox header (the `H key:value` lines).

    Returns {} when the header is unreadable or has no PID lines — suggestions
    then degrade to curve-only (no PID/filter cross-reference).
    """
    text = raw[:65536].decode("latin1", "replace")
    h: dict[str, str] = {}
    for line in text.split("\n"):
        if not line.startswith("H "):
            continue
        k, _, v = line[2:].partition(":")
        if v:
            h[k.strip()] = v.strip()
    if "rollPID" not in h:
        return {}

    def ints(key, n=None):
        try:
            vals = [int(float(x)) for x in h[key].split(",")]
        except (KeyError, ValueError):
            return None
        return vals[:n] if n else vals

    def i1(key):
        v = ints(key)
        return v[0] if v else None

    cfg: dict = {"pids": {}}
    for axis, key in (("roll", "rollPID"), ("pitch", "pitchPID"), ("yaw", "yawPID")):
        p = ints(key, 3)
        if p:
            cfg["pids"][axis] = p
    cfg["d_max"] = ints("d_max", 3)
    cfg["ff"] = ints("ff_weight", 3)   # per-axis feedforward gain (roll, pitch, yaw); 0 = off
    g1 = ints("gyro_lpf1_dyn_hz")
    cfg["gyro_lpf1"] = {"dyn": g1, "static": i1("gyro_lpf1_static_hz"),
                        "type": _LPF_TYPES.get(h.get("gyro_lpf1_type"), h.get("gyro_lpf1_type"))}
    cfg["gyro_lpf2"] = {"static": i1("gyro_lpf2_static_hz"),
                        "type": _LPF_TYPES.get(h.get("gyro_lpf2_type"), h.get("gyro_lpf2_type"))}
    d1 = ints("dterm_lpf1_dyn_hz")
    cfg["dterm_lpf1"] = {"dyn": d1, "static": i1("dterm_lpf1_static_hz"),
                         "type": _LPF_TYPES.get(h.get("dterm_lpf1_type"), h.get("dterm_lpf1_type"))}
    cfg["dterm_lpf2"] = {"static": i1("dterm_lpf2_static_hz"),
                         "type": _LPF_TYPES.get(h.get("dterm_lpf2_type"), h.get("dterm_lpf2_type"))}
    dn_min = (ints("dyn_notch_min_hz") or [None])[0]
    dn_max = (ints("dyn_notch_max_hz") or [None])[0]
    cfg["dyn_notch"] = {"count": (ints("dyn_notch_count") or [None])[0],
                        "q": (ints("dyn_notch_q") or [None])[0],
                        "min": dn_min, "max": dn_max}
    cfg["rpm_harmonics"] = (ints("rpm_filter_harmonics") or [0])[0]
    cfg["motor_poles"] = i1("motor_poles")
    return cfg


def config_fields(cfg: dict) -> list[tuple]:
    """Flat, ordered (label, value) list of the comparable PID + filter fields — the single
    source of truth for both the textual diff and the HTML comparison table."""
    if not cfg:
        return []
    out = []
    for ax in AXES:
        p = (cfg.get("pids") or {}).get(ax)
        if p:
            out.append((f"{ax} P/I/D", "/".join(map(str, p))))
    if cfg.get("d_max"):
        out.append(("D_max", "/".join(map(str, cfg["d_max"]))))
    if cfg.get("ff") and any(cfg["ff"]):
        out.append(("FF (R/P/Y)", "/".join(map(str, cfg["ff"]))))

    def lpf(key, lbl):
        d = cfg.get(key) or {}
        v = d.get("dyn") or d.get("static")
        if v is not None:
            vs = "–".join(map(str, v)) if isinstance(v, list) else str(v)
            out.append((lbl, (f"{vs} Hz {d.get('type') or ''}").strip()))

    lpf("gyro_lpf1", "gyro LPF1")
    lpf("gyro_lpf2", "gyro LPF2")
    lpf("dterm_lpf1", "D-term LPF1")
    lpf("dterm_lpf2", "D-term LPF2")
    dn = cfg.get("dyn_notch") or {}
    if dn.get("count") is not None:
        out.append(("dyn_notch", f"×{dn.get('count')} Q{dn.get('q')} [{dn.get('min')}–{dn.get('max')} Hz]"))
    if cfg.get("rpm_harmonics") is not None:
        out.append(("RPM filter", f"×{cfg['rpm_harmonics']}"))
    return out


def config_diff(prev: dict, cur: dict) -> str:
    """Exhaustive human summary of what changed between two passes' tuning configs."""
    if not prev or not cur:
        return ""
    pf, cf = dict(config_fields(prev)), dict(config_fields(cur))
    changes = [f"{k} {pf.get(k)}→{cf[k]}" for k, _ in config_fields(cur)
               if k in pf and pf[k] != cf[k]]
    return ", ".join(changes)
