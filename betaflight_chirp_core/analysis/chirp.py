"""Chirp closed-loop frequency-response (Bode) analysis — the compute core.

Faithfully extracted from the Betaflight skill's chirp_analysis.py: same Welch
cross-spectral FRF, step response, throttle x frequency resonance map, gyro
noise spectrum, per-axis scoring and synthesis. No CLI, no I/O — operates on a
decoded DataFrame + sample rate, returns plain dicts.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from scipy import signal as sp_signal
from scipy.integrate import trapezoid

from ..signal import AXES, THROTTLE_COL, TIME_COL, active_mask as _active_mask

GYRO_COL = "gyroADC[{}]"


SETPOINT_COL = "setpoint[{}]"


CHIRP_AXIS_COL = "debug[1]"


CHIRP_FREQ_COL = "debug[2]"


DEFAULT_INPUT_COL = "debug[3]"


PHASE_COL = "debug[0]"


PHASE_SCALE = 5000.0


ENERGY_WIN_S = 0.3


ENERGY_STD_FLOOR = 2.0


ENERGY_DOMINANCE = 1.8


DEFAULT_FMIN = 1.0


DEFAULT_FMAX = 1000.0


COHERENCE_GATE = 0.8


PEAK_PROMINENCE_DB = 3.0


THROTTLE_BINS = 8


THROTTLE_IDLE = 1100


def _auto_nperseg(fs: float) -> int:
    """Welch window targeting ~2 Hz resolution, power of 2, generous for averaging."""
    n = int(2 ** round(np.log2(max(fs / 2.0, 512))))
    return int(max(1024, min(n, 8192)))


def _col_has_signal(df: pd.DataFrame, col: str) -> bool:
    """True if the column exists and is not flat (carries actual data, not all-zero)."""
    if col not in df.columns:
        return False
    v = df[col].to_numpy(float)
    return float(np.ptp(v)) > 0.0 and float(np.std(v)) > 0.0


def _has_axis_flag(df: pd.DataFrame) -> bool:
    """True if debug[1] carries a real per-axis flag (legacy firmware), not a flat 0."""
    return CHIRP_AXIS_COL in df.columns and int(df[CHIRP_AXIS_COL].nunique()) > 1


def _reconstruct_exc(df: pd.DataFrame) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Reconstruct the excitation sine and active mask from debug[0] = 5000*sinarg.

    Returns (exc, active) or (None, None) if debug[0] is absent/flat.
    """
    if not _col_has_signal(df, PHASE_COL):
        return None, None
    d0 = df[PHASE_COL].to_numpy(float)
    return np.sin(d0 / PHASE_SCALE), d0 != 0.0


def _inst_freq(df: pd.DataFrame, fs: float) -> np.ndarray | None:
    """Instantaneous chirp frequency (Hz) from the unwrapped debug[0] phase.

    Replaces debug[2] on current firmware. Sawtooth resets between sub-sweeps
    produce gradient spikes; clip to [0, Nyquist] and treat 0 as inactive.
    """
    if not _col_has_signal(df, PHASE_COL):
        return None
    phase = df[PHASE_COL].to_numpy(float) / PHASE_SCALE
    f = np.gradient(np.unwrap(phase), 1.0 / fs) / (2.0 * np.pi)
    return np.clip(f, 0.0, fs / 2.0)


def _label_axes_by_energy(df: pd.DataFrame, active: np.ndarray, fs: float) -> np.ndarray:
    """Per-sample active-axis labels (0/1/2, -1=none) from setpoint energy.

    No debug[1] on current firmware: the chirp drives one axis at a time, so the
    excited axis carries far more setpoint variance than the (pilot-centred) others.
    Energy beats correlation-with-exc, which decorrelates at the top of the sweep.
    """
    n = len(df)
    labels = np.full(n, -1, dtype=int)
    cols = [SETPOINT_COL.format(a) for a in range(3)]
    if any(c not in df.columns for c in cols):
        return labels
    sp = [df[c].to_numpy(float) for c in cols]
    win = max(1, int(ENERGY_WIN_S * fs))
    hop = max(1, win // 2)
    for s in range(0, max(1, n - win), hop):
        sl = slice(s, s + win)
        if active[sl].mean() < 0.5:
            continue
        v = [float(sp[a][sl].std()) for a in range(3)]
        a = int(np.argmax(v))
        if v[a] > ENERGY_STD_FLOOR and v[a] > ENERGY_DOMINANCE * sorted(v)[1]:
            labels[sl] = a
    return labels


def _swept_band(df: pd.DataFrame, mask: np.ndarray, fmin: float, fmax: float,
                finst: np.ndarray | None = None) -> tuple[float, float]:
    """Restrict the band to the frequencies actually swept on this axis.

    Legacy: from debug[2] (deci-Hz). Current firmware: from the reconstructed
    instantaneous frequency `finst`, using robust 2nd/98th percentiles to shrug off
    the sawtooth-reset spikes.
    """
    mask = np.asarray(mask)
    if _col_has_signal(df, CHIRP_FREQ_COL) and mask.any():
        f = df.loc[mask, CHIRP_FREQ_COL].to_numpy(float) / 10.0
        f = f[f > 0]
        if f.size:
            return max(fmin, float(np.min(f))), min(fmax, float(np.max(f)))
    if finst is not None and mask.any():
        f = finst[mask]
        f = f[f > 0]
        if f.size:
            lo = max(fmin, float(np.percentile(f, 2)))
            hi = min(fmax, float(np.percentile(f, 98)))
            if hi > lo:
                return lo, hi
    return fmin, fmax


def _resolve_input(df: pd.DataFrame, exc: np.ndarray | None, requested: str,
                   axis_idx: int, mask: np.ndarray) -> tuple[np.ndarray | None, str | None]:
    """Resolve the FRF input x for this axis as a (values, label) pair.

    Priority:
      --input-col debug0   -> reconstructed unit sine sin(debug[0]/5000) (shape only)
      --input-col setpoint -> setpoint[i] (calibrated, deg/s)
      explicit column      -> that column if present
      default (debug[3])   -> debug[3] when it carries signal (legacy);
                              otherwise setpoint[i] (current firmware fallback)
    """
    mask = np.asarray(mask)
    spcol = SETPOINT_COL.format(axis_idx)

    def take(col):
        return df.loc[mask, col].to_numpy(float)

    if requested == "debug0":
        return (exc[mask], "sin(debug[0]/5000)") if exc is not None else (None, None)
    if requested == "setpoint":
        return (take(spcol), spcol) if spcol in df.columns else (None, None)
    if requested != DEFAULT_INPUT_COL and requested in df.columns:
        return take(requested), requested
    if requested == DEFAULT_INPUT_COL and _col_has_signal(df, DEFAULT_INPUT_COL):
        return take(DEFAULT_INPUT_COL), DEFAULT_INPUT_COL
    # default debug channel empty -> calibrated setpoint
    return (take(spcol), spcol) if spcol in df.columns else (None, None)


def _frf(x: np.ndarray, y: np.ndarray, fs: float, nperseg: int, regularize: float = 1e-6):
    """Return (freqs, gain_db, phase_deg, coherence, H) for the transfer x -> y.

    H is the complex closed-loop FRF (the complementary sensitivity T = gyro/setpoint);
    callers that need the sensitivity S = 1 - T read it from H directly.
    """
    x = sp_signal.detrend(x.astype(float))
    y = sp_signal.detrend(y.astype(float))
    nperseg = min(nperseg, len(x))
    f, Pxx = sp_signal.welch(x, fs=fs, nperseg=nperseg, window="hann")
    _, Pxy = sp_signal.csd(x, y, fs=fs, nperseg=nperseg, window="hann")
    _, Cxy = sp_signal.coherence(x, y, fs=fs, nperseg=nperseg, window="hann")
    reg = regularize * float(np.max(np.abs(Pxx))) if np.max(np.abs(Pxx)) > 0 else regularize
    H = Pxy / (Pxx + reg)
    gain_db = 20.0 * np.log10(np.abs(H) + 1e-12)
    phase_deg = np.degrees(np.unwrap(np.angle(H)))
    return f, gain_db, phase_deg, Cxy, H


def _step_response(setpoint: np.ndarray, gyro: np.ndarray, fs: float, band_fmax: float = 200.0,
                   horizon_ms: float = 150.0, npts: int = 160) -> dict:
    """Time-domain step response of setpoint -> gyro (same Welch H(f) as the Bode, via IFFT).

    The closed-loop H(f) is coherence-weighted and band-limited (smooth Hann taper above the
    swept band) BEFORE the IFFT — otherwise the incoherent high-frequency content injects
    spurious ringing and overshoot that contradicts the phase margin.

    Returns {"t_ms": [...], "y": [...], "metrics": {...}} or {} if unusable. The step is
    normalised to 1.0 at steady state so axes / passes are directly comparable.
    """
    sp = sp_signal.detrend(setpoint.astype(float))
    gy = sp_signal.detrend(gyro.astype(float))
    # window ~0.5 s: long enough to both resolve the ~20 Hz crossover (df ~2 Hz) and to hold the
    # full settling transient (nperseg/fs is the step's time span).
    nperseg = int(2 ** round(np.log2(fs * 0.5)))
    nperseg = max(1024, min(nperseg, 8192, len(sp)))
    f, Pxx = sp_signal.welch(sp, fs=fs, nperseg=nperseg, window="hann")
    _, Pxy = sp_signal.csd(sp, gy, fs=fs, nperseg=nperseg, window="hann")
    _, Cxy = sp_signal.coherence(sp, gy, fs=fs, nperseg=nperseg, window="hann")
    reg = 1e-5 * float(np.max(np.abs(Pxx))) if np.max(np.abs(Pxx)) > 0 else 1e-5
    H = Pxy / (Pxx + reg)
    # weight: soft coherence gate (Wiener-like) * Hann taper across the top of the swept band
    w = np.clip((Cxy - 0.3) / 0.6, 0.0, 1.0)
    fcut = max(60.0, min(band_fmax, fs / 2.0))
    f0 = 0.6 * fcut
    taper = np.where(f <= f0, 1.0,
                     np.where(f >= fcut, 0.0, 0.5 * (1.0 + np.cos(np.pi * (f - f0) / (fcut - f0)))))
    h = np.fft.irfft(H * w * taper, n=nperseg)
    step = np.cumsum(h) / fs
    n = len(step)
    ss = float(np.mean(step[int(0.7 * n):]))
    if abs(ss) < 1e-9:
        return {}
    step = step / ss
    t_ms = np.arange(n) * 1000.0 / fs
    keep = t_ms <= horizon_ms
    t_ms, step = t_ms[keep], step[keep]
    if t_ms.size < 4:
        return {}
    # metrics on the kept window
    peak = float(np.max(step))
    overshoot = round((peak - 1.0) * 100.0, 1) if peak > 1.0 else 0.0
    i10 = int(np.argmax(step >= 0.1)); i90 = int(np.argmax(step >= 0.9))
    rise = round(float(t_ms[i90] - t_ms[i10]), 1) if i90 > i10 > 0 else None
    i50 = int(np.argmax(step >= 0.5))
    delay = round(float(t_ms[i50]), 1) if i50 > 0 else None
    out = np.where(np.abs(step - 1.0) > 0.02)[0]
    settle = round(float(t_ms[min(int(out[-1]) + 1, len(t_ms) - 1)]), 1) if len(out) else round(float(t_ms[0]), 1)
    # downsample for the payload
    s = max(1, len(t_ms) // npts)
    return {
        "t_ms": [round(float(v), 1) for v in t_ms[::s]],
        "y": [round(float(v), 3) for v in step[::s]],
        "metrics": {"overshoot_pct": overshoot, "rise_ms": rise,
                    "delay_ms": delay, "settle_ms": settle, "peak": round(peak, 3)},
    }


def _gain_peaks(freqs, gain_db, coh, fmin, fmax) -> list[dict]:
    """Peaks in the gain curve (resonances / overshoot bumps) within the trusted band."""
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    if band.sum() < 5:
        return []
    fb, gb = freqs[band], gain_db[band]
    df = float(np.median(np.diff(fb))) or 1.0
    distance = max(1, int(round(8.0 / df)))
    idx, props = sp_signal.find_peaks(gb, prominence=PEAK_PROMINENCE_DB, distance=distance)
    peaks = [
        {"freq_hz": round(float(fb[i]), 1),
         "gain_db": round(float(gb[i]), 1),
         "prominence_db": round(float(props["prominences"][k]), 1)}
        for k, i in enumerate(idx)
    ]
    peaks.sort(key=lambda p: p["prominence_db"], reverse=True)
    return peaks


def _smooth(y, w):
    """Centred moving average (odd window), edge-preserving."""
    w = int(max(1, w) | 1)
    if w <= 1 or len(y) < w:
        return np.asarray(y, float)
    k = np.ones(w) / w
    return np.convolve(np.asarray(y, float), k, mode="same")


def _phase_margin(freqs, gain_db, phase_deg, coh, fmin, fmax):
    """Phase margin at the 0 dB gain crossover, made robust to curve wiggle and flight noise.

    The phase is steep at the crossover (~10°/Hz), so reading it at one raw sample is jumpy.
    We smooth the gain/phase, interpolate the exact 0 dB crossing, read the phase there, and
    estimate an uncertainty from the local gain scatter / slope propagated through the phase
    slope. Returns (crossover_hz, margin_deg, margin_unc_deg) or (None, None, None).
    """
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    fb, gb, pb = freqs[band], gain_db[band], phase_deg[band]
    if len(fb) < 6:
        return None, None, None
    w = min(9, len(gb) | 1)
    gs, ps = _smooth(gb, w), _smooth(pb, w)
    crossings = [i for i in range(1, len(gs)) if gs[i - 1] >= 0.0 > gs[i]]
    if not crossings:
        return None, None, None
    i = crossings[-1]                        # highest-freq crossover = the loop bandwidth
    g0, g1, f0, f1 = gs[i - 1], gs[i], fb[i - 1], fb[i]
    t = float(g0 / (g0 - g1)) if g0 != g1 else 0.0      # interpolate 0 dB crossing
    fco = float(f0 + t * (f1 - f0))
    ph = float(ps[i - 1] + t * (ps[i] - ps[i - 1]))
    margin = 180.0 + ph
    margin = margin - 360.0 * np.ceil((margin - 180.0) / 360.0)
    # uncertainty: Δfco = gain scatter / |dgain/df|, propagated through |dphase/df|
    lo, hi = max(0, i - w), min(len(fb), i + w + 1)
    span = float(fb[hi - 1] - fb[lo]) or 1.0
    dgdf = float(gs[hi - 1] - gs[lo]) / span
    dpdf = float(ps[hi - 1] - ps[lo]) / span
    resid = float(np.std(gb[lo:hi] - gs[lo:hi]))
    dfco = abs(resid / dgdf) if abs(dgdf) > 1e-6 else span
    unc = min(90.0, abs(dpdf) * dfco)
    return round(fco, 1), round(margin, 1), round(unc, 0)


def _sensitivity_peak(freqs, H, coh, fmin, fmax):
    """Peak of the sensitivity S(f) = 1 - T(f), where T = H is the measured closed-loop FRF.

    Ms = max|S| is the robustness headline: by Bode's integral |S| exceeds 1 somewhere, so
    Ms >= 1 always, and Ms bounds the phase margin from below via PM >= 2*arcsin(1/(2*Ms)).
    The frequency f_Ms where |S| peaks is the loop's most fragile point — near the open-loop
    crossover / main resonance — and is what actually governs the phase margin (unlike the
    0 dB crossover of T, which is the closed-loop bandwidth). Restricted to the coherent swept
    band so incoherent high-frequency garbage can't fake a peak. The curve is lightly smoothed
    so a single noisy bin doesn't win the argmax.

    Returns (f_ms_hz, ms, pm_guaranteed_deg) or (None, None, None).
    """
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    if int(band.sum()) < 6:
        return None, None, None
    fb = freqs[band]
    s = _smooth(np.abs(1.0 - H[band]), min(9, int(band.sum()) | 1))
    i = int(np.argmax(s))
    ms = float(s[i])
    if ms <= 1e-6:
        return None, None, None
    pm = float(np.degrees(2.0 * np.arcsin(min(1.0, 1.0 / (2.0 * ms)))))
    return round(float(fb[i]), 1), round(ms, 2), round(pm, 0)


def _comp_sensitivity_peak(freqs, H, coh, fmin, fmax):
    """Peak of the complementary sensitivity T(f) = H, the measured closed-loop FRF.

    Mt = max|T| over the coherent swept band. Where Ms = max|S| watches disturbance
    rejection / model-error robustness at the loop's most fragile point, Mt is the
    resonant peak of the closed loop itself: a low Mt (~1.0–1.5) means well-damped
    tracking and good robustness to pure transport/compute delay (the lag already
    folded into the measured T); a high Mt flags a peaky, lightly-damped closed loop.
    Use it as the tie-break companion to Ms — same band, same gate, same smoothing, so
    the two are directly comparable. Restricted to the coherent swept band so incoherent
    high-frequency garbage can't fake a peak; lightly smoothed so one noisy bin can't win.

    Returns (f_mt_hz, mt) or (None, None).
    """
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    if int(band.sum()) < 6:
        return None, None
    fb = freqs[band]
    t = _smooth(np.abs(H[band]), min(9, int(band.sum()) | 1))
    i = int(np.argmax(t))
    mt = float(t[i])
    if mt <= 1e-6:
        return None, None
    return round(float(fb[i]), 1), round(mt, 2)


def _diagnose(peaks, phase_margin, fmin, fmax) -> list[dict]:
    """Bode diagnosis hints, each as a {fr, en} pair."""
    hints = []
    fco, margin, unc = (phase_margin + (None,))[:3] if len(phase_margin) == 2 else phase_margin
    pm = f"±{unc:.0f}° " if unc else ""
    for p in peaks[:3]:
        f, pr = p["freq_hz"], p["prominence_db"]
        if f < 80:
            hints.append({
                "fr": f"Bosse de gain à {f:.0f} Hz (+{pr:.0f} dB) → overshoot en boucle fermée ; "
                      f"c'est la zone P/D — réduire P (ou ajouter du D) si elle dépasse ~3 dB.",
                "en": f"Gain bump at {f:.0f} Hz (+{pr:.0f} dB) → closed-loop overshoot; this is the "
                      f"P/D region — back off P (or add D) if it exceeds ~3 dB.",
            })
        else:
            hints.append({
                "fr": f"Pic de gain marqué à {f:.0f} Hz (+{pr:.0f} dB) → résonance ; à traiter avec "
                      f"un notch (dynamique/statique), pas en changeant les gains PID.",
                "en": f"Sharp gain peak at {f:.0f} Hz (+{pr:.0f} dB) → resonance; target it with a "
                      f"dynamic/static notch, not by changing PID gains.",
            })
    if margin is not None:
        if margin <= 0:
            vfr, ven = "INSTABLE — phase au-delà de -180° avec gain ≥ 0 dB", "UNSTABLE — phase past -180° while gain ≥ 0 dB"
        elif margin >= 30:
            vfr, ven = "sain", "healthy"
        elif margin >= 15:
            vfr, ven = "limite", "marginal"
        else:
            vfr, ven = "faible", "low"
        hints.append({
            "fr": f"Marge de phase ~{margin:.0f}° {pm}au crossover 0 dB de {fco:.0f} Hz ({vfr}). "
                  f"Sous ~30° la boucle sonne ; réduire les gains ou ajouter du filtrage. "
                  f"(Le scalaire est sensible à la pente de phase — compare plutôt les courbes/la step.)",
            "en": f"Phase margin ~{margin:.0f}° {pm}at the {fco:.0f} Hz 0 dB crossover ({ven}). "
                  f"Below ~30° the loop rings; reduce gains or add filtering. "
                  f"(The scalar is sensitive to the phase slope — prefer comparing the curves/step.)",
        })
    else:
        hints.append({
            "fr": "Pas de crossover 0 dB dans la bande cohérente — soit la boucle reste sous 0 dB "
                  "(tune conservateur), soit la cohérence est trop basse pour lire la marge.",
            "en": "No 0 dB gain crossover inside the coherent band — either the loop stays below "
                  "0 dB (conservative tune) or coherence is too low to read the margin.",
        })
    if not peaks:
        hints.append({
            "fr": "Gain plat dans la bande cohérente — aucune bosse d'overshoot ni résonance ne ressort.",
            "en": "Gain is flat in the coherent band — no overshoot bump or resonance stands out.",
        })
    return hints


def _step_diagnosis(m: dict) -> list[dict]:
    """Step-response interpretation (overshoot / rise / settling), each as a {fr, en} pair."""
    if not m:
        return []
    hints = []
    ov = m.get("overshoot_pct") or 0.0
    rise = m.get("rise_ms")
    settle = m.get("settle_ms")
    if ov >= 25:
        hints.append({
            "fr": f"Overshoot ~{ov:.0f}% : fort dépassement → P trop haut ou D insuffisant/trop filtré "
                  f"(rebond, propwash probable). Cohérent avec une marge de phase faible.",
            "en": f"Overshoot ~{ov:.0f}%: large overshoot → P too high or D too low/over-filtered "
                  f"(bounce-back, likely propwash). Consistent with a low phase margin.",
        })
    elif ov >= 10:
        hints.append({
            "fr": f"Overshoot ~{ov:.0f}% : dépassement modéré, acceptable mais réductible (un peu plus "
                  f"de D ou un peu moins de P).",
            "en": f"Overshoot ~{ov:.0f}%: moderate, acceptable but reducible (a touch more D or a "
                  f"touch less P).",
        })
    else:
        hints.append({
            "fr": f"Overshoot ~{ov:.0f}% : réponse bien amortie.",
            "en": f"Overshoot ~{ov:.0f}%: well-damped response.",
        })
    if rise is not None:
        hints.append({
            "fr": f"Temps de montée ~{rise:.0f} ms" + (f", établissement ~{settle:.0f} ms." if settle else "."),
            "en": f"Rise time ~{rise:.0f} ms" + (f", settling ~{settle:.0f} ms." if settle else "."),
        })
    return hints


def _throttle_series(df: pd.DataFrame) -> tuple:
    """A 'throttle' axis for binning: rcCommand[3] if logged, else the motor-output average
    (DShot scale) — so logs that don't log rcCommand still get a throttle map. Returns
    (values, idle_threshold, source_label) or (None, None, None)."""
    if THROTTLE_COL in df.columns:
        return df[THROTTLE_COL].to_numpy(float), float(THROTTLE_IDLE), "rcCommand[3]"
    mc = [f"motor[{i}]" for i in range(4) if f"motor[{i}]" in df.columns]
    if mc:
        v = df[mc].to_numpy(float).mean(axis=1)
        lo, hi = float(np.percentile(v, 2)), float(np.percentile(v, 98))
        return v, lo + 0.10 * (hi - lo), "motor avg"        # idle/spool floor + margin
    return None, None, None


def _thr_percent(thr: np.ndarray, src: str) -> np.ndarray:
    """Map a raw throttle series to 0–100 %. rcCommand[3] is the 1000–2000 µs band; the motor-avg
    fallback is normalised to its own 2nd–98th percentile span (DShot scale has no fixed range)."""
    if src == "rcCommand[3]":
        return np.clip((thr - 1000.0) / 10.0, 0.0, 100.0)
    lo, hi = float(np.percentile(thr, 2)), float(np.percentile(thr, 98))
    return np.clip(100.0 * (thr - lo) / max(hi - lo, 1e-6), 0.0, 100.0)


def _throttle_map(df: pd.DataFrame, fs: float, axis_idx: int, fmin: float, fmax: float,
                  nbins: int = THROTTLE_BINS, poles=None) -> dict:
    """PSD of gyro per throttle slice -> heatmap of how resonances migrate with throttle.

    When `poles` and eRPM telemetry are present, also returns `motor_orders`: the mean motor
    rotation fundamental (Hz) per throttle bin, so the renderer can draw the 1x/2x/3x order
    lines that climb with throttle (ground truth that a peak is motor-borne, not a frame resonance).
    """
    gcol = GYRO_COL.format(axis_idx)
    thr, idle, src = _throttle_series(df)
    if gcol not in df.columns or thr is None:
        return {}
    flying = thr > idle
    if flying.sum() < 1024:
        return {}
    lo, hi = float(np.min(thr[flying])), float(np.max(thr[flying]))
    if hi - lo < 1.0:
        return {}
    edges = np.linspace(lo, hi, nbins + 1)

    # Collect per-bin masks first so every bin can share ONE Welch window size.
    # A per-bin nperseg would give bins of different length different frequency
    # grids -> ragged `levels_db` rows that no longer line up with `freqs`.
    masks, centers = [], []
    for b in range(nbins):
        m = flying & (thr >= edges[b]) & (thr <= edges[b + 1])
        centers.append(round(float((edges[b] + edges[b + 1]) / 2.0)))
        masks.append(m if int(m.sum()) >= 256 else None)
    qualifying = [int(m.sum()) for m in masks if m is not None]
    if not qualifying:
        return {}
    seg = min(1024, min(qualifying))     # one common window -> identical freq grid

    freqs_ref = None
    levels = []
    for m in masks:
        if m is None:
            levels.append(None)
            continue
        sig = sp_signal.detrend(df.loc[m, gcol].to_numpy(float))
        f, pxx = sp_signal.welch(sig, fs=fs, nperseg=seg, window="hann")
        sel = (f >= fmin) & (f <= fmax)
        if freqs_ref is None:
            freqs_ref = f[sel]
        levels.append((10.0 * np.log10(pxx[sel] + 1e-12)).round(1).tolist())
    if freqs_ref is None:
        return {}
    width = len(freqs_ref)
    levels = [row if row is not None else [None] * width for row in levels]
    # downsample frequency axis to keep the payload light
    step = max(1, width // 200)
    out = {
        "axis": AXES[axis_idx],
        "source": src,
        "throttle_bins": centers,
        "freqs": [round(float(x), 1) for x in freqs_ref[::step]],
        "levels_db": [row[::step] for row in levels],
    }
    # per-bin motor fundamental (Hz) from eRPM -> order lines that climb with throttle
    ecols = [f"eRPM[{i}]" for i in range(4) if f"eRPM[{i}]" in df.columns]
    if poles and ecols:
        orders = []
        for m in masks:
            if m is None:
                orders.append(None)
                continue
            e = df.loc[m, ecols].to_numpy(float).ravel()
            e = e[e > 0]
            orders.append(round(float(np.mean(e)) * 100.0 / (poles / 2.0) / 60.0, 1)
                          if e.size >= 64 else None)
        if any(o is not None for o in orders):
            out["motor_orders"] = orders
    return out


def _spectrogram(sig: np.ndarray, fs: float, fmin: float = 5.0, fmax: float | None = None,
                 ntime: int = 200, nfreq: int = 140) -> dict:
    """Time x frequency STFT (dB) of the chirp window — shows the swept sine as a rising diagonal,
    and resonances as bright horizontal bands it crosses. Cropped to the swept band, and
    normalised per time-column so the instantaneous chirp frequency is always the bright cell
    (the diagonal stays crisp even though the gyro attenuates the high-frequency end)."""
    fmax = fmax or fs / 2.0 * 0.98
    if sig.size < 4096:
        return {}
    sig = sp_signal.detrend(sig.astype(float))
    nperseg = 512
    f, t, Sxx = sp_signal.spectrogram(sig, fs=fs, nperseg=nperseg, noverlap=nperseg * 3 // 4, window="hann")
    sel = (f >= fmin) & (f <= fmax)
    f, Sxx = f[sel], Sxx[sel]
    if f.size < 4 or t.size < 4:
        return {}
    db = 10.0 * np.log10(Sxx + 1e-12)
    db = db - np.max(db, axis=0, keepdims=True)   # per-column: 0 dB = loudest freq at each instant
    ts = max(1, -(-db.shape[1] // ntime))         # ceil -> cap the time-axis payload
    db, t = db[:, ::ts], t[::ts]
    # resample the frequency axis onto a LOG grid: the BF chirp sweeps exponentially, so on a log
    # axis the sweep is a straight line and the busy low-frequency region gets the room it needs.
    flo = float(max(fmin, f[0]))
    logf = np.logspace(np.log10(flo), np.log10(float(f[-1])), nfreq)
    db = np.vstack([np.interp(logf, f, db[:, c]) for c in range(db.shape[1])]).T   # nfreq x ntime
    return {
        "t_s": [round(float(x), 2) for x in t],
        "freqs": [round(float(x), 1) for x in logf],
        "logy": True,
        "levels_db": [[round(float(v), 1) for v in row] for row in db],
    }


def _spectrogram_median(segs, fs: float, fmin: float = 5.0, fmax: float | None = None,
                        ntime: int = 200, nfreq: int = 140) -> dict:
    """Median spectrogram across the repeated sweeps of one axis (n >= 2).

    Each sweep is the same construction (monotone exponential 0->fmax sweep), so they align
    on RELATIVE time. We resample every sweep onto a shared (log-f x relative-time) grid,
    per-column normalise as in `_spectrogram`, then take the per-cell median dB — a cleaner
    ridge than any single sweep, with sweep-to-sweep noise averaged down. Same dict shape as
    `_spectrogram` (+ `n_sweeps`); the time axis is rescaled to the median sweep duration so it
    still reads in seconds. Returns {} if fewer than two usable sweeps survive."""
    fmax = fmax or fs / 2.0 * 0.98
    nperseg = 512
    tgrid = np.linspace(0.0, 1.0, ntime)
    logf = None
    grids, durs = [], []
    for seg in segs:
        seg = np.asarray(seg, float)
        if seg.size < 4096:
            continue
        sig = sp_signal.detrend(seg)
        f, t, Sxx = sp_signal.spectrogram(sig, fs=fs, nperseg=nperseg,
                                          noverlap=nperseg * 3 // 4, window="hann")
        sel = (f >= fmin) & (f <= fmax)
        f, Sxx = f[sel], Sxx[sel]
        if f.size < 4 or t.size < 4:
            continue
        db = 10.0 * np.log10(Sxx + 1e-12)
        db = db - np.max(db, axis=0, keepdims=True)       # per-column 0 dB = loudest freq
        if logf is None:                                   # lock the shared freq grid on sweep 0
            flo = float(max(fmin, f[0]))
            logf = np.logspace(np.log10(flo), np.log10(float(f[-1])), nfreq)
        db = np.vstack([np.interp(logf, f, db[:, c]) for c in range(db.shape[1])]).T  # nfreq x ncols
        trel = (t - t[0]) / (t[-1] - t[0])
        db = np.vstack([np.interp(tgrid, trel, db[r, :]) for r in range(db.shape[0])])  # nfreq x ntime
        grids.append(db); durs.append(float(t[-1] - t[0]))
    if len(grids) < 2:
        return {}
    med = np.median(np.stack(grids), axis=0)
    tsec = tgrid * float(np.median(durs))
    return {
        "t_s": [round(float(x), 2) for x in tsec],
        "freqs": [round(float(x), 1) for x in logf],
        "logy": True,
        "levels_db": [[round(float(v), 1) for v in row] for row in med],
        "n_sweeps": len(grids),
    }


NOISE_PEAK_PROM_DB = 3.0


NOISE_FLOOR_PCT = 20


RESIDUAL_OK_DB = 6.0


# --- Filter-quality score (universal 0..1) ----------------------------------
# f_split is where the raw gyro spectrum emerges above its own "natural
# background" (motion + colored noise), found by an iterative sigma-clipped
# log-log power-law fit; the score is the fraction of in-band (0..f_split) raw
# power that survives filtering. ~0.5 = sweet spot, ->1 under-filtered, ->0
# over-filtered (the filter is eating the band it should preserve).
FQ_FMIN_FIT = 40.0       # Hz, fit/detection floor — above the low-freq motion & propwash
                         # knee a single power law cannot model (else the steep motion
                         # content drags the fit down and the whole low band "emerges")
FQ_FMIN_PRES = 20.0      # Hz, lower bound of the preservation band (skip the DC pedestal)
FQ_SIGMA = 2.0           # clip raw points emerging > this many sigma above the fit
FQ_RATIO_THRESH = 1.5    # raw/fit emergence ratio that marks the signal "rising"
FQ_CONSEC = 10           # consecutive points above the ratio to call it f_split
FQ_SMOOTH = 10           # moving-average window on the ratio before detection
FQ_MAX_ITER = 50         # sigma-clip iteration cap
FQ_ALPHA_TOL = 0.01      # converged when |Δα| < this * |α|
FQ_ALIAS_GUARD_HZ = 50.0  # exclude [fs/2 - this .. fs/2] (aliasing zone) from the fit
FQ_PEAK_WIN_BINS = 10    # ±bins for the local-median/σ motor-peak detector
FQ_PEAK_SIGMA = 3.0      # drop a bin from the fit if raw > local_median + this·σ
FQ_HF_FLOOR_MIN = 100.0  # Hz, lower bound of the HF broadband-floor estimate
FQ_K_HF = 5.0            # emergence must also sit > this · HF floor (not just > fit)
FQ_KNEE_GRID = 25        # candidate breakpoints for the two-slope (broken power-law) fit
FQ_SLOPE_TOL = 0.1       # both segment slopes must be ≤ this (a PSD background falls/flat,
                         # never rises with frequency) — rejects degenerate fits
# Corner-anchored band split (Phase 1) + emergent-excess attenuation (Phase 2) +
# phase-cost preservation (Phase 3) + regime gating (Phase 4).
FQ_F_CTRL_MAX = 90.0     # Hz, hard ceiling of the "control" (preservation) band — the useful
                         # rate-command bandwidth; never preserve above this even if the corner sits higher
FQ_COH_GATE = 0.9        # coherence floor for the filter FRF (filt is a deterministic function of
                         # raw, so coherence should be ~1; below this the bin is non-stationary noise)
FQ_PHASE_GOOD_MS = 0.5   # group-delay added in-band at/below which preservation = 1 (cheap filter)
FQ_PHASE_BAD_MS = 2.5    # group-delay at/above which preservation = 0 (filter lag dominates feel)
FQ_ALPHA_STEEP = 2.0     # alpha_hf at/above this = spectrum dominated by the motion tail (label only;
                         # A is keyed on floor-referenced peaks, not on this slope)
FQ_FLOOR_FMIN = 70.0     # Hz, lower bound of the broadband-floor band for the attenuation measure
                         # (matches the noise panel's floor/peak detection)
FQ_A_GOOD = 0.70         # attenuation at/above this = emergent noise well removed (not under-filtered)
FQ_P_GOOD = 0.70         # preservation at/above this = phase cost acceptable (not over-filtered)


def _broken_powerlaw_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float]:
    """Continuous two-segment linear fit in log-log space (a broken power law).

    A single power law cannot model the gyro spectrum's knee: a steep low-freq
    motion/propwash rolloff that flattens into the HF noise plateau. Modelling the
    knee keeps the background from sitting under the motion tail (which would make
    the whole low band spuriously 'emerge'). Returns (b, s1, s2, xb): intercept,
    low-freq slope, HF slope, breakpoint (in log10 Hz). Among grid breakpoints, the
    least-squares best whose *both* slopes are non-rising (≤ FQ_SLOPE_TOL) is kept —
    a rising background is unphysical and signals an overfit. Continuity is built in.
    """
    n = x.size
    lo, hi = np.quantile(x, 0.15), np.quantile(x, 0.85)
    best = best_valid = None
    for xb in np.linspace(lo, hi, FQ_KNEE_GRID):
        h = np.maximum(x - xb, 0.0)                 # y = b + s1*x + (s2-s1)*relu(x-xb)
        a = np.column_stack([np.ones(n), x, h])
        coef, *_ = np.linalg.lstsq(a, y, rcond=None)
        ssr = float(np.sum((y - a @ coef) ** 2))
        s1, s2 = float(coef[1]), float(coef[1] + coef[2])
        cand = (ssr, float(coef[0]), s1, s2, float(xb))
        if best is None or ssr < best[0]:
            best = cand
        if s1 <= FQ_SLOPE_TOL and s2 <= FQ_SLOPE_TOL and (best_valid is None or ssr < best_valid[0]):
            best_valid = cand
    _, b, s1, s2, xb = best_valid if best_valid is not None else best
    return b, s1, s2, xb


def _bp_model(x: np.ndarray, b: float, s1: float, s2: float, xb: float) -> np.ndarray:
    return b + s1 * x + (s2 - s1) * np.maximum(x - xb, 0.0)


def _fq_reco(score: float) -> str:
    """Map a filter-quality score [0..1] to a recommendation code (FR/EN in strings)."""
    if score >= 0.90:
        return "decrease_strong"
    if score >= 0.70:
        return "decrease_slight"
    if score >= 0.50:
        return "sweet_spot"
    if score >= 0.30:
        return "increase_slight"
    return "increase_strong"


def _fq_reco_pres(p: float) -> str:
    """Reco when only preservation P is measurable (no emergent noise to attenuate).

    High P = the filter spares the useful band and there is nothing left to remove → room to
    loosen. Low P = the filter is eating the control band for no benefit → over-filtered."""
    if p >= 0.70:
        return "loosen_candidate"
    if p >= 0.50:
        return "sweet_spot"
    return "decrease_strong"


def _fq_reco_atten(a: float) -> str:
    """Reco when only attenuation A is measurable (emergence present, no phase data).

    High A = the filter is killing the excess → fine. Low A = noise still emerges → tighten."""
    if a >= 0.50:
        return "sweet_spot"
    if a >= 0.30:
        return "increase_slight"
    return "increase_strong"


def _fq_verdict(A: float, P: float) -> str:
    """Recommendation from attenuation A and preservation P *directly* — not from the harmonic
    score, which can't tell an under-filtered low score from an over-filtered one (opposite fixes).

    Under-filtered = emergent noise survives (low A) → tighten filtering. Over-filtered = excess
    phase delay (low P) → loosen filtering. When both are healthy, a very high P means there is
    spare phase margin to loosen further; otherwise it is the sweet spot."""
    if A < 0.50:
        return "increase_strong"          # noise clearly survives → more filtering
    if A < FQ_A_GOOD:
        return "increase_slight"
    if P < 0.50:
        return "decrease_strong"          # heavy phase lag → loosen hard
    if P < FQ_P_GOOD:
        return "decrease_slight"
    if P >= 0.90:
        return "loosen_candidate"         # both healthy, lots of phase margin
    return "sweet_spot"


def _fq_compose(A: float | None, P: float | None, *, alpha_steep: bool = False):
    """Recompose the filter-quality score + verdict from attenuation A and preservation P
    (Phase 5). Shared by ``_filter_quality`` (per axis) and ``_filter_quality_block`` (mean) so
    the two never diverge — the bug being that ``_fq_reco`` on a preservation-only score reads a
    high P (great, low phase lag) as 'over-filtered'. Returns (score, recommendation, confidence,
    reason|None)."""
    if A is None and P is None:
        if alpha_steep:
            return None, "na_motion_dominated", "low", \
                "no measurable HF noise (steep roll-off / motion-dominated spectrum)"
        return None, "loosen_candidate", "low", "clean spectrum, no noise emergence — room to loosen"
    if A is None:                              # only preservation measurable (nothing emerged)
        return P, _fq_reco_pres(P), "low", "no noise emergence — verdict from preservation only"
    if P is None:                              # only attenuation measurable (no phase data)
        return A, _fq_reco_atten(A), "low", None
    score = round(2.0 * A * P / (A + P), 3) if (A + P) > 0.0 else 0.0
    return score, _fq_verdict(A, P), "high", None     # verdict from A/P directly, not the score


# filter "order" (group-delay multiplier vs a single PT1) by Betaflight low-pass type
_LPF_ORDER = {"PT1": 1.0, "PT2": 2.0, "PT3": 3.0, "BIQUAD": 1.41}  # BIQUAD ≈ √2/ωc at DC


def _lpf_group_delay_ms(stages: list, f_ctrl_max: float) -> float | None:
    """Analytic mean group delay (ms) of a cascade of low-pass stages over the control band.

    A 1st-order PT1 of cutoff fc has group delay τ(f) = (1/ωc)/(1+(2πf/ωc)²), ωc=2π·fc; PTn ≈ n·that
    (n cascaded stages add); BIQUAD ≈ √2/ωc at DC. Cascaded stages' delays add. Averaged over
    [FQ_FMIN_PRES, f_ctrl_max]. ``stages`` = [(fc_hz, order), …]. Returns None if no stage."""
    stages = [(fc, order) for fc, order in stages if fc and fc > 0]
    if not stages:
        return None
    grid = np.linspace(FQ_FMIN_PRES, max(f_ctrl_max, FQ_FMIN_PRES + 1.0), 16)
    w = 2.0 * np.pi * grid
    tau = np.zeros_like(grid)
    for fc, order in stages:
        wc = 2.0 * np.pi * float(fc)
        tau += order * (1.0 / wc) / (1.0 + (w / wc) ** 2)
    return float(np.mean(tau)) * 1e3


def _filter_corners(config: dict, fs: float) -> dict | None:
    """Effective GYRO-side filter corner frequencies for the quiet-window noise spectrum.

    The noise spectrum is gyroUnfilt vs gyroADC, so only the gyro filters shape it:
      - gyro_lpf1 is *dynamic*. The quiet window is measured at low throttle, so the dynamic
        cutoff sits near its LOWER bound; we use that lower bound as the effective corner. Using
        the high bound would overstate the filtering actually applied during the measurement and
        push the band split too high (the bug the corner-anchoring fixes).
      - gyro_lpf2 is a static second stage.
      - dyn_notch sweeps between min/max (carried through for context, not part of the LPF corner).

    Returns {lpf1, lpf2, notch_min, notch_max, corner, group_delay_ms} (Hz/ms) or None if nothing
    usable. ``corner`` is the lowest active low-pass cutoff; ``group_delay_ms`` is the analytic
    in-control-band group delay of the gyro LPF cascade (deterministic, the robust preservation
    metric — the measured FRF group delay is noisy/non-stationary, see _filter_phase_cost).
    """
    if not config:
        return None
    g1 = config.get("gyro_lpf1") or {}
    dyn = g1.get("dyn") or []
    lpf1 = (dyn[0] if dyn else None) or g1.get("static")     # lower dynamic bound, else static
    g2 = config.get("gyro_lpf2") or {}
    lpf2 = g2.get("static")
    dn = config.get("dyn_notch") or {}
    notch_min, notch_max = dn.get("min"), dn.get("max")
    lpfs = [v for v in (lpf1, lpf2) if v and v > 0]
    corner = float(min(lpfs)) if lpfs else None
    if corner is None and notch_min is None and notch_max is None:
        return None
    f_ctrl_max = min(corner, FQ_F_CTRL_MAX) if corner else FQ_F_CTRL_MAX
    stages = [(lpf1, _LPF_ORDER.get((g1.get("type") or "PT1").upper(), 1.0)),
              (lpf2, _LPF_ORDER.get((g2.get("type") or "PT1").upper(), 1.0))]
    group_delay_ms = _lpf_group_delay_ms(stages, f_ctrl_max)
    return {"lpf1": lpf1, "lpf2": lpf2, "notch_min": notch_min, "notch_max": notch_max,
            "corner": corner, "group_delay_ms": group_delay_ms}


def _filter_phase_cost(sig_raw: np.ndarray, sig_filt: np.ndarray, fs: float, nperseg: int,
                       f_ctrl_max: float = FQ_F_CTRL_MAX) -> dict | None:
    """Phase cost of the gyro filter chain: group delay it adds in the control band.

    The true price of filtering is not lost power, it is the group delay injected where the
    rate loop is still acting. We measure the filter's own FRF H = gyroADC/gyroUnfilt (a
    deterministic transfer, so coherence should be ~1 — bins below FQ_COH_GATE are dropped as
    non-stationary, e.g. dyn_notch wandering). Returns {phase_lag_ms, mag_droop_db, coherence}
    over [FQ_FMIN_PRES, f_ctrl_max], or None if the band is unusable.
    """
    if sig_raw is None or sig_filt is None:
        return None
    sig_raw = np.asarray(sig_raw, float)
    sig_filt = np.asarray(sig_filt, float)
    if sig_raw.size < nperseg or sig_filt.size < nperseg:
        return None
    fr, gdb, ph, coh, _ = _frf(sig_raw, sig_filt, fs, nperseg)
    band = (fr >= FQ_FMIN_PRES) & (fr <= f_ctrl_max) & (coh > FQ_COH_GATE)
    if int(band.sum()) < 3:
        return None
    # Group delay via a robust LINEAR fit of unwrapped phase vs ω over the band (slope = mean group
    # delay), not a pointwise -dφ/dω which is dominated by phase jitter when the filter barely acts.
    # Clamp ≥0: a causal filter cannot have negative group delay (negatives = pure measurement noise).
    w = 2.0 * np.pi * fr
    phr = np.unwrap(np.deg2rad(ph))
    slope = float(np.polyfit(w[band], phr[band], 1)[0])
    tau_ms = max(0.0, -slope) * 1e3
    droop_i = int(np.argmin(np.abs(fr - f_ctrl_max)))
    return {
        "phase_lag_ms": tau_ms,
        "mag_droop_db": float(gdb[droop_i]),
        "coherence": float(np.mean(coh[band])),
    }


def _filter_quality(f: np.ndarray, raw_lin: np.ndarray, filt_lin: np.ndarray,
                    fs: float, *, corners: dict | None = None,
                    sig_raw: np.ndarray | None = None, sig_filt: np.ndarray | None = None,
                    nperseg: int | None = None) -> dict | None:
    """Filter-quality score in [0,1] from raw vs filtered gyro PSD (linear), corner-anchored.

    Fits a (broken) power law (PSD ∝ f^-α) to the raw spectrum in log-log, iteratively
    sigma-clipping points that emerge *above* the fit, so the fit converges on the signal's
    natural background. ``f_split`` is the first frequency where the raw/fit ratio stays above
    FQ_RATIO_THRESH for FQ_CONSEC consecutive points.

    The two reference-independent components are:
      A (attenuation)  — over [max(f_split, corner)..fmax], the fraction of the *emergent excess*
                         above the background the filter removes. None when nothing emerges
                         (clean spectrum) or the spectrum is motion-dominated (alpha_hf steep).
      P (preservation) — the phase cost of the filter in the control band [FQ_FMIN_PRES..f_ctrl_max]:
                         group delay added, mapped 0.5→2.5 ms onto 1→0 (needs ``sig_raw``/``sig_filt``).
                         Falls back to a corner-bounded magnitude ratio when no time signals are given.

    When a gyro ``corners`` dict is supplied the band split is anchored on the real low-pass
    corner instead of fs/4, which is what stops P from counting the intended roll-off as lost
    signal and A from sweeping a too-wide noise band. Returns None if the fit is not feasible.
    """
    f = np.asarray(f, float)
    raw = np.asarray(raw_lin, float)
    filt = np.asarray(filt_lin, float)

    base_sel = (f >= FQ_FMIN_FIT) & (raw > 0)
    if int(base_sel.sum()) < 20:
        return None

    # --- pre-fit masking (fit + f_split detection only; the A/P band integrals
    # below keep using the full arrays). Both masks keep the power-law fit and
    # f_split off content that would bias them:
    #   1) the aliasing zone just below Nyquist,
    #   2) sharp motor-harmonic peaks (a narrow spike can drag f_split onto itself
    #      and inflate the clip σ); wide humps survive (local median tracks them).
    nyq = fs / 2.0
    mask = base_sel & (f < nyq - FQ_ALIAS_GUARD_HZ)
    ki = np.where(mask)[0]
    if ki.size:
        rsub = raw[ki]
        w = FQ_PEAK_WIN_BINS
        peak = np.zeros(rsub.size, dtype=bool)
        for j in range(rsub.size):
            seg = rsub[max(0, j - w): j + w + 1]
            sg = float(seg.std())
            if sg > 0.0 and rsub[j] > float(np.median(seg)) + FQ_PEAK_SIGMA * sg:
                peak[j] = True
        mask[ki[peak]] = False
    # bins dropped from the fit relative to the plain [5Hz..) selection (alias + peaks)
    masked_bins_count = int(base_sel.sum() - mask.sum())

    fm = f[mask]
    rm = raw[mask]

    alpha_hf = alpha_lf = 0.0
    f_knee = 0.0
    f_split: float | None = None
    b = s1 = s2 = xb = 0.0
    have_fit = False
    if fm.size >= 20:
        have_fit = True
        x = np.log10(fm)
        y = np.log10(rm)
        keep = np.ones(x.shape, dtype=bool)
        s2_prev: float | None = None
        for _ in range(FQ_MAX_ITER):
            if int(keep.sum()) < 8:
                break
            b, s1, s2, xb = _broken_powerlaw_fit(x[keep], y[keep])
            resid = y - _bp_model(x, b, s1, s2, xb)
            sigma = float(resid[keep].std())
            if sigma <= 0.0:
                break
            new_keep = resid <= FQ_SIGMA * sigma     # drop points emerging ABOVE the fit
            if s2_prev is not None and abs(s2 - s2_prev) < FQ_ALPHA_TOL * max(abs(s2_prev), 1e-9):
                break
            s2_prev = s2
            if np.array_equal(new_keep, keep):
                break
            keep = new_keep

        alpha_hf, alpha_lf, f_knee = -s2, -s1, float(10.0 ** xb)
        # two-slope background, then the emergence ratio — on the masked arrays
        fit_m = 10.0 ** _bp_model(x, b, s1, s2, xb)
        ratio = rm / np.maximum(fit_m, 1e-30)
        if FQ_SMOOTH > 1:
            ratio = np.convolve(ratio, np.ones(FQ_SMOOTH) / FQ_SMOOTH, mode="same")
        # HF broadband floor: emergence must rise above both the power-law fit AND a
        # multiple of this floor, so the low-freq motion tail (above the fit but below
        # the real motor noise) can no longer pin f_split to the fit-domain start.
        hf = (fm >= FQ_HF_FLOOR_MIN) & (fm <= nyq - FQ_ALIAS_GUARD_HZ)
        hf_floor = float(np.median(rm[hf])) if hf.any() else 0.0
        logger.debug("filter_quality: hf_floor=%.4g (over %d bins)", hf_floor, int(hf.sum()))
        run = 0
        for i in range(fm.size):
            if (fm[i] >= FQ_FMIN_FIT and ratio[i] > FQ_RATIO_THRESH
                    and rm[i] > hf_floor * FQ_K_HF):
                run += 1
                if run >= FQ_CONSEC:
                    f_split = float(fm[i - FQ_CONSEC + 1])
                    break
            else:
                run = 0

    fmax = float(f[-1])
    corner = float(corners["corner"]) if (corners and corners.get("corner")) else None
    # Control-band ceiling: never preserve above the corner, and never above the hard rate-loop
    # bandwidth (FQ_F_CTRL_MAX). Without a corner, fall back to the hard ceiling alone.
    f_ctrl_max = min(corner, FQ_F_CTRL_MAX) if corner else FQ_F_CTRL_MAX

    # --- Band split (Phase 1). With a real corner the fallback anchors on it, not fs/4 — fs/4
    # is what let the preservation band swallow the intended low-pass roll-off.
    fallback = False
    if f_split is None or f_split < 20.0 or f_split > fs / 3.0:
        if corner is not None:
            f_split = corner
        else:
            f_split = fs / 4.0
            fallback = True

    # --- Attenuation A (Phase 2): of the noise that *emerges as peaks above the broadband floor*,
    # how much linear power the filter removes. Keyed on the SAME floor-referenced peak detection
    # the noise panel shows (find_peaks prominence), not on the power-law fit: the fit is fragile on
    # real broken spectra (it absorbs the noise hump as "background"), and a raw/floor integral would
    # count the smooth motion tail as excess. Peaks exclude both — flat noise and smooth backgrounds
    # have no prominent peaks, so A is None there ("nothing emergent to attenuate").
    alpha_regime = "steep" if alpha_hf >= FQ_ALPHA_STEEP else "normal"
    A: float | None = None
    excess_present = False
    a_band = (f >= FQ_FLOOR_FMIN) & (f <= nyq - FQ_ALIAS_GUARD_HZ) & (raw > 0)   # floor-referenced band
    if int(a_band.sum()) >= 8:
        fb = f[a_band]
        rb_lin = np.asarray(raw_lin, float)[a_band]
        flb_lin = np.asarray(filt_lin, float)[a_band]
        floor_lin = float(np.percentile(rb_lin, NOISE_FLOOR_PCT))
        if floor_lin > 0.0:
            rb_db = 10.0 * np.log10(np.maximum(rb_lin / floor_lin, 1e-12))   # raw over floor (dB)
            dfd = float(np.median(np.diff(fb))) or 1.0
            idx, _props = sp_signal.find_peaks(rb_db, prominence=NOISE_PEAK_PROM_DB,
                                               distance=max(1, int(15.0 / dfd)))
            if idx.size:
                excess_present = True
                # emergent linear power above the floor at each peak, raw vs filtered
                er = np.clip(rb_lin[idx] - floor_lin, 0.0, None)
                ef = np.clip(flb_lin[idx] - floor_lin, 0.0, None)
                denom = float(er.sum())
                if denom > 0.0:
                    A = min(max(1.0 - float(ef.sum()) / denom, 0.0), 1.0)

    # --- Preservation P (Phase 3): phase cost of the filter in the control band — the group delay
    # it adds where the loop still acts. PRIMARY = analytic delay from the (known) gyro filter
    # config: deterministic and identical across axes, so immune to the FRF phase noise that made
    # the measured group delay swing wildly / go negative axis-to-axis. The measured FRF lag is
    # kept as a guard-rail (phase_lag_frf_ms). Magnitude ratio is a last resort when neither exists.
    phase = _filter_phase_cost(sig_raw, sig_filt, fs, nperseg, f_ctrl_max) if nperseg else None
    analytic_ms = corners.get("group_delay_ms") if corners else None
    P: float | None = None
    phase_lag_ms = mag_droop_db = phase_lag_frf_ms = None
    if phase is not None:
        phase_lag_frf_ms = round(phase["phase_lag_ms"], 3)
        mag_droop_db = round(phase["mag_droop_db"], 2)
    if analytic_ms is not None:
        phase_lag_ms = round(analytic_ms, 3)
        pp = _ramp(analytic_ms, FQ_PHASE_GOOD_MS, FQ_PHASE_BAD_MS)
        P = round(pp / 100.0, 3) if pp is not None else None
    elif phase is not None:
        phase_lag_ms = phase_lag_frf_ms
        pp = _ramp(phase["phase_lag_ms"], FQ_PHASE_GOOD_MS, FQ_PHASE_BAD_MS)
        P = round(pp / 100.0, 3) if pp is not None else None
    elif not fallback:
        # corner-bounded magnitude preservation (no phase data at all): only trustworthy once the
        # band is anchored on a real split/corner; in fallback the band is meaningless → leave None.
        p_hi = min(f_split, f_ctrl_max)
        pb = (f >= FQ_FMIN_PRES) & (f <= p_hi)
        if int(pb.sum()) >= 3:
            pr = float(trapezoid(raw[pb], f[pb]))
            if pr > 0.0:
                P = min(max(float(trapezoid(filt[pb], f[pb])) / pr, 0.0), 1.0)

    # --- Score recomposition + verdict (Phase 5).
    if A is not None:
        A = round(A, 3)
    score, recommendation, confidence, reason = _fq_compose(
        A, P, alpha_steep=(alpha_regime == "steep"))

    out = {
        "score": score,
        "score_attenuation": A,
        "score_preservation": P,
        "f_split_hz": round(f_split, 1),
        "f_ctrl_max_hz": round(f_ctrl_max, 1),
        "corner_hz": round(corner, 1) if corner is not None else None,
        "alpha": round(alpha_hf, 2),            # HF (noise-plateau) slope — the meaningful one
        "alpha_lf": round(alpha_lf, 2),         # low-freq (motion) slope
        "alpha_regime": alpha_regime,
        "f_knee_hz": round(f_knee, 1),          # breakpoint between the two slopes
        "fallback": fallback,
        "excess_present": excess_present,
        "masked_bins_count": masked_bins_count,
        "phase_lag_ms": phase_lag_ms,            # analytic (config) when available, else FRF
        "phase_lag_frf_ms": phase_lag_frf_ms,    # measured FRF group delay (guard-rail / reference)
        "mag_droop_db": mag_droop_db,
        "confidence": confidence,
        "recommendation": recommendation,
    }
    if reason is not None:
        out["reason"] = reason
    return out


def _filter_quality_block(noise: dict) -> dict:
    """Collect per-axis filter_quality from the noise spectrum + a mean-of-axes summary.

    Per-axis scores can be None (clean spectrum, motion-dominated, or only one of A/P
    measurable), so every mean filters the Nones first and is itself None when no axis
    has that quantity. The verdict follows the mean score when it is defined."""
    axes = (noise or {}).get("axes") or {}
    per = {a: d["filter_quality"] for a, d in axes.items() if d.get("filter_quality")}
    if not per:
        return {}

    def _mean(key, agg=np.mean, nd=3):
        vals = [v[key] for v in per.values() if v.get(key) is not None]
        return round(float(agg(vals)), nd) if vals else None

    mean_A = _mean("score_attenuation")              # real per-axis variation → mean
    # P / phase lag come from one filter shared by all axes, so the per-axis spread is measurement
    # noise — aggregate with the MEDIAN (one bad axis can't drag the verdict, the report5 bug).
    mean_P = _mean("score_preservation", np.median)
    # Recompose the mean verdict from mean A/P with the SAME Phase-5 logic as per axis, so the
    # block can't disagree with its own rows (a preservation-only mean must not read as "tighten").
    all_steep = mean_A is None and all(v.get("alpha_regime") == "steep" for v in per.values())
    score, recommendation, confidence, reason = _fq_compose(mean_A, mean_P, alpha_steep=all_steep)
    mean = {
        "score": score,
        "score_attenuation": mean_A,
        "score_preservation": mean_P,
        "f_split_hz": _mean("f_split_hz", np.median, 1),
        "phase_lag_ms": _mean("phase_lag_ms", np.median),
        "phase_lag_frf_ms": _mean("phase_lag_frf_ms", np.median),
        "confidence": confidence,
        "recommendation": recommendation,
    }
    if reason is not None:
        mean["reason"] = reason
    # worst surviving residual peak across axes (the case A can't see — flag it explicitly)
    survivors = [(v["worst_resid_db"], v.get("worst_resid_hz"), a)
                 for a, v in per.items() if v.get("worst_resid_db") is not None]
    if survivors:
        rd, hz, ax = max(survivors)
        mean["worst_resid_db"], mean["worst_resid_hz"], mean["worst_resid_axis"] = rd, hz, ax
    return {"axes": per, "mean": mean}


def _filter_model_block(config: dict, fs: float, fmin: float, fmax: float) -> dict:
    """Analytic predicted filter response + group-delay budget from the config.

    Forward model (config -> expected): a predicted magnitude curve to overlay on
    the measured noise PSD and a per-stage delay budget. Empty when no config.
    """
    if not config:
        return {}
    from .filter_model import build_filter_model
    try:
        return build_filter_model(config, fs, fmin=fmin, fmax=fmax)
    except Exception:
        logger.exception("filter_model failed")
        return {}


def _noise_spectrum(df: pd.DataFrame, fs: float, axis_idx: int, quiet_mask: np.ndarray,
                    fmin: float = 30.0, fmax: float | None = None,
                    corners: dict | None = None) -> dict:
    """Gyro PSD (dB) over a chirp-free window: raw (gyroUnfilt) vs filtered (gyroADC).

    During the chirp the gyro is full of excitation across the whole band, which masks the real
    noise floor — so we measure over the *quiet* window (flying, this axis not excited).

    Both curves are referenced to the RAW broadband noise floor (the flat HF baseline, robust and
    stable from flight to flight, unlike the motion peak): 0 dB = floor. A peak's height above the
    floor is its prominence; the filtered peak's residual above the floor and the raw->filtered
    attenuation are the reference-independent quantities the filtering decision rests on.
    """
    gcol = GYRO_COL.format(axis_idx)
    ucol = f"gyroUnfilt[{axis_idx}]"
    if gcol not in df.columns:
        return {}
    fmax = fmax or fs / 2.0 * 0.98
    m = np.asarray(quiet_mask)
    if int(m.sum()) < 2048:
        return {}
    nperseg = int(min(4096, 2 ** int(np.log2(int(m.sum())))))
    nperseg = max(1024, nperseg)

    def psd(col):
        sig = sp_signal.detrend(df.loc[m, col].to_numpy(float))
        f, pxx = sp_signal.welch(sig, fs=fs, nperseg=min(nperseg, len(sig)), window="hann")
        return f, pxx

    has_unfilt = ucol in df.columns
    f, raw_lin = psd(ucol if has_unfilt else gcol)
    _, filt_lin = psd(gcol)
    # Filter-quality score needs the full linear PSD down to ~5 Hz for the power-law fit, so
    # compute it before the dB conversion and the fmin crop below. The phase-cost preservation
    # (Phase 3) also needs the raw time-domain gyroUnfilt/gyroADC over the quiet window.
    fq = None
    if has_unfilt:
        sig_raw = sp_signal.detrend(df.loc[m, ucol].to_numpy(float))
        sig_filt = sp_signal.detrend(df.loc[m, gcol].to_numpy(float))
        fq = _filter_quality(f, raw_lin, filt_lin, fs, corners=corners,
                             sig_raw=sig_raw, sig_filt=sig_filt, nperseg=nperseg)
    # D-term SNR from the pre-filter (gyroUnfilt) spectrum — full band, before the fmin crop below.
    snr_d = _dterm_snr_db(f, raw_lin, fmax) if has_unfilt else None
    raw = 10.0 * np.log10(raw_lin + 1e-10)
    filt = 10.0 * np.log10(filt_lin + 1e-10)
    sel = (f >= fmin) & (f <= fmax)
    f, raw, filt = f[sel], raw[sel], filt[sel]
    if f.size < 8:
        return {}
    hf = f >= 70.0
    floor = float(np.percentile(raw[hf], NOISE_FLOOR_PCT)) if hf.sum() >= 5 else float(np.median(raw))
    raw = raw - floor                       # 0 dB = raw broadband noise floor
    filt = filt - floor

    peaks = []
    if hf.sum() > 5:
        fb, rb = f[hf], raw[hf]
        dfd = float(np.median(np.diff(fb))) or 1.0
        idx, props = sp_signal.find_peaks(rb, prominence=NOISE_PEAK_PROM_DB, distance=max(1, int(15.0 / dfd)))
        for k, i in enumerate(idx):
            j = int(np.argmin(np.abs(f - fb[i])))
            peaks.append({"freq_hz": round(float(fb[i]), 0),
                          "above_floor_db": round(float(rb[i]), 1),       # raw peak height over the floor
                          "resid_db": round(float(filt[j]), 1),           # filtered residual over the floor
                          "atten_db": round(float(rb[i] - filt[j]), 1),   # raw -> filtered cut (ref-independent)
                          "prom_db": round(float(props["prominences"][k]), 1)})
        peaks.sort(key=lambda p: p["above_floor_db"], reverse=True)
        peaks = peaks[:6]

    # Worst surviving peak (highest filtered residual over the floor). A saturates near 1 when the
    # filter removes most of the *energy*, so it can miss a single peak that creeps back above the
    # floor (e.g. a too-low dyn_notch Q). Surface that residual explicitly alongside filter_quality.
    if fq is not None and peaks:
        wp = max(peaks, key=lambda p: p["resid_db"])
        fq["worst_resid_db"] = wp["resid_db"]
        fq["worst_resid_hz"] = wp["freq_hz"]

    step = max(1, len(f) // 400)
    return {
        "axis": AXES[axis_idx], "has_unfilt": bool(has_unfilt),
        "freqs": [round(float(v), 1) for v in f[::step]],
        "raw_db": [round(float(v), 1) for v in raw[::step]],
        "filt_db": [round(float(v), 1) for v in filt[::step]],
        "peaks": peaks,
        **({"filter_quality": fq} if fq else {}),
        **({"dterm_snr_db": snr_d} if snr_d is not None else {}),
    }


def _band_floor_peaks(f: np.ndarray, db: np.ndarray, fmin: float, fmax: float) -> dict:
    """Crop a PSD (dB) to [fmin, fmax], re-reference to its HF broadband floor (0 dB =
    floor) and pick the peaks above it. Shared by the single-signal D-term / motor panels;
    the gyro keeps its own raw-vs-filtered path in `_noise_spectrum`. Returns {} if too short."""
    sel = (f >= fmin) & (f <= fmax)
    f, db = f[sel], db[sel]
    if f.size < 8:
        return {}
    hf = f >= 70.0
    floor = float(np.percentile(db[hf], NOISE_FLOOR_PCT)) if hf.sum() >= 5 else float(np.median(db))
    db = db - floor
    peaks = []
    if hf.sum() > 5:
        fb, rb = f[hf], db[hf]
        dfd = float(np.median(np.diff(fb))) or 1.0
        idx, props = sp_signal.find_peaks(rb, prominence=NOISE_PEAK_PROM_DB, distance=max(1, int(15.0 / dfd)))
        for k, i in enumerate(idx):
            peaks.append({"freq_hz": round(float(fb[i]), 0),
                          "above_floor_db": round(float(rb[i]), 1),
                          "prom_db": round(float(props["prominences"][k]), 1)})
        peaks.sort(key=lambda p: p["above_floor_db"], reverse=True)
        peaks = peaks[:6]
    step = max(1, len(f) // 400)
    return {"freqs": [round(float(v), 1) for v in f[::step]],
            "db": [round(float(v), 1) for v in db[::step]], "peaks": peaks}


def _quiet_nperseg(n_quiet: int) -> int:
    return max(1024, int(min(4096, 2 ** int(np.log2(n_quiet)))))


def _dterm_motor_spectrum(df: pd.DataFrame, fs: float, quiet_for, quiet_primary: np.ndarray,
                          fmin: float = 30.0, fmax: float | None = None) -> dict:
    """D-term (axisD) PSD per axis + a combined motor-output PSD, over the chirp-free window.

    The D-term is the PID path that dominates the motor command at high frequency, so a sharp
    HF peak here is the oscillation that saturates the ESCs and heats the motors — the gyro
    spectrum can look filtered-clean while the D-term/motor still ring. The motor curve averages
    the per-motor PSDs (linear power): uncorrelated noise averages down, a shared oscillation
    survives. Present only when D-term / motor channels were logged.
    """
    fmax = fmax or fs / 2.0 * 0.98
    out: dict = {"axes": {}}

    for i, axis in enumerate(AXES):
        col = f"axisD[{i}]"
        if col not in df.columns:
            continue
        m = np.asarray(quiet_for(i))
        if int(m.sum()) < 2048:
            continue
        nperseg = _quiet_nperseg(int(m.sum()))
        sig = sp_signal.detrend(df.loc[m, col].to_numpy(float))
        f, pxx = sp_signal.welch(sig, fs=fs, nperseg=min(nperseg, len(sig)), window="hann")
        d = _band_floor_peaks(f, 10.0 * np.log10(pxx + 1e-10), fmin, fmax)
        if d:
            out["axes"][axis] = d

    mcols = [f"motor[{i}]" for i in range(8) if f"motor[{i}]" in df.columns]
    m = np.asarray(quiet_primary)
    if mcols and int(m.sum()) >= 2048:
        nperseg = _quiet_nperseg(int(m.sum()))
        pacc, f = None, None
        for c in mcols:
            sig = sp_signal.detrend(df.loc[m, c].to_numpy(float))
            f, pxx = sp_signal.welch(sig, fs=fs, nperseg=min(nperseg, len(sig)), window="hann")
            pacc = pxx if pacc is None else pacc + pxx
        mt = _band_floor_peaks(f, 10.0 * np.log10(pacc / len(mcols) + 1e-10), fmin, fmax)
        if mt:
            out["motor"] = mt

    return out if out["axes"] or out.get("motor") else {}


DTERM_SNR_SPLIT_HZ = 100.0   # boundary: useful D (reaction to real motion) below, derivation noise above
DTERM_SNR_FMIN_HZ = 10.0     # ignore sub-10 Hz drift / detrend residual in the signal band


def _dterm_snr_db(f: np.ndarray, raw_lin: np.ndarray, fmax: float,
                  split: float = DTERM_SNR_SPLIT_HZ, fmin: float = DTERM_SNR_FMIN_HZ) -> float | None:
    """D-term signal/noise ratio (dB) from the RAW (pre-filter) gyro spectrum.

    The D path differentiates the gyro, so its power spectrum is the gyro PSD weighted by (2*pi*f)^2.
    Split that derivative power at `split` Hz: below = the useful D reaction to real stick / airframe
    motion, above = the broadband noise the derivative amplifies (what the D-term LPFs exist to kill).
    A high ratio means little of the D path is noise -> headroom to raise or disable dterm_lpf2.
    The (2*pi)^2 constant cancels in the ratio, so we weight by f^2 directly. Computed from gyroUnfilt
    (pre-filter), so it reflects the noise the LPF *would* see, not what survives the current filter.
    """
    f = np.asarray(f, float)
    w = (f ** 2) * np.asarray(raw_lin, float)
    lo = (f >= fmin) & (f < split)
    hi = (f >= split) & (f <= fmax)
    if int(lo.sum()) < 3 or int(hi.sum()) < 3:
        return None
    s = float(trapezoid(w[lo], f[lo]))
    n = float(trapezoid(w[hi], f[hi]))
    if s <= 0.0 or n <= 0.0:
        return None
    return round(10.0 * np.log10(s / n), 1)


def _worst_residual_db(noise: dict) -> float | None:
    """The largest filtered residual above the noise floor (dB), or None — how much resonance
    survives filtering. Low (near the floor) = the filtering has flattened the noise."""
    peaks = (noise or {}).get("peaks") or []
    return max((p["resid_db"] for p in peaks), default=None)


def _filter_disable_notes(noise: dict, config: dict) -> list[dict]:
    """Which whole filters could be turned off, judged from the raw noise above their cut-off.

    A lowpass only earns its phase lag if there is noise in its stopband. The second LPF stage
    (gyro_lpf2 / dterm_lpf2) is the usual redundancy: if the raw spectrum is already at the floor
    above its cut-off, it removes nothing the first stage + RPM/dyn_notch didn't already remove.
    """
    if not config:
        return []
    freqs = (noise or {}).get("freqs") or []
    raw = (noise or {}).get("raw_db") or []
    if not freqs:
        return []

    def max_raw_above(fc):
        vals = [r for f, r in zip(freqs, raw) if f >= fc]
        return max(vals) if vals else None

    out = []
    g1 = config.get("gyro_lpf1") or {}
    g1hi = (g1.get("dyn") or [None, None])[-1] or g1.get("static")
    g2c = (config.get("gyro_lpf2") or {}).get("static")
    if g2c:
        mr = max_raw_above(g2c)              # raw level above the cut-off, relative to the floor
        if mr is not None and mr <= RESIDUAL_OK_DB:
            out.append({
                "fr": f"Gyro LPF2 (statique {g2c} Hz) : au-dessus de sa coupure le bruit brut reste à +{max(mr,0):.0f} dB "
                      f"du plancher (LPF1 jusqu'à {g1hi} Hz + RPM/dyn_notch font le travail) → rien à enlever. "
                      f"Candidat à désactiver (gyro_lpf2_static_hz = 0) pour retirer son retard de phase.",
                "en": f"Gyro LPF2 (static {g2c} Hz): above its cut-off the raw noise stays at +{max(mr,0):.0f} dB over the "
                      f"floor (LPF1 up to {g1hi} Hz + RPM/dyn_notch do the work) → nothing to remove. Candidate to "
                      f"disable (gyro_lpf2_static_hz = 0) to drop its phase lag."})
        elif mr is not None:
            out.append({
                "fr": f"Gyro LPF2 ({g2c} Hz) : encore +{mr:.0f} dB de bruit au-dessus du plancher passé sa coupure — "
                      f"il sert toujours, à garder.",
                "en": f"Gyro LPF2 ({g2c} Hz): still +{mr:.0f} dB of noise above the floor past its cut-off — it's still "
                      f"working, keep it."})
    d2c = (config.get("dterm_lpf2") or {}).get("static")
    if d2c:
        mrd = max_raw_above(150.0)
        if mrd is not None and mrd <= RESIDUAL_OK_DB:
            out.append({
                "fr": f"D-term LPF2 (statique {d2c} Hz) : le D-term n'est pas mesuré ici, mais le gyro qui l'alimente "
                      f"est déjà au plancher au-dessus de 150 Hz (+{max(mrd,0):.0f} dB) → ce 2e étage est probablement "
                      f"désactivable aussi (dterm_lpf2_static_hz = 0) ; à confirmer au ressenti/température.",
                "en": f"D-term LPF2 (static {d2c} Hz): the D-term isn't measured here, but the gyro feeding it is already "
                      f"at the floor above 150 Hz (+{max(mrd,0):.0f} dB) → this 2nd stage is likely disable-able too "
                      f"(dterm_lpf2_static_hz = 0); confirm by feel/motor temps."})
        elif mrd is not None:
            out.append({
                "fr": f"D-term LPF2 ({d2c} Hz) : le bruit moteur vers 230 Hz (+{mrd:.0f} dB du plancher) tombe dans la "
                      f"zone que le D amplifie → le filtrage D-term est utile ici, à garder.",
                "en": f"D-term LPF2 ({d2c} Hz): motor noise near 230 Hz (+{mrd:.0f} dB over the floor) lands in the band "
                      f"the D amplifies → D-term filtering earns its keep here, leave it on."})
    return out


# --- reactivity (freestyle) tuning headroom -------------------------------------
# Thresholds for "the loop is conservative — there is room to be snappier". An LLM
# chasing freestyle reactivity reads these instead of re-deriving them from the raw
# metrics. All deliberately cautious: they flag *available* headroom, not a mandate.
_MT_OVERDAMPED = 1.05      # closed-loop peak below this = damped, room to raise P
_MT_SNAPPY = 1.15          # freestyle target band upper edge (Mt ~1.1-1.15)
_OVERSHOOT_HEADROOM_PCT = 8.0   # step overshoot below this = room before it gets bouncy
_ERR_RATIO_SLUGGISH = 0.20      # tracking error / setpoint above this = visible lag
_DTERM_LPF_LOW_HZ = 200.0       # dterm LPF1 upper cut-off below this adds avoidable D lag


def _tuning_suggestions(results: dict, noise: dict, config: dict) -> list[dict]:
    """Reactivity-oriented headroom notes for a freestyle tune ({fr, en} per item).

    Reads the closed-loop peak (Mt), step overshoot, tracking-error ratio and the
    filter cut-offs to say where the loop is *conservative* and could be pushed for
    a snappier feel — the gain/filter advice the metric blocks stop short of giving.
    Empty when nothing has headroom (already aggressive, or no chirp/step to judge).
    """
    out: list[dict] = []
    pids = (config or {}).get("pids") or {}
    for axis in AXES:
        d = results.get(axis) or {}
        if not d:
            continue
        mt = d.get("mt")
        os_pct = ((d.get("step") or {}).get("metrics") or {}).get("overshoot_pct")
        err = d.get("track_err_ratio")
        axis_pids = pids.get(axis) or []
        p_gain = axis_pids[0] if axis_pids else None
        d_gain = axis_pids[2] if len(axis_pids) >= 3 else None
        # Damped loop (Mt under ~1.05) with little/no step overshoot = P has room.
        damped = mt is not None and mt < _MT_OVERDAMPED
        low_os = os_pct is None or os_pct < _OVERSHOOT_HEADROOM_PCT
        if damped and low_os:
            sluggish = err is not None and err > _ERR_RATIO_SLUGGISH
            pstr = f" (P actuel {p_gain})" if p_gain else ""
            erf = f" Suivi de consigne mou (err_ratio {err:.2f})." if sluggish else ""
            ere = f" Setpoint tracking is loose (err_ratio {err:.2f})." if sluggish else ""
            # Only pair with a D bump on axes that run D (yaw is usually P/I-only).
            df_clause = " Monter D en parallèle pour garder l'amortissement." if d_gain else ""
            de_clause = " Raise D alongside to keep the damping." if d_gain else ""
            out.append({
                "axis": axis,
                "fr": f"{axis} — réactivité dispo : Mt {mt:.2f} (<{_MT_OVERDAMPED}) + overshoot "
                      f"{0.0 if os_pct is None else os_pct:.0f} % = boucle sur-amortie.{erf} Marge pour "
                      f"monter P ~+10-15 %{pstr} jusqu'à Mt ~{_MT_SNAPPY} / overshoot ~6-8 %.{df_clause}",
                "en": f"{axis} — reactivity available: Mt {mt:.2f} (<{_MT_OVERDAMPED}) + overshoot "
                      f"{0.0 if os_pct is None else os_pct:.0f}% = over-damped loop.{ere} Room to raise P "
                      f"~+10-15%{pstr} toward Mt ~{_MT_SNAPPY} / overshoot ~6-8%.{de_clause}"})
    # D-term LPF1 cut-off low + clean noise above 150 Hz = the lag is avoidable.
    d1 = (config or {}).get("dterm_lpf1") or {}
    d1hi = (d1.get("dyn") or [None, None])[-1] or d1.get("static")
    freqs = (noise or {}).get("freqs") or []
    raw = (noise or {}).get("raw_db") or []
    if d1hi and freqs and raw:
        above = [r for f, r in zip(freqs, raw) if f >= 150.0]
        clean = above and max(above) <= RESIDUAL_OK_DB
        if d1hi < _DTERM_LPF_LOW_HZ and clean:
            out.append({
                "fr": f"D-term LPF1 plafonne à {d1hi:.0f} Hz, mais le gyro est déjà au plancher au-dessus de "
                      f"150 Hz (+{max(max(above),0):.0f} dB) : ce cut-off bas ajoute du retard de phase D sans "
                      f"rien filtrer d'utile. Le remonter (~{d1hi:.0f}→250-300 Hz) coupe le lag D et laisse "
                      f"monter D pour plus de réactivité.",
                "en": f"D-term LPF1 caps at {d1hi:.0f} Hz, but the gyro is already at the floor above 150 Hz "
                      f"(+{max(max(above),0):.0f} dB): this low cut-off adds D phase lag without filtering "
                      f"anything useful. Raising it (~{d1hi:.0f}→250-300 Hz) cuts D lag and lets D go higher "
                      f"for more reactivity."})
    return out


def _motor_harmonics(df: pd.DataFrame, mask: np.ndarray, poles, fmax: float) -> dict:
    """Motor rotation harmonics from eRPM telemetry, over the quiet window.

    BF stores eRPM in 100-eRPM LSBs; mechanical rotation Hz = eRPM*100 / (poles/2) / 60. Motors
    run at a spread of rpm (4 motors x throttle variation), so each harmonic is a *band*
    [n*f_lo, n*f_hi] rather than a line — exactly where the dyn_notch/RPM filter has to track.
    """
    if not poles:
        return {}
    cols = [f"eRPM[{i}]" for i in range(4) if f"eRPM[{i}]" in df.columns]
    if not cols:
        return {}
    e = df.loc[np.asarray(mask), cols].to_numpy(float).ravel()
    e = e[e > 0]
    if e.size < 256:
        return {}
    hz = e * 100.0 / (poles / 2.0) / 60.0            # per-sample, per-motor fundamental
    f_lo, f_hi = float(np.percentile(hz, 10)), float(np.percentile(hz, 90))
    if f_hi <= 0:
        return {}
    bands = []
    for n in range(1, 9):
        if n * f_lo > fmax:
            break
        bands.append({"n": n, "lo": round(n * f_lo, 0), "hi": round(min(n * f_hi, fmax), 0)})
    return {"f_lo": round(f_lo, 0), "f_hi": round(f_hi, 0), "bands": bands}


def _pid_balance(df: pd.DataFrame, fs: float) -> dict:
    """Per-axis P/I/D contribution balance + tracking error, from the logged term columns.

    axisP/axisI/axisD are the actual PID term outputs; their RMS over the active flight tells
    which term dominates the loop (and the inter-axis balance). The tracking error RMS
    (setpoint-gyro) is normalised by the setpoint RMS into `err_ratio`, a flight-style-robust
    quality number folded into the tune score. Returns {axis: {...}} or {} if columns absent.
    """
    mask = _active_mask(df)
    out: dict = {}
    for i, axis in enumerate(AXES):
        pcol, icol, dcol = f"axisP[{i}]", f"axisI[{i}]", f"axisD[{i}]"
        if pcol not in df.columns or icol not in df.columns:
            continue
        rms = lambda c: float(np.sqrt(np.mean(np.square(df.loc[mask, c].to_numpy(float))))) \
            if c in df.columns else 0.0
        # AC-RMS (mean removed) drives the share split: the I-term carries a large DC
        # offset (it holds attitude/trim) that raw RMS would count as loop authority,
        # masking the true P/I/D balance — std() reflects the *active* contribution.
        acrms = lambda c: float(np.std(df.loc[mask, c].to_numpy(float))) \
            if c in df.columns else 0.0
        rp, ri, rd = rms(pcol), rms(icol), rms(dcol)
        ap, ai, ad = acrms(pcol), acrms(icol), acrms(dcol)
        tot = ap + ai + ad
        if tot <= 1e-9:
            continue
        entry = {
            "rms_p": round(rp, 1), "rms_i": round(ri, 1), "rms_d": round(rd, 1),
            "pct_p": round(100.0 * ap / tot, 0), "pct_i": round(100.0 * ai / tot, 0),
            "pct_d": round(100.0 * ad / tot, 0),
        }
        spcol, gycol = SETPOINT_COL.format(i), GYRO_COL.format(i)
        if spcol in df.columns and gycol in df.columns:
            sp = df.loc[mask, spcol].to_numpy(float)
            gy = df.loc[mask, gycol].to_numpy(float)
            err = float(np.sqrt(np.mean(np.square(sp - gy))))
            sp_rms = float(np.sqrt(np.mean(np.square(sp))))
            entry["err_rms"] = round(err, 1)
            entry["err_ratio"] = round(err / sp_rms, 3) if sp_rms > 1e-6 else None
        out[axis] = entry
    return out


def _noise_suggestions(noise: dict) -> list[dict]:
    """Observations from the noise PSD peaks — prominence over the floor + raw->filtered
    attenuation, both reference-independent ({fr, en})."""
    if not noise:
        return []
    peaks = noise.get("peaks") or []
    out = []
    if not peaks:
        out.append({
            "fr": "Plancher de bruit propre : aucun pic >70 Hz ne dépasse le plancher — rien de discret à notcher.",
            "en": "Clean noise floor: no >70 Hz peak rises above the floor — nothing discrete to notch."})
        return out
    bands = (noise.get("motor") or {}).get("bands") or []
    for p in peaks:
        f, af, resid, att = p["freq_hz"], p["above_floor_db"], p["resid_db"], p["atten_db"]
        hn = next((b["n"] for b in bands if b["lo"] <= f <= b["hi"]), None)
        ofr = f", sur l'harmonique {hn}× moteur" if hn else ""
        oen = f", on the {hn}× motor harmonic" if hn else ""
        head = (f"{f:.0f} Hz : pic de bruit à +{af:.0f} dB au-dessus du plancher{ofr}, atténué de {att:.0f} dB "
                f"par les filtres",
                f"{f:.0f} Hz: noise peak at +{af:.0f} dB above the floor{oen}, cut by {att:.0f} dB by the filters")
        if resid <= RESIDUAL_OK_DB:
            out.append({
                "fr": f"{head[0]} → résiduel +{max(resid,0):.0f} dB, dans le plancher : aplati, rien à faire ici.",
                "en": f"{head[1]} → residual +{max(resid,0):.0f} dB, in the floor: flattened, nothing to do here."})
        else:
            out.append({
                "fr": f"{head[0]} → résiduel encore +{resid:.0f} dB au-dessus du plancher : raie discrète non "
                      f"complètement traitée (notch à renforcer/cibler).",
                "en": f"{head[1]} → residual still +{resid:.0f} dB above the floor: a discrete line not fully "
                      f"handled (notch to strengthen/target)."})
    return out


def _downsample(freqs, *series, fmin, fmax, max_pts=600):
    band = (freqs >= fmin) & (freqs <= fmax)
    fb = freqs[band]
    step = max(1, len(fb) // max_pts)
    out = [fb[::step]]
    for s in series:
        out.append(s[band][::step])
    return out


SWEEP_MIN_GAP_S = 0.5


SWEEP_MIN_DUR_S = 2.0


def _split_sweeps(mask: np.ndarray, fs: float,
                  min_gap_s: float = SWEEP_MIN_GAP_S, min_dur_s: float = SWEEP_MIN_DUR_S):
    """Contiguous runs of `mask` (one per chirp activation), bridging sub-second gaps.

    A multi-sweep log triggers the chirp several times on the same axis; each trigger is a
    full 0->fmax sweep separated from the next by idle samples (the energy/flag mask drops to
    False). Splitting the axis mask into runs — short intra-sweep dropouts bridged, the long
    inter-activation gaps preserved — recovers the individual sweeps for repeatability stats.
    Returns [(start, end_exclusive), ...], keeping only runs >= min_dur_s.
    """
    idx = np.where(np.asarray(mask))[0]
    if idx.size == 0:
        return []
    gap = max(1, int(min_gap_s * fs))
    splits = np.where(np.diff(idx) > gap)[0]
    starts = np.concatenate(([idx[0]], idx[splits + 1]))
    ends = np.concatenate((idx[splits], [idx[-1]]))
    mindur = int(min_dur_s * fs)
    return [(int(s), int(e) + 1) for s, e in zip(starts, ends) if e - s >= mindur]


def _med_range(vals):
    """(median, lo, hi) over the non-None values, or (None, None, None) if all None."""
    a = np.array([v for v in vals if v is not None], float)
    if a.size == 0:
        return None, None, None
    return round(float(np.median(a)), 2), round(float(a.min()), 2), round(float(a.max()), 2)


def _curve_band(series):
    """Element-wise (median, lo, hi) over a list of equal-length arrays."""
    arr = np.vstack(series)
    return np.median(arr, axis=0), arr.min(axis=0), arr.max(axis=0)


def _aggregate_step(steps: list, band_fields: dict) -> dict:
    """Median step curve + min/max envelope from per-sweep step responses.

    The per-sweep steps share the same time grid (identical nperseg/fs/horizon/downsample), so
    they line up; a stray fragment is truncated to the common length. Scalar metrics are the
    median across sweeps, with the inter-sweep range recorded in `band_fields[...]_range`.
    Returns {} if fewer than two usable steps (caller then has no band to draw).
    """
    steps = [s for s in steps if s and s.get("y")]
    if len(steps) < 2:
        return steps[0] if steps else {}
    n = min(len(s["y"]) for s in steps)
    t_ms = steps[0]["t_ms"][:n]
    ys = [np.array(s["y"][:n], float) for s in steps]
    y_med, y_lo, y_hi = _curve_band(ys)
    ov, ov_lo, ov_hi = _med_range([s["metrics"].get("overshoot_pct") for s in steps])
    rise, ri_lo, ri_hi = _med_range([s["metrics"].get("rise_ms") for s in steps])
    settle, se_lo, se_hi = _med_range([s["metrics"].get("settle_ms") for s in steps])
    delay, _, _ = _med_range([s["metrics"].get("delay_ms") for s in steps])
    peak, _, _ = _med_range([s["metrics"].get("peak") for s in steps])
    band_fields["overshoot_range"] = [ov_lo, ov_hi]
    band_fields["rise_range"] = [ri_lo, ri_hi]
    band_fields["settle_range"] = [se_lo, se_hi]
    return {
        "t_ms": t_ms,
        "y": [round(float(v), 3) for v in y_med],
        "y_lo": [round(float(v), 3) for v in y_lo],
        "y_hi": [round(float(v), 3) for v in y_hi],
        "metrics": {"overshoot_pct": ov, "rise_ms": rise, "delay_ms": delay,
                    "settle_ms": settle, "peak": peak},
    }


def _frf_pack(x, y, sp_vals, fs, nperseg, a_fmin, a_fmax):
    """One sweep's FRF + robustness scalars + step response, bundled for aggregation."""
    freqs, gain, phase, coh, H = _frf(x, y, fs, nperseg)
    fco, margin, m_unc = _phase_margin(freqs, gain, phase, coh, a_fmin, a_fmax)
    f_ms, ms, pm_ms = _sensitivity_peak(freqs, H, coh, a_fmin, a_fmax)
    f_mt, mt = _comp_sensitivity_peak(freqs, H, coh, a_fmin, a_fmax)
    step = {}
    if sp_vals is not None:
        sb = min(a_fmax, max(120.0, 6.0 * fco)) if fco else min(a_fmax, 150.0)
        step = _step_response(sp_vals, y, fs, band_fmax=sb)
    return {"freqs": freqs, "gain": gain, "phase": phase, "coh": coh, "H": H,
            "fco": fco, "margin": margin, "m_unc": m_unc,
            "f_ms": f_ms, "ms": ms, "pm_ms": pm_ms,
            "f_mt": f_mt, "mt": mt, "step": step}


def analyse(df, fs, input_col, axes_filter=None, fmin=DEFAULT_FMIN, fmax=DEFAULT_FMAX,
            nperseg=None, motor_poles=None, config=None) -> dict:
    nyq = fs / 2.0
    fmax = min(fmax, nyq * 0.98)
    if nperseg is None:
        nperseg = _auto_nperseg(fs)

    # Detect firmware generation and build reconstruction aids once.
    has_flag = _has_axis_flag(df)
    exc, active = _reconstruct_exc(df)
    finst = _inst_freq(df, fs)
    labels = None
    if not has_flag and active is not None:
        labels = _label_axes_by_energy(df, active, fs)
    if not has_flag and labels is None and active is None:
        print("Warning: no debug[1] axis flag and no debug[0] phase channel — "
              "cannot segment chirp axes. Was the log recorded with debug_mode=CHIRP?",
              file=sys.stderr)
    elif not has_flag:
        print("Note: current-firmware chirp log (debug[1..3] empty) — segmenting axes "
              "by setpoint energy from debug[0]; FRF input falls back to calibrated "
              "setpoint[i].", file=sys.stderr)

    results: dict = {}
    primary_axis_idx = None
    primary_n = 0
    sweep_windows: dict = {}   # axis index -> [(start, end_exclusive), ...] for the spectrogram merge
    # throttle as 0–100 % over the whole log, for the per-sweep Ms-vs-throttle mini (TPA cue)
    _thr_all, _, _thr_src = _throttle_series(df)
    thr_pct_all = _thr_percent(_thr_all, _thr_src) if _thr_all is not None else None

    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue
        gcol = GYRO_COL.format(i)
        if gcol not in df.columns:
            continue

        # Axis mask: legacy debug[1] flag, else energy labels, else whole flying window.
        if has_flag:
            mask = df[CHIRP_AXIS_COL].to_numpy() == i
        elif labels is not None:
            mask = labels == i
        elif active is not None:
            mask = active
        elif THROTTLE_COL in df.columns:
            mask = df[THROTTLE_COL].to_numpy() > THROTTLE_IDLE
        else:
            mask = np.ones(len(df), dtype=bool)
        if int(mask.sum()) < 512:
            continue

        x, xcol = _resolve_input(df, exc, input_col, i, mask)
        if x is None:
            continue
        if int(mask.sum()) > primary_n:
            primary_n, primary_axis_idx = int(mask.sum()), i

        y = df.loc[np.asarray(mask), gcol].to_numpy(float)
        a_fmin, a_fmax = _swept_band(df, mask, fmin, fmax, finst)
        spcol = SETPOINT_COL.format(i)

        # Repeatability: if the chirp was triggered several times on this axis, each activation is
        # an independent full sweep. Compute one FRF/step per sweep and aggregate into a median
        # curve + min/max band. With a single sweep we keep the exact original single-FRF path.
        # Split on the chirp-ON mask (debug[0]!=0), not the energy labels: the labeller bleeds ~half
        # a window past the activation, and those zero-excitation edge samples corrupt the per-sweep
        # Welch/step (steady-state drifts -> false overshoot). The active mask is the true window.
        axis_active = (mask & active) if active is not None else mask
        sweeps = _split_sweeps(axis_active, fs)
        sweep_windows[i] = sweeps
        packs = []
        sweep_thr = []   # mean throttle (%) per sweep, aligned with packs
        if len(sweeps) >= 2:
            for s, e in sweeps:
                sm = np.zeros(len(df), dtype=bool); sm[s:e] = axis_active[s:e]
                xs, _ = _resolve_input(df, exc, input_col, i, sm)
                if xs is None:
                    continue
                ys = df.loc[sm, gcol].to_numpy(float)
                sps = df.loc[sm, spcol].to_numpy(float) if spcol in df.columns else None
                packs.append(_frf_pack(xs, ys, sps, fs, nperseg, a_fmin, a_fmax))
                sweep_thr.append(float(np.mean(thr_pct_all[sm])) if thr_pct_all is not None else None)
            if packs:   # drop any short fragment whose Welch grid doesn't match the others
                gl = max(len(p["freqs"]) for p in packs)
                keep = [k for k, p in enumerate(packs) if len(p["freqs"]) == gl]
                packs = [packs[k] for k in keep]
                sweep_thr = [sweep_thr[k] for k in keep]
        multi = len(packs) >= 2

        # Ms-vs-throttle: each repeat sweep is a full sweep flown at its own throttle, so its Ms is
        # confound-free (unlike slicing one rising sweep, where throttle tracks frequency). If Ms climbs
        # from low to high throttle, the loop peaks under power (propwash zone) -> raise TPA up top.
        ms_throttle = []
        if multi and all(t is not None for t in sweep_thr):
            for p, t in zip(packs, sweep_thr):
                if p["ms"] is not None:
                    ms_throttle.append({"throttle_pct": round(t, 0), "ms": p["ms"],
                                        "f_ms_hz": p["f_ms"], "mt": p["mt"]})
            ms_throttle.sort(key=lambda r: r["throttle_pct"])
            # only meaningful if the sweeps actually span a throttle range (≥8 % low→high)
            if len(ms_throttle) < 2 or ms_throttle[-1]["throttle_pct"] - ms_throttle[0]["throttle_pct"] < 8:
                ms_throttle = []

        band_fields: dict = {}
        if multi:
            freqs = packs[0]["freqs"]
            gain_db, g_lo, g_hi = _curve_band([p["gain"] for p in packs])
            phase_deg, p_lo, p_hi = _curve_band([p["phase"] for p in packs])
            coh, c_lo, c_hi = _curve_band([p["coh"] for p in packs])
            fco, fco_lo, fco_hi = _med_range([p["fco"] for p in packs])
            margin, m_lo, m_hi = _med_range([p["margin"] for p in packs])
            m_unc, _, _ = _med_range([p["m_unc"] for p in packs])
            f_ms, fms_lo, fms_hi = _med_range([p["f_ms"] for p in packs])
            ms, ms_lo, ms_hi = _med_range([p["ms"] for p in packs])
            pm_ms, pmg_lo, pmg_hi = _med_range([p["pm_ms"] for p in packs])
            f_mt, fmt_lo, fmt_hi = _med_range([p["f_mt"] for p in packs])
            mt, mt_lo, mt_hi = _med_range([p["mt"] for p in packs])
            fb, gb, pb, cb, glo, ghi, plo, phi, clo, chi = _downsample(
                freqs, gain_db, phase_deg, coh, g_lo, g_hi, p_lo, p_hi, c_lo, c_hi,
                fmin=a_fmin, fmax=a_fmax)
            band_fields = {
                "n_sweeps": len(packs),
                "gain_band": [[round(float(v), 1) for v in glo], [round(float(v), 1) for v in ghi]],
                "phase_band": [[round(float(v), 1) for v in plo], [round(float(v), 1) for v in phi]],
                "coherence_band": [[round(float(v), 3) for v in clo], [round(float(v), 3) for v in chi]],
                "crossover_range": [fco_lo, fco_hi],
                "phase_margin_range": [m_lo, m_hi],
                "ms_range": [ms_lo, ms_hi],
                "f_ms_range": [fms_lo, fms_hi],
                "pm_guaranteed_range": [pmg_lo, pmg_hi],
                "mt_range": [mt_lo, mt_hi],
                "f_mt_range": [fmt_lo, fmt_hi],
            }
            step = _aggregate_step([p["step"] for p in packs], band_fields)
        else:
            freqs, gain_db, phase_deg, coh, H = _frf(x, y, fs, nperseg)
            fco, margin, m_unc = _phase_margin(freqs, gain_db, phase_deg, coh, a_fmin, a_fmax)
            f_ms, ms, pm_ms = _sensitivity_peak(freqs, H, coh, a_fmin, a_fmax)
            f_mt, mt = _comp_sensitivity_peak(freqs, H, coh, a_fmin, a_fmax)
            # Step response from the calibrated setpoint -> gyro (time-domain companion to the Bode).
            step = {}
            if spcol in df.columns:
                # closed-loop bandwidth is a few × the crossover; cap the step band well below the
                # full swept range so high-frequency noise doesn't fake ringing in the transient.
                sb = min(a_fmax, max(120.0, 6.0 * fco)) if fco else min(a_fmax, 150.0)
                step = _step_response(df.loc[np.asarray(mask), spcol].to_numpy(float), y, fs, band_fmax=sb)
            fb, gb, pb, cb = _downsample(freqs, gain_db, phase_deg, coh, fmin=a_fmin, fmax=a_fmax)

        peaks = _gain_peaks(freqs, gain_db, coh, a_fmin, a_fmax)
        results[axis] = {
            "input_col": xcol,
            "band_hz": [round(a_fmin, 1), round(a_fmax, 1)],
            "n_samples": int(mask.sum()),
            "freq": [round(float(v), 1) for v in fb],
            "gain_db": [round(float(v), 1) for v in gb],
            "phase_deg": [round(float(v), 1) for v in pb],
            "coherence": [round(float(v), 3) for v in cb],
            "peaks": peaks,
            "phase_margin_deg": margin,
            "phase_margin_unc_deg": m_unc,
            "crossover_hz": fco,
            "ms": ms,
            "f_ms_hz": f_ms,
            "pm_guaranteed_deg": pm_ms,
            "mt": mt,
            "f_mt_hz": f_mt,
            "step": step,
            "diagnosis": _diagnose(peaks, (fco, margin, m_unc), a_fmin, a_fmax),
            "step_diagnosis": _step_diagnosis(step.get("metrics", {})) if step else [],
            **({"ms_throttle": ms_throttle} if ms_throttle else {}),
            **band_fields,
        }

    throttle_map = {}
    noise = {}
    if primary_axis_idx is not None:
        throttle_map = _throttle_map(df, fs, primary_axis_idx, fmin, fmax, poles=motor_poles)
        # Noise PSD over each axis' chirp-free window (when that axis is NOT being excited),
        # so the renderer's per-axis chips can show roll/pitch/yaw, not just the swept axis.
        thr, idle, _ = _throttle_series(df)

        def quiet_for(i):
            if labels is not None:
                q = labels != i
            elif active is not None:
                q = ~active
            else:
                q = np.ones(len(df), dtype=bool)
            return q & (thr > idle) if thr is not None else q

        noise_axes = {}
        corners = _filter_corners(config, fs)
        for i, axis in enumerate(AXES):
            if GYRO_COL.format(i) not in df.columns:
                continue
            n = _noise_spectrum(df, fs, i, quiet_for(i), fmin=30.0, fmax=fmax, corners=corners)
            if n:
                noise_axes[axis] = n

        # Top-level noise keeps the primary axis' shape (back-compat for existing consumers);
        # the full per-axis set rides alongside under "axes". Shallow-copy so the primary entry
        # inside "axes" is not the same object as the parent (that would be a circular ref on dump).
        prim = noise_axes.get(AXES[primary_axis_idx])
        noise = dict(prim) if prim else {}
        if noise:
            noise["axes"] = noise_axes
            quiet_primary = quiet_for(primary_axis_idx)
            if motor_poles:
                mh = _motor_harmonics(df, quiet_primary, motor_poles, float(noise["freqs"][-1]))
                if mh:
                    noise["motor"] = mh
            # D-term / motor-output PSD: the HF oscillation that heats the ESCs (feature: shown
            # below the gyro spectrum when the D-term / motor channels were logged).
            dm = _dterm_motor_spectrum(df, fs, quiet_for, quiet_primary, fmin=30.0, fmax=fmax)
            if dm:
                noise["dterm"] = dm

    # Spectrogram of the primary axis over its chirp window -> the rising sweep. With several
    # sweeps on that axis we median them (cleaner ridge); a single sweep keeps the original path
    # verbatim, so single-sweep logs render byte-identically.
    spectro = {}
    if primary_axis_idx is not None:
        gcol = GYRO_COL.format(primary_axis_idx)
        act = (labels == primary_axis_idx) if labels is not None else active
        if act is not None and gcol in df.columns:
            idx = np.where(np.asarray(act))[0]
            sweeps = sweep_windows.get(primary_axis_idx, [])
            # crop to the swept band (+10%) so the diagonal fills the plot instead of empty HF
            sweptmax = (results[AXES[primary_axis_idx]]["band_hz"][1]) * 1.1
            gyro = df[gcol].to_numpy(float)
            if len(sweeps) >= 2:
                segs = [gyro[s:e] for s, e in sweeps]
                spectro = _spectrogram_median(segs, fs, fmax=min(fmax, sweptmax))
            elif idx.size:
                seg = gyro[int(idx[0]):int(idx[-1]) + 1]
                spectro = _spectrogram(seg, fs, fmax=min(fmax, sweptmax))
            if spectro:
                spectro["axis"] = AXES[primary_axis_idx]

    return results, throttle_map, noise, spectro


def _psd_resonances(throttle_map: dict) -> list[dict]:
    """Resonances in the throttle-averaged gyro PSD, flagged if they migrate with throttle.

    The throttle map is the curve behind the filtering advice: a peak that grows /
    shifts with throttle is motor/desync (RPM filter, dyn notch); a fixed peak is a
    frame resonance (static notch).
    """
    freqs = throttle_map.get("freqs")
    levels = throttle_map.get("levels_db")
    if not freqs or not levels:
        return []
    f = np.asarray(freqs, float)
    arr = np.array([[np.nan if v is None else v for v in row] for row in levels], float)
    mean_psd = np.nanmean(arr, axis=0)
    if not np.isfinite(mean_psd).any():
        return []
    # only look above ~70 Hz: below that is the closed-loop band, not a filter target
    lo = f >= 70.0
    if lo.sum() < 5:
        return []
    fb, mb = f[lo], mean_psd[lo]
    df = float(np.median(np.diff(fb))) or 1.0
    idx, props = sp_signal.find_peaks(mb, prominence=6.0, distance=max(1, int(20.0 / df)))
    out = []
    nrows = arr.shape[0]
    for k, i in enumerate(idx):
        gi = np.where(f == fb[i])[0]
        col = int(gi[0]) if gi.size else None
        migrates = False
        if col is not None and nrows >= 4:
            half = nrows // 2
            win = slice(max(0, col - 3), col + 4)
            low_pk = np.nanargmax(np.nanmean(arr[:half, win], axis=0)) if np.isfinite(arr[:half, win]).any() else 0
            high_pk = np.nanargmax(np.nanmean(arr[half:, win], axis=0)) if np.isfinite(arr[half:, win]).any() else 0
            migrates = abs(int(low_pk) - int(high_pk)) >= 2
        out.append({"freq_hz": round(float(fb[i]), 0),
                    "prominence_db": round(float(props["prominences"][k]), 1),
                    "migrates": bool(migrates)})
    out.sort(key=lambda p: p["prominence_db"], reverse=True)
    return out[:5]


def _filter_suggestions(throttle_map: dict, cfg: dict) -> list[dict]:
    """Filtering leads, each tied to a measured resonance frequency (evidence for the curve)."""
    res = _psd_resonances(throttle_map)
    dn = cfg.get("dyn_notch") or {}
    nmin, nmax = dn.get("min"), dn.get("max")
    cnt, q = dn.get("count"), dn.get("q")
    sug = []
    for r in res:
        f, pr = r["freq_hz"], r["prominence_db"]
        ofr = ("migre avec le throttle → moteur/desync (RPM filter, dyn notch)" if r["migrates"]
               else "stable en throttle → résonance de frame (notch statique)")
        oen = ("migrates with throttle → motor/desync (RPM filter, dyn notch)" if r["migrates"]
               else "throttle-stable → frame resonance (static notch)")
        if nmin is not None and nmax is not None and nmin <= f <= nmax:
            fr = (f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr} — dans la plage dyn_notch "
                  f"({nmin}-{nmax} Hz, ×{cnt}, Q={q}), donc déjà ciblée. Si elle reste visible, c'est que "
                  f"le notch ne la couvre pas assez (count ou Q insuffisant).")
            en = (f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen} — inside the dyn_notch range "
                  f"({nmin}-{nmax} Hz, ×{cnt}, Q={q}), so already targeted. If it remains visible, the notch "
                  f"isn't covering it enough (count or Q too low).")
        elif nmax is not None and f > nmax:
            fr = (f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr} — AU-DESSUS de dyn_notch_max ({nmax} Hz), "
                  f"donc hors de portée du notch : un dyn_notch_max plus haut (~{int(f + 50)}) la couvrirait.")
            en = (f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen} — ABOVE dyn_notch_max ({nmax} Hz), so beyond "
                  f"the notch's reach: a higher dyn_notch_max (~{int(f + 50)}) would cover it.")
        elif nmin is not None and f < nmin:
            fr = (f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr} — SOUS dyn_notch_min ({nmin} Hz), "
                  f"donc hors plage : un dyn_notch_min plus bas (~{max(60, int(f - 20))}) la prendrait.")
            en = (f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen} — BELOW dyn_notch_min ({nmin} Hz), so out of "
                  f"range: a lower dyn_notch_min (~{max(60, int(f - 20))}) would catch it.")
        else:
            fr = f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr}."
            en = f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen}."
        sug.append({"freq_hz": f, "fr": fr, "en": en})
    if not sug:
        sug.append({"freq_hz": None,
                    "fr": (f"Aucune résonance marquée (>70 Hz) dans la carte throttle : le filtrage en place "
                           f"(dyn_notch ×{cnt} Q={q}, RPM filter ×{cfg.get('rpm_harmonics')}) tient le spectre "
                           f"propre — voir le spectre de bruit pour la marge réelle d'assouplissement."),
                    "en": (f"No prominent resonance (>70 Hz) in the throttle map: the filtering in place "
                           f"(dyn_notch ×{cnt} Q={q}, RPM filter ×{cfg.get('rpm_harmonics')}) keeps the spectrum "
                           f"clean — see the noise spectrum for the actual room to loosen it.")})
    return sug


def _synthesis(axes: dict, noise: dict, config: dict, throttle_max: float | None = None) -> list[dict]:
    """Top-level 'read' of the whole report as linked observations (filter -> phase -> P/D).

    Data-driven: it states what the curves show and how the levers chain, without prescribing.
    """
    obs: list[dict] = []
    worst = _worst_residual_db(noise)       # largest filtered residual above the noise floor
    has_unfilt = (noise or {}).get("has_unfilt")
    margin_avail = worst is not None and worst <= RESIDUAL_OK_DB
    margins = {ax: d["phase_margin_deg"] for ax, d in axes.items() if d.get("phase_margin_deg") is not None}
    low = {ax: mv for ax, mv in margins.items() if mv < 35.0}
    low_str = ", ".join(f"{ax} {mv:.0f}°" for ax, mv in low.items())

    # 1) Filtering state — judged on the filtered residual above the floor (reference-stable)
    if worst is not None:
        if has_unfilt and margin_avail:
            obs.append({
                "fr": f"Filtrage — après filtres, le bruit retombe dans son plancher (résiduel max +{max(worst,0):.0f} dB) : "
                      f"le filtrage en place est plus fort que ne l'exige le bruit présent.",
                "en": f"Filtering — after filtering, the noise falls back into its floor (max residual +{max(worst,0):.0f} dB): "
                      f"current filtering is stronger than the present noise requires."})
        else:
            obs.append({
                "fr": f"Filtrage — il subsiste un résiduel à +{worst:.0f} dB au-dessus du plancher après filtres : "
                      f"le filtrage travaille encore, peu de marge pour l'alléger.",
                "en": f"Filtering — a +{worst:.0f} dB residual remains above the floor after filtering: the filters "
                      f"are still working, little room to loosen them."})

    # 2) The chain filter -> phase -> P/D
    if margin_avail and low:
        obs.append({
            "fr": f"Chaînage — ces marges de phase basses ({low_str}) sont aujourd'hui le facteur limitant. Alléger "
                  f"le filtrage réduit le retard de phase, donc relèverait d'abord ces marges ; et une marge "
                  f"regagnée, c'est ensuite du headroom pour monter P et D sans que la boucle oscille.",
            "en": f"Chain — these low phase margins ({low_str}) are the current limiting factor. Loosening the "
                  f"filtering cuts phase lag, so it would lift these margins first; and margin regained is then "
                  f"headroom to raise P and D without the loop oscillating."})
    elif low:
        obs.append({
            "fr": f"Chaînage — marges basses ({low_str}) mais peu de marge de filtrage : ici le levier direct est "
                  f"de réduire P/D plutôt que de toucher au filtre.",
            "en": f"Chain — low margins ({low_str}) but little filtering room: here the direct lever is reducing "
                  f"P/D rather than the filtering."})

    # 3) Throttle-coverage caveat
    if throttle_max is not None and throttle_max < 1450:
        obs.append({
            "fr": f"Réserve — ce log monte peu en gaz (~{throttle_max:.0f} sur 2000) : la marge de bruit est "
                  f"mesurée à bas régime, or le bruit moteur augmente avec le throttle. À confirmer avec une passe "
                  f"plus engagée avant d'alléger franchement le filtrage.",
            "en": f"Caveat — this log barely climbs in throttle (~{throttle_max:.0f} of 2000): the noise margin is "
                  f"measured at low rpm, and motor noise grows with throttle. Confirm with a more aggressive pass "
                  f"before loosening the filtering for real."})
    return obs


SCORE_WEIGHTS = {"overshoot": 0.25, "rise": 0.25, "margin": 0.18, "noise": 0.17,
                 "ms": 0.10, "track_err": 0.05}


def _ramp(v, good, bad):
    """Linear 0..100: 100 at the `good` end, 0 at the `bad` end, clamped. Direction-agnostic
    (good may be greater or smaller than bad). None in -> None out (term dropped from the blend)."""
    if v is None or good == bad:
        return None if v is None else (100.0 if v == good else 0.0)
    return float(max(0.0, min(1.0, (v - bad) / (good - bad))) * 100.0)


def _noise_margin_db(d):
    """Head-room before the loop gain would reach 0 dB in the HIGH band — the D-term ceiling.
    Measured well ABOVE the passband (where closed-loop gain sits at ~0 dB by design and must
    not be mistaken for a noise problem): the worst (highest) gain over freq > max(60, 2.5*f(Ms)).
    A buried roll-off (~-32 dB) scores well; a resonance climbing back toward 0 dB scores poorly.

    Worst-case by construction (a single bad peak sets it, not the average), and the resolved
    full-resolution resonances in d["peaks"] are folded in — the downsampled curve alone can
    smooth a narrow spike away. The peak COUNT is not captured here (scalar); list d["peaks"]
    separately to reason about multiple resonances."""
    freq, gain = d.get("freq") or [], d.get("gain_db") or []
    if not freq or not gain:
        return None
    pivot = d.get("f_ms_hz") or d.get("crossover_hz") or 24.0
    fref = max(60.0, 2.5 * pivot)
    cand = [gain[i] for i in range(len(freq)) if freq[i] > fref]
    if not cand:                                # band never reaches the HF region: use its top quarter
        cand = gain[max(1, len(gain) * 3 // 4):]
    # fold in full-resolution HF resonances (a narrow spike the downsampled curve missed)
    cand += [p["gain_db"] for p in (d.get("peaks") or []) if p.get("freq_hz", 0) > fref]
    return -max(cand) if cand else None


def _axis_score(d):
    """Per-axis composite. Returns {score, subs:{...}} or None if nothing is measurable."""
    sm = (d.get("step") or {}).get("metrics") or {}
    # Overshoot from the real-flight large-step (non-linear truth) when available, else the
    # linear chirp step. They never both feed the blend -> no double counting.
    ov = d.get("os_flight")
    if ov is None:
        ov = sm.get("overshoot_pct")
    subs = {
        "overshoot": _ramp(ov, 8.0, 22.0),                        # %: target <=8, ceiling ~15, bad >=22
        "rise":      _ramp(sm.get("rise_ms"), 15.0, 50.0),         # ms: faster better, floor 15, slow 50
        "margin":    _ramp(d.get("pm_guaranteed_deg"), 45.0, 20.0),# deg guaranteed: >=45 great, <20 risky
        "ms":        _ramp(d.get("ms"), 1.3, 2.2),                 # sensitivity peak: 1.3 healthy, >=2.2 bad
        "noise":     _ramp(_noise_margin_db(d), 32.0, 8.0),        # dB HF head-room: ~32 healthy, <=8 = D ceiling
        "track_err": _ramp(d.get("track_err_ratio"), 0.15, 0.5),  # normalised setpoint->gyro error: <=0.15 great
    }
    num = den = 0.0
    for k, w in SCORE_WEIGHTS.items():
        if subs[k] is not None:
            num += w * subs[k]; den += w
    if den == 0:
        return None
    return {"score": round(num / den, 1),
            "subs": {k: (round(v) if v is not None else None) for k, v in subs.items()}}


def _grade(score):
    """Letter grade tuned so ~75 reads as a solid B (FPV-realistic, not academic)."""
    if score is None:
        return "—"
    return next(g for thr, g in [(85, "A"), (70, "B"), (55, "C"), (40, "D"), (0, "F")] if score >= thr)


def _tune_score(results):
    """Overall = mean of the per-axis scores; carried in the pass so history gives the trend."""
    per = {ax: s for ax, d in results.items() if d for s in [_axis_score(d)] if s}
    if not per:
        return {}
    overall = round(sum(s["score"] for s in per.values()) / len(per), 1)
    return {"overall": overall, "grade": _grade(overall), "axes": per}


# ---------------------------------------------------------------------------
# Public entry: one decoded log -> one self-contained "pass" (was _build_pass,
# minus the CLI args Namespace and filesystem path — config is passed in).
# ---------------------------------------------------------------------------
def build_pass(df, fs, config, *, file="", input_col=DEFAULT_INPUT_COL,
               fmin=DEFAULT_FMIN, fmax=DEFAULT_FMAX, nperseg=None, axis=None) -> dict:
    """Run the analysis on one decoded log and package it as a self-contained 'pass'."""
    config = config or {}
    axes_filter = [axis] if axis else None
    results, throttle_map, noise, spectro = analyse(
        df, fs, input_col, axes_filter, fmin=fmin, fmax=fmax, nperseg=nperseg,
        motor_poles=config.get("motor_poles"), config=config)
    nyq = fs / 2.0
    throttle_max = None
    thr, idle, thr_src = _throttle_series(df)
    if thr is not None and thr_src == "rcCommand[3]":
        fly = thr[thr > idle]
        throttle_max = round(float(fly.max()), 0) if fly.size else None

    # Is this a chirp log? Legacy debug[1] axis flag, or the current-firmware debug[0] phase
    # channel. On a chirp log the closed-loop FRF/Bode/step is the authoritative step and the
    # real-flight step is hidden; on a NORMAL flight log the chirp FRF is meaningless (no
    # excitation -> coherence ~0) and the amplitude-binned flight step is the step to trust.
    _, _active = _reconstruct_exc(df)
    is_chirp = _has_axis_flag(df) or _active is not None

    # FRF reliability: without a real excitation the setpoint->gyro coherence collapses, so the
    # Bode / Ms / phase margin / chirp-step and the composite score are meaningless even though the
    # maths still produces confident-looking numbers. A chirp drives a CONTIGUOUS coherent band
    # (the swept region), so the discriminator is the *fraction* of the analysed band that clears
    # the coherence gate — high for a chirp (~0.1-0.3), near zero on normal flight. The renderer
    # masks the score + evolution and shows a warning banner when it is too low.
    _fracs = []
    for _d in results.values():
        if _d and _d.get("coherence") and _d.get("freq"):
            _f = np.asarray(_d["freq"], float); _c = np.asarray(_d["coherence"], float)
            _b = (_f >= fmin) & (_f <= min(fmax, nyq * 0.98))
            if _b.any():
                _fracs.append(float(np.mean(_c[_b] >= COHERENCE_GATE)))
    frf_coherent_frac = round(max(_fracs), 3) if _fracs else None
    frf_reliable = bool(frf_coherent_frac is not None and frf_coherent_frac >= 0.10)

    # P/I/D balance + tracking error (chirp-independent), and the real-flight step (normal logs only).
    pid_balance = _pid_balance(df, fs)
    step_flight = {}
    if not is_chirp:
        from . import step as _step
        try:
            step_flight = _step.analyse_flight(df, fs, axes_filter)
        except Exception:
            logger.exception("step_flight failed")
            step_flight = {}
    # Inject the score-feeding fields into each axis BEFORE tune_score reads them:
    # the normalised tracking error (always) and, on a normal log, the clean large-step overshoot.
    for ax, d in results.items():
        if not d:
            continue
        pb = pid_balance.get(ax)
        if pb and pb.get("err_ratio") is not None:
            d["track_err_ratio"] = pb["err_ratio"]
        # Only on a normal log, and only when the large step is CLEAN (rise time measurable AND
        # ≥15 stacked windows): otherwise a ringy deconvolution injects spurious overshoot. On a
        # chirp log the chirp-step overshoot stands.
        large = (step_flight.get(ax) or {}).get("large")
        lm = (large or {}).get("metrics") or {}
        if large and large.get("n", 0) >= 15 and lm.get("rise_time_ms") is not None \
                and lm.get("overshoot_pct") is not None:
            d["os_flight"] = lm["overshoot_pct"]

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": file,
        "sample_rate_hz": round(fs),
        "input_col": input_col,
        "band_hz": [fmin, round(min(fmax, nyq * 0.98), 1)],
        "throttle_max": throttle_max,
        "is_chirp": is_chirp,
        "frf_reliable": frf_reliable,
        "frf_coherent_frac": frf_coherent_frac,
        "config": config,
        "axes": results,
        "tune_score": _tune_score(results),
        "throttle_map": throttle_map,
        "noise_spectrum": noise,
        "filter_quality": _filter_quality_block(noise),
        "filter_model": _filter_model_block(config, fs, fmin, fmax),
        "pid_balance": pid_balance,
        "step_flight": step_flight,
        "spectrogram": spectro,
        "synthesis": _synthesis(results, noise, config, throttle_max),
        "filter_suggestions": _filter_suggestions(throttle_map, config) if config else [],
        "noise_suggestions": _noise_suggestions(noise) + _filter_disable_notes(noise, config),
        "tuning_suggestions": _tuning_suggestions(results, noise, config),
    }


# Public alias — display metric reused by CLI front-ends (skill _print_human).
noise_margin_db = _noise_margin_db
