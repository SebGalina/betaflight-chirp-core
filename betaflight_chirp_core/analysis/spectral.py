"""Betaflight noise spectrum / FFT peak & harmonic analysis.

Extracted from the skill's spectral_analysis.py. Compute only — main()/CLI stays in the skill.
(_plot_results imports matplotlib lazily; it is an optional extra, not a core dep.)
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy import signal as sp_signal
from scipy.integrate import trapezoid
from ..signal import AXES, THROTTLE_COL, TIME_COL, active_mask as _active_mask


SIGNALS = {
    # logical name -> per-axis column template
    "gyro":  "gyroADC[{}]",
    "dterm": "axisD[{}]",
}


DEFAULT_FMIN = 10.0


DEFAULT_FMAX = 1000.0


PEAK_PROMINENCE_DB = 6.0


HARMONIC_TOL = 0.06


MAX_HARMONIC = 5


def _auto_nperseg(fs: float) -> int:
    """Welch window targeting ~4 Hz frequency resolution, power of 2."""
    n = int(2 ** round(np.log2(max(fs / 4.0, 256))))
    return int(max(512, min(n, 8192)))


def _psd(sig: np.ndarray, fs: float, nperseg: int) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD, detrended. Returns (freqs, psd_linear)."""
    x = sp_signal.detrend(sig.astype(float))
    f, pxx = sp_signal.welch(x, fs=fs, nperseg=min(nperseg, len(x)), window="hann")
    return f, pxx


def _find_peaks(freqs: np.ndarray, psd_db: np.ndarray, floor_db: float,
                fmin: float, fmax: float) -> list[dict]:
    """Find spectral peaks rising PEAK_PROMINENCE_DB above the local floor."""
    band = (freqs >= fmin) & (freqs <= fmax)
    if not band.any():
        return []
    fb = freqs[band]
    db = psd_db[band]
    # minimum spacing of ~8 Hz between detected peaks
    df = float(np.median(np.diff(fb))) or 1.0
    distance = max(1, int(round(8.0 / df)))
    idx, props = sp_signal.find_peaks(
        db, prominence=PEAK_PROMINENCE_DB, distance=distance, height=floor_db
    )
    peaks = []
    for k, i in enumerate(idx):
        peaks.append({
            "freq_hz": round(float(fb[i]), 1),
            "level_db": round(float(db[i]), 1),
            "prominence_db": round(float(props["prominences"][k]), 1),
        })
    peaks.sort(key=lambda p: p["prominence_db"], reverse=True)
    return peaks


def _group_harmonics(peaks: list[dict]) -> list[dict]:
    """Group peaks into harmonic families (f0, 2·f0, 3·f0 …).

    The strongest peaks are tried as fundamentals first; a peak already claimed
    as a harmonic of a lower fundamental is not reused as a new fundamental.
    Returns a list of series; peaks not in any multi-member series are left out
    (reported separately as isolated peaks by the caller).
    """
    by_prom = sorted(peaks, key=lambda p: p["prominence_db"], reverse=True)
    claimed = set()
    series = []
    for cand in by_prom:
        f0 = cand["freq_hz"]
        if f0 in claimed or f0 < 30:   # below 30 Hz: treat as broadband, not a fundamental
            continue
        members = [cand]
        for n in range(2, MAX_HARMONIC + 1):
            target = n * f0
            best = None
            for p in peaks:
                if p["freq_hz"] in claimed or p is cand:
                    continue
                if abs(p["freq_hz"] - target) <= HARMONIC_TOL * target:
                    if best is None or p["prominence_db"] > best["prominence_db"]:
                        best = p
            if best is not None:
                members.append(best)
        if len(members) >= 2:
            for m in members:
                claimed.add(m["freq_hz"])
            members.sort(key=lambda p: p["freq_hz"])
            series.append({
                "fundamental_hz": f0,
                "n_harmonics": len(members),
                "members": members,
            })
    return series


def _band_rms(freqs, psd, fmin, fmax) -> float:
    """RMS amplitude integrated over [fmin, fmax] from the PSD."""
    band = (freqs >= fmin) & (freqs <= fmax)
    if not band.any():
        return 0.0
    power = trapezoid(psd[band], freqs[band])
    return float(np.sqrt(max(power, 0.0)))


def _diagnose(signal_name: str, peaks, series, floor_db, peak_db,
              isolated, broadband_ratio) -> list[str]:
    hints = []

    if series:
        s = series[0]
        hints.append(
            f"Harmonic family at {s['fundamental_hz']:.0f} Hz "
            f"(+{s['n_harmonics'] - 1} harmonic(s)) -> motor noise. "
            f"Fundamental ≈ RPM/60; fix with the RPM filter "
            f"(enable bidir DSHOT, raise Q, keep the 3rd harmonic)."
        )
    for p in isolated:
        hints.append(
            f"Isolated peak at {p['freq_hz']:.0f} Hz ({p['prominence_db']:.0f} dB) "
            f"-> likely a frame resonance; target it with a dynamic notch, not the RPM filter."
        )

    if not series and not isolated and broadband_ratio > 0.6:
        # No distinct peaks at all: don't alarm, but point the right way.
        hints.append(
            "No distinct peaks — energy is spread across the band. If you still "
            "feel/hear noise it is broadband (electrical / gyro): address it with a "
            "low-pass, never a notch."
        )
    elif broadband_ratio > 0.6 and (series or isolated):
        hints.append(
            "Most energy sits off the peaks (broad noise floor) — a low-pass helps "
            "more than chasing individual notches here."
        )

    if signal_name == "dterm":
        # D-term is the main noise path to the motors (PID sum ≈ D term).
        hints.append(
            f"D-term peak level {peak_db:.0f} dB above floor. "
            f"This is what reaches the ESCs — keep it low; if a peak dominates, "
            f"filter that frequency before raising D."
        )

    if not peaks:
        hints.append("No significant peaks — spectrum is clean in this band.")
    return hints


def analyse(df, fs, signal_name, axes_filter=None, fmin=DEFAULT_FMIN,
            fmax=DEFAULT_FMAX, nperseg=None) -> dict:
    mask = _active_mask(df)
    nyq = fs / 2.0
    fmax = min(fmax, nyq * 0.98)
    if nperseg is None:
        nperseg = _auto_nperseg(fs)

    template = SIGNALS[signal_name]
    results: dict = {}

    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue
        col = template.format(i)
        if col not in df.columns:
            continue
        sig = df.loc[mask, col].to_numpy(float)
        if len(sig) < 512:
            continue

        freqs, psd = _psd(sig, fs, nperseg)
        psd_db = 10.0 * np.log10(psd + 1e-12)

        band = (freqs >= fmin) & (freqs <= fmax)
        floor_db = float(np.median(psd_db[band])) if band.any() else float(np.median(psd_db))

        peaks = _find_peaks(freqs, psd_db, floor_db, fmin, fmax)
        series = _group_harmonics(peaks)
        claimed = {m["freq_hz"] for s in series for m in s["members"]}
        isolated = [p for p in peaks if p["freq_hz"] not in claimed]

        peak_db = (max(p["level_db"] for p in peaks) - floor_db) if peaks else 0.0
        # broadband ratio: how much band energy is NOT in the few sharp peaks
        total_rms = _band_rms(freqs, psd, fmin, fmax)
        peak_rms = 0.0
        for p in peaks:
            pf = p["freq_hz"]
            peak_rms += _band_rms(freqs, psd, pf - 6, pf + 6)
        broadband_ratio = 1.0 - min(peak_rms / total_rms, 1.0) if total_rms > 0 else 0.0

        results[axis] = {
            "noise_floor_db": round(floor_db, 1),
            "band_rms": round(total_rms, 2),
            "broadband_ratio": round(broadband_ratio, 2),
            "peaks": peaks,
            "harmonic_series": series,
            "isolated_peaks": isolated,
            "diagnosis": _diagnose(signal_name, peaks, series, floor_db,
                                   peak_db, isolated[:3], broadband_ratio),
            # downsampled spectrum for plotting / export (freq_hz, level_db)
            "spectrum": [
                [round(float(f), 1), round(float(d), 1)]
                for f, d in zip(freqs[band], psd_db[band])
            ],
        }

    return results


def _chart_payload(output: dict, file_name: str, max_pts: int = 300) -> dict:
    """Build a generate_line_chart payload (AntV mcp-server-chart).

    One series per axis via the `group` field; the spectrum is downsampled to
    at most `max_pts` points per axis to keep the payload light.
    """
    data = []
    for axis, d in output["axes"].items():
        spec = d.get("spectrum", [])
        step = max(1, len(spec) // max_pts)
        for f, db in spec[::step]:
            data.append({"time": f"{f:.1f}", "value": round(float(db), 1), "group": axis})
    return {
        "mcp_tool": "generate_line_chart",
        "note": "Pass `arguments` to the AntV mcp-server-chart generate_line_chart tool.",
        "arguments": {
            "title": f"Noise spectrum ({output['signal']}) — {file_name}",
            "axisXTitle": "Frequency (Hz)",
            "axisYTitle": "PSD (dB)",
            "data": data,
        },
    }


def _plot_results(df, fs, signal_name, output, fmin, fmax, title_suffix=""):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("matplotlib required for --plot: pip install matplotlib")

    COLORS = {"roll": "#1f77b4", "pitch": "#ff7f0e", "yaw": "#2ca02c"}
    axes_data = output["axes"]
    mask = _active_mask(df)
    template = SIGNALS[signal_name]

    fig, (ax_psd, ax_spec) = plt.subplots(
        2, 1, figsize=(11, 8), gridspec_kw={"height_ratios": [3, 2]}
    )
    fig.suptitle(f"Betaflight Noise Spectrum — {signal_name}{title_suffix}",
                 fontsize=13, fontweight="bold")

    # --- PSD panel with peak markers ---
    for axis, data in axes_data.items():
        pts = data.get("spectrum", [])
        if not pts:
            continue
        fs_arr, db = zip(*pts)
        ax_psd.plot(fs_arr, db, label=axis, color=COLORS.get(axis), linewidth=1.2)
        for s in data.get("harmonic_series", []):
            for m in s["members"]:
                ax_psd.annotate(f"{m['freq_hz']:.0f}", (m["freq_hz"], m["level_db"]),
                                fontsize=7, color=COLORS.get(axis),
                                ha="center", va="bottom")
        for p in data.get("isolated_peaks", []):
            ax_psd.plot(p["freq_hz"], p["level_db"], "v", color=COLORS.get(axis), markersize=6)

    ax_psd.set_xlim(fmin, fmax)
    ax_psd.set_xlabel("Frequency (Hz)")
    ax_psd.set_ylabel("PSD (dB)")
    ax_psd.set_title("Power spectral density — ▽ = isolated peak, numbers = harmonic family",
                     fontsize=10)
    ax_psd.legend(fontsize=9)
    ax_psd.grid(True, alpha=0.3)

    # --- Spectrogram panel (first analysed axis) ---
    first_axis = next(iter(axes_data), None)
    if first_axis is not None:
        col = template.format(AXES.index(first_axis))
        if col in df.columns:
            sig = sp_signal.detrend(df.loc[mask, col].to_numpy(float))
            f, t, Sxx = sp_signal.spectrogram(sig, fs=fs, nperseg=512, noverlap=384)
            sel = (f >= fmin) & (f <= fmax)
            ax_spec.pcolormesh(t, f[sel], 10 * np.log10(Sxx[sel] + 1e-12),
                               shading="gouraud", cmap="magma")
            ax_spec.set_ylim(fmin, fmax)
            ax_spec.set_xlabel("Time (s)")
            ax_spec.set_ylabel("Frequency (Hz)")
            ax_spec.set_title(f"Spectrogram — {first_axis} (frequency vs time)", fontsize=10)

    plt.tight_layout()
    plt.show()

# Public aliases for CLI front-ends.
chart_payload = _chart_payload
plot_results = _plot_results
