"""Chirp closed-loop frequency-response (Bode) analysis — the compute core.

Faithfully extracted from the Betaflight skill's chirp_analysis.py: same Welch
cross-spectral FRF, step response, throttle x frequency resonance map, gyro
noise spectrum, per-axis scoring and synthesis. No CLI, no I/O — operates on a
decoded DataFrame + sample rate, returns plain dicts.
"""
from __future__ import annotations

import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import signal as sp_signal

from ..signal import AXES, THROTTLE_COL, TIME_COL

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


def _throttle_map(df: pd.DataFrame, fs: float, axis_idx: int, fmin: float, fmax: float,
                  nbins: int = THROTTLE_BINS) -> dict:
    """PSD of gyro per throttle slice -> heatmap of how resonances migrate with throttle."""
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
    return {
        "axis": AXES[axis_idx],
        "source": src,
        "throttle_bins": centers,
        "freqs": [round(float(x), 1) for x in freqs_ref[::step]],
        "levels_db": [row[::step] for row in levels],
    }


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


def _noise_spectrum(df: pd.DataFrame, fs: float, axis_idx: int, quiet_mask: np.ndarray,
                    fmin: float = 30.0, fmax: float | None = None) -> dict:
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
        return f, 10.0 * np.log10(pxx + 1e-10)

    has_unfilt = ucol in df.columns
    f, raw = psd(ucol if has_unfilt else gcol)
    _, filt = psd(gcol)
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

    step = max(1, len(f) // 400)
    return {
        "axis": AXES[axis_idx], "has_unfilt": bool(has_unfilt),
        "freqs": [round(float(v), 1) for v in f[::step]],
        "raw_db": [round(float(v), 1) for v in raw[::step]],
        "filt_db": [round(float(v), 1) for v in filt[::step]],
        "peaks": peaks,
    }


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
    step = {}
    if sp_vals is not None:
        sb = min(a_fmax, max(120.0, 6.0 * fco)) if fco else min(a_fmax, 150.0)
        step = _step_response(sp_vals, y, fs, band_fmax=sb)
    return {"freqs": freqs, "gain": gain, "phase": phase, "coh": coh, "H": H,
            "fco": fco, "margin": margin, "m_unc": m_unc,
            "f_ms": f_ms, "ms": ms, "pm_ms": pm_ms, "step": step}


def analyse(df, fs, input_col, axes_filter=None, fmin=DEFAULT_FMIN, fmax=DEFAULT_FMAX,
            nperseg=None, motor_poles=None) -> dict:
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
        if len(sweeps) >= 2:
            for s, e in sweeps:
                sm = np.zeros(len(df), dtype=bool); sm[s:e] = axis_active[s:e]
                xs, _ = _resolve_input(df, exc, input_col, i, sm)
                if xs is None:
                    continue
                ys = df.loc[sm, gcol].to_numpy(float)
                sps = df.loc[sm, spcol].to_numpy(float) if spcol in df.columns else None
                packs.append(_frf_pack(xs, ys, sps, fs, nperseg, a_fmin, a_fmax))
            if packs:   # drop any short fragment whose Welch grid doesn't match the others
                gl = max(len(p["freqs"]) for p in packs)
                packs = [p for p in packs if len(p["freqs"]) == gl]
        multi = len(packs) >= 2

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
            }
            step = _aggregate_step([p["step"] for p in packs], band_fields)
        else:
            freqs, gain_db, phase_deg, coh, H = _frf(x, y, fs, nperseg)
            fco, margin, m_unc = _phase_margin(freqs, gain_db, phase_deg, coh, a_fmin, a_fmax)
            f_ms, ms, pm_ms = _sensitivity_peak(freqs, H, coh, a_fmin, a_fmax)
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
            "step": step,
            "diagnosis": _diagnose(peaks, (fco, margin, m_unc), a_fmin, a_fmax),
            "step_diagnosis": _step_diagnosis(step.get("metrics", {})) if step else [],
            **band_fields,
        }

    throttle_map = {}
    noise = {}
    if primary_axis_idx is not None:
        throttle_map = _throttle_map(df, fs, primary_axis_idx, fmin, fmax)
        # Noise PSD over the chirp-free window for this axis (when it is NOT being excited).
        if labels is not None:
            quiet = labels != primary_axis_idx
        elif active is not None:
            quiet = ~active
        else:
            quiet = np.ones(len(df), dtype=bool)
        thr, idle, _ = _throttle_series(df)
        if thr is not None:
            quiet = quiet & (thr > idle)
        noise = _noise_spectrum(df, fs, primary_axis_idx, quiet, fmin=30.0, fmax=fmax)
        if noise and motor_poles:
            mh = _motor_harmonics(df, quiet, motor_poles, float(noise["freqs"][-1]))
            if mh:
                noise["motor"] = mh

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


SCORE_WEIGHTS = {"overshoot": 0.25, "rise": 0.25, "margin": 0.20, "noise": 0.20, "ms": 0.10}


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
    subs = {
        "overshoot": _ramp(sm.get("overshoot_pct"), 8.0, 22.0),   # %: target <=8, ceiling ~15, bad >=22
        "rise":      _ramp(sm.get("rise_ms"), 15.0, 50.0),         # ms: faster better, floor 15, slow 50
        "margin":    _ramp(d.get("pm_guaranteed_deg"), 45.0, 20.0),# deg guaranteed: >=45 great, <20 risky
        "ms":        _ramp(d.get("ms"), 1.3, 2.2),                 # sensitivity peak: 1.3 healthy, >=2.2 bad
        "noise":     _ramp(_noise_margin_db(d), 32.0, 8.0),        # dB HF head-room: ~32 healthy, <=8 = D ceiling
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
        motor_poles=config.get("motor_poles"))
    nyq = fs / 2.0
    throttle_max = None
    thr, idle, thr_src = _throttle_series(df)
    if thr is not None and thr_src == "rcCommand[3]":
        fly = thr[thr > idle]
        throttle_max = round(float(fly.max()), 0) if fly.size else None
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": file,
        "sample_rate_hz": round(fs),
        "input_col": input_col,
        "band_hz": [fmin, round(min(fmax, nyq * 0.98), 1)],
        "throttle_max": throttle_max,
        "config": config,
        "axes": results,
        "tune_score": _tune_score(results),
        "throttle_map": throttle_map,
        "noise_spectrum": noise,
        "spectrogram": spectro,
        "synthesis": _synthesis(results, noise, config, throttle_max),
        "filter_suggestions": _filter_suggestions(throttle_map, config) if config else [],
        "noise_suggestions": _noise_suggestions(noise) + _filter_disable_notes(noise, config),
    }
