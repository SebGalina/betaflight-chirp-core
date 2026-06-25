"""Analytic Betaflight filter model — predicted magnitude + group-delay budget.

Reconstructs the configured gyro / D-term filter chain (PT1/PT2/PT3, biquad
lowpass, dynamic notch) straight from the parsed header config and evaluates it:
a predicted magnitude curve to overlay on the *measured* raw->filtered noise PSD,
and a group-delay budget in milliseconds (per stage + per path) directly
comparable to the step-response delay and to the phase margin.

This is the forward (config -> expected) companion to the empirical
filter-quality block, which measures the raw->filtered ratio after the fact.
Compute only — no I/O.
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sp_signal

# Betaflight PTn cutoff correction: each of the n cascaded PT1 stages runs at
# fc * CORRECTION so the combined -3 dB point lands on the configured cutoff.
# CORRECTION = 1 / sqrt(2**(1/n) - 1).
_PTN_CORRECTION = {1: 1.0, 2: 1.5537739740, 3: 1.9614591767}

# Default Q of a Betaflight BIQUAD lowpass (Butterworth, 1/sqrt(2)).
_BIQUAD_LPF_Q = 0.70710678

# Reference band for the scalar delay headline: where the loop / setpoint energy
# lives. The group delay is averaged over [0, _DELAY_REF_HZ] Hz.
_DELAY_REF_HZ = 100.0


def _pt1_ba(fc: float, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Betaflight PT1 as a digital (b, a): state += k*(x-state), k = dT/(RC+dT)."""
    if fc <= 0 or fc >= fs / 2.0:
        return np.array([1.0]), np.array([1.0])
    rc = 1.0 / (2.0 * np.pi * fc)
    dt = 1.0 / fs
    k = dt / (rc + dt)
    return np.array([k]), np.array([1.0, -(1.0 - k)])


def _ptn_ba(fc: float, fs: float, order: int) -> tuple[np.ndarray, np.ndarray]:
    """Cascade of `order` PT1 stages at the Betaflight-corrected cutoff."""
    fc_corr = fc * _PTN_CORRECTION.get(order, 1.0)
    b, a = np.array([1.0]), np.array([1.0])
    for _ in range(order):
        b1, a1 = _pt1_ba(fc_corr, fs)
        b, a = np.convolve(b, b1), np.convolve(a, a1)
    return b, a


def _biquad_lpf_ba(fc: float, fs: float, q: float = _BIQUAD_LPF_Q) -> tuple[np.ndarray, np.ndarray]:
    """RBJ biquad lowpass (Betaflight BIQUAD type)."""
    if fc <= 0 or fc >= fs / 2.0:
        return np.array([1.0]), np.array([1.0])
    w0 = 2.0 * np.pi * fc / fs
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / (2.0 * q)
    b = np.array([(1.0 - cw) / 2.0, 1.0 - cw, (1.0 - cw) / 2.0])
    a = np.array([1.0 + alpha, -2.0 * cw, 1.0 - alpha])
    return b / a[0], a / a[0]


def _notch_ba(fc: float, fs: float, q: float) -> tuple[np.ndarray, np.ndarray]:
    """RBJ band-stop (notch) biquad."""
    if fc <= 0 or fc >= fs / 2.0 or q <= 0:
        return np.array([1.0]), np.array([1.0])
    w0 = 2.0 * np.pi * fc / fs
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / (2.0 * q)
    b = np.array([1.0, -2.0 * cw, 1.0])
    a = np.array([1.0 + alpha, -2.0 * cw, 1.0 - alpha])
    return b / a[0], a / a[0]


# Maps the LPF `type` label (from config.py) to an order / kind.
_PTN_ORDER = {"PT1": 1, "PT2": 2, "PT3": 3}


def _lpf_ba(cfg_lpf: dict, fs: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Build (b, a) for one configured LPF stage, or None if disabled/empty.

    A dynamic LPF (`dyn: [lo, hi]`) is evaluated at the mean of its range — a
    representative operating point for the delay budget.
    """
    if not cfg_lpf:
        return None
    dyn = cfg_lpf.get("dyn")
    fc = (sum(dyn) / len(dyn)) if dyn else cfg_lpf.get("static")
    if not fc or fc <= 0:
        return None
    typ = (cfg_lpf.get("type") or "PT1").upper()
    if typ == "BIQUAD":
        return _biquad_lpf_ba(float(fc), fs)
    return _ptn_ba(float(fc), fs, _PTN_ORDER.get(typ, 1))


def _stage_delay_ms(b: np.ndarray, a: np.ndarray, fs: float) -> float:
    """Mean group delay (ms) over [0, _DELAY_REF_HZ], the loop-relevant band."""
    n = 512
    w = np.linspace(0.0, np.pi * min(1.0, 2.0 * _DELAY_REF_HZ / fs), n)
    try:
        _, gd = sp_signal.group_delay((b, a), w=w)
    except Exception:
        return 0.0
    gd = gd[np.isfinite(gd)]
    if gd.size == 0:
        return 0.0
    return float(np.mean(gd)) / fs * 1000.0


def _mag_db(b: np.ndarray, a: np.ndarray, freqs: np.ndarray, fs: float) -> np.ndarray:
    """Magnitude (dB) of (b, a) at `freqs` (Hz)."""
    w = 2.0 * np.pi * freqs / fs
    _, h = sp_signal.freqz(b, a, worN=w)
    return 20.0 * np.log10(np.abs(h) + 1e-12)


# Stage specs per path: (config key, human label).
_GYRO_STAGES = [("gyro_lpf1", "gyro LPF1"), ("gyro_lpf2", "gyro LPF2")]
_DTERM_STAGES = [("dterm_lpf1", "D-term LPF1"), ("dterm_lpf2", "D-term LPF2")]


def _path(stage_specs, cfg: dict, freqs: np.ndarray, fs: float,
          with_notch: bool = False) -> dict:
    """Evaluate one filter path: per-stage delay + cascaded magnitude curve."""
    stages = []
    mag = np.zeros_like(freqs)
    total = 0.0
    for key, label in stage_specs:
        ba = _lpf_ba(cfg.get(key) or {}, fs)
        if ba is None:
            continue
        b, a = ba
        d = _stage_delay_ms(b, a, fs)
        stages.append({"name": label, "type": (cfg[key].get("type") or "PT1"),
                       "fc_hz": _stage_fc(cfg[key]), "delay_ms": round(d, 2)})
        total += d
        mag = mag + _mag_db(b, a, freqs, fs)
    if with_notch:
        dn = cfg.get("dyn_notch") or {}
        fc = dn.get("min")  # representative: the lower edge of the tracked range
        q = dn.get("q")
        if fc and q:
            b, a = _notch_ba(float(fc), fs, float(q))
            d = _stage_delay_ms(b, a, fs)
            stages.append({"name": "dyn_notch", "type": "NOTCH",
                           "fc_hz": float(fc), "delay_ms": round(d, 2)})
            total += d
            mag = mag + _mag_db(b, a, freqs, fs)
    return {"stages": stages, "total_delay_ms": round(total, 2),
            "mag_db": [round(float(v), 2) for v in mag]}


def _stage_fc(cfg_lpf: dict) -> float | None:
    dyn = cfg_lpf.get("dyn")
    return round(sum(dyn) / len(dyn), 0) if dyn else cfg_lpf.get("static")


def build_filter_model(config: dict, fs: float,
                       fmin: float = 1.0, fmax: float = 1000.0,
                       n_freqs: int = 200) -> dict:
    """Predicted filter response + group-delay budget from the parsed config.

    Returns {} when no filter config is present. Otherwise:
      {
        "freqs": [...],                       # log-spaced, Hz (overlay x-axis)
        "gyro":  {"stages": [...], "total_delay_ms": float, "mag_db": [...]},
        "dterm": {"stages": [...], "total_delay_ms": float, "mag_db": [...]},
        "delay_ref_hz": 100.0,                # band the scalar delay averages over
      }
    The gyro `mag_db` includes the dynamic notch (it is in the gyro path); the
    per-stage `delay_ms` is the mean group delay over [0, delay_ref_hz].
    """
    if not config:
        return {}
    has_gyro = any(config.get(k) for k, _ in _GYRO_STAGES)
    has_dterm = any(config.get(k) for k, _ in _DTERM_STAGES)
    if not (has_gyro or has_dterm):
        return {}
    fmax = min(fmax, fs / 2.0 * 0.98)
    freqs = np.logspace(np.log10(max(fmin, 0.5)), np.log10(fmax), n_freqs)
    return {
        "freqs": [round(float(f), 2) for f in freqs],
        "gyro": _path(_GYRO_STAGES, config, freqs, fs, with_notch=True),
        "dterm": _path(_DTERM_STAGES, config, freqs, fs, with_notch=False),
        "delay_ref_hz": _DELAY_REF_HZ,
    }
