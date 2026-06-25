"""Betaflight step response analysis (Welch cross-spectral method).

Extracted from the skill's step_response.py. Compute only — main()/CLI stays in the skill.
(_plot_results imports matplotlib lazily; it is an optional extra, not a core dep.)
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy import signal as sp_signal
from ..signal import AXES, THROTTLE_COL, TIME_COL, active_mask as _active_mask


SETPOINT_COLS = ["setpoint[0]", "setpoint[1]", "setpoint[2]"]


GYRO_COLS     = ["gyroADC[0]",  "gyroADC[1]",  "gyroADC[2]"]


def _active_only_mask(df: pd.DataFrame, fs: float, threshold_dps_per_s: float = 5000.0) -> np.ndarray:
    """
    Stricter mask: keep only frames around fast stick inputs.

    Computes |dSetpoint/dt| across all axes and keeps frames where the
    stick is moving fast enough to be informative for system identification.
    A 20 ms pre/post window is added around each active frame so the
    step response onset and tail are captured.
    """
    pad = int(fs * 0.020)  # 20 ms padding
    combined = np.zeros(len(df), dtype=bool)
    for col in SETPOINT_COLS:
        if col not in df.columns:
            continue
        sp = df[col].to_numpy(float)
        dsp = np.abs(np.gradient(sp, 1.0 / fs))
        combined |= dsp > threshold_dps_per_s

    # Dilate with pre/post window
    idx = np.where(combined)[0]
    for i in idx:
        lo = max(0, i - pad)
        hi = min(len(combined), i + pad + 1)
        combined[lo:hi] = True
    return combined


def _bandpass(sig: np.ndarray, fs: float, lo: float = 5.0, hi: float = 80.0) -> np.ndarray:
    """Apply a 4th-order Butterworth bandpass filter (lo–hi Hz)."""
    nyq = fs / 2.0
    sos = sp_signal.butter(4, [lo / nyq, hi / nyq], btype="band", output="sos")
    return sp_signal.sosfiltfilt(sos, sig)


def _welch_step_response(
    setpoint: np.ndarray,
    gyro: np.ndarray,
    fs: float,
    nperseg: int | None = None,
    regularize: float = 1e-5,
    bandpass: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Estimate step response from arbitrary input/output signals.

    Returns:
        times_ms  -time axis for the step response, in milliseconds
        step      -normalized step response (1.0 = steady state)
        freqs     -frequency axis (Hz) for the coherence output
        coherence -signal coherence per frequency band (quality indicator)
    """
    if nperseg is None:
        # ~64 ms window, rounded to nearest power of 2
        nperseg = int(2 ** round(np.log2(fs * 0.064)))
        nperseg = max(256, min(nperseg, 4096))

    # Detrend then optionally bandpass
    sp = sp_signal.detrend(setpoint.astype(float))
    gy = sp_signal.detrend(gyro.astype(float))
    if bandpass:
        sp = _bandpass(sp, fs)
        gy = _bandpass(gy, fs)

    # Welch's cross-PSD and auto-PSD
    f, Pxx = sp_signal.welch(sp, fs=fs, nperseg=nperseg, window="hann")
    _, Pxy = sp_signal.csd(sp, gy, fs=fs, nperseg=nperseg, window="hann")
    _, Cxy = sp_signal.coherence(sp, gy, fs=fs, nperseg=nperseg)

    # Transfer function estimate H(f) = Pxy / Pxx, regularised
    reg = regularize * float(np.max(np.abs(Pxx)))
    H = Pxy / (Pxx + reg)

    # Impulse response via IFFT, then integrate to step response
    h = np.fft.irfft(H)
    dt = 1.0 / fs
    step = np.cumsum(h) * dt

    # Normalise to steady state (mean of last 30 % of the window)
    n = len(step)
    ss = float(np.mean(step[int(0.7 * n):]))
    if abs(ss) > 1e-9:
        step = step / ss

    times_ms = np.arange(n) * dt * 1000.0
    return times_ms, step, f, Cxy


def _metrics(times_ms: np.ndarray, step: np.ndarray) -> dict:
    """
    Standard step response metrics.

    Rise time (10 %→90 %), overshoot %, settling time (±2 % band), delay (50 %).
    """
    n = len(step)
    ss = float(np.mean(step[int(0.7 * n):]))
    if abs(ss) < 1e-9:
        return {}

    sn = step / ss  # normalised to 1.0

    result: dict = {}

    # Delay -first crossing of 50 %
    idx_50 = int(np.argmax(sn >= 0.5))
    result["delay_ms"] = round(float(times_ms[idx_50]), 1) if idx_50 > 0 else None

    # Rise time -10 % to 90 %
    idx_10 = int(np.argmax(sn >= 0.1))
    idx_90 = int(np.argmax(sn >= 0.9))
    if idx_10 > 0 and idx_90 > idx_10:
        result["rise_time_ms"] = round(float(times_ms[idx_90] - times_ms[idx_10]), 1)
    else:
        result["rise_time_ms"] = None

    # Overshoot
    peak = float(np.max(sn))
    result["overshoot_pct"] = round((peak - 1.0) * 100.0, 1) if peak > 1.0 else 0.0

    # Settling time -last exit from ±2 % band
    out = np.where(np.abs(sn - 1.0) > 0.02)[0]
    if len(out):
        last = int(out[-1])
        result["settling_time_ms"] = round(
            float(times_ms[last + 1]) if last + 1 < n else float(times_ms[-1]), 1
        )
    else:
        result["settling_time_ms"] = round(float(times_ms[0]), 1)

    return result


def _diagnose(m: dict) -> list[str]:
    hints = []
    rt   = m.get("rise_time_ms")
    os_p = m.get("overshoot_pct", 0.0)
    st   = m.get("settling_time_ms")
    dl   = m.get("delay_ms")

    if rt is not None:
        if rt < 8:
            hints.append(f"Rise time very fast ({rt} ms) - risk of P oscillation or FF too high")
        elif rt < 15:
            hints.append(f"Rise time good ({rt} ms)")
        elif rt < 30:
            hints.append(f"Rise time acceptable ({rt} ms) - raise FF slightly if response feels sluggish")
        else:
            hints.append(f"Slow rise ({rt} ms) - raise FF first, then P")

    if os_p > 25:
        hints.append(f"High overshoot {os_p:.0f} % - lower P or raise D")
    elif os_p > 12:
        hints.append(f"Moderate overshoot {os_p:.0f} % - consider raising D slightly")
    elif os_p < 2 and (rt or 0) > 25:
        hints.append("Underdamped -raise P or FF")
    else:
        hints.append(f"Overshoot clean ({os_p:.0f} %)")

    if rt is not None and st is not None and (st - rt) > 50:
        hints.append(f"Long settling ({st} ms) - I too high or iterm_relax_cutoff too high")

    if dl is not None:
        if dl > 15:
            hints.append(f"High latency ({dl} ms) - filters may be too aggressive")
        elif dl < 5:
            hints.append(f"Low latency ({dl} ms) - filters are light (good)")

    return hints or ["Step response looks clean"]


def _deconv_window(sp: np.ndarray, gy: np.ndarray, fs: float,
                   horizon_ms: float, reg: float = 1e-4) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Wiener deconvolution of one window: step = cumsum(irfft(Sgy·conj(Ssp)/(|Ssp|²+reg))).

    Returns (t_ms, step_norm, steady) trimmed to horizon_ms, or None if unusable. The step is
    normalised to its steady state (mean of the last 30% of the FULL window before trimming).
    """
    sp = sp_signal.detrend(sp.astype(float))
    gy = sp_signal.detrend(gy.astype(float))
    Ssp = np.fft.rfft(sp)
    Sgy = np.fft.rfft(gy)
    denom = (np.abs(Ssp) ** 2)
    H = (Sgy * np.conj(Ssp)) / (denom + reg * float(np.max(denom) or 1.0))
    h = np.fft.irfft(H, n=len(sp))
    step = np.cumsum(h)
    n = len(step)
    steady = float(np.mean(step[int(0.7 * n):]))
    if abs(steady) < 1e-9:
        return None
    step = step / steady
    t_ms = np.arange(n) * 1000.0 / fs
    keep = t_ms <= horizon_ms
    return t_ms[keep], step[keep], steady


def analyse_flight(
    df: pd.DataFrame,
    fs: float,
    axes_filter=None,
    win_s: float = 1.0,
    horizon_ms: float = 150.0,
    small_band=(20.0, 150.0),
    large_min: float = 250.0,
    exclude_mask=None,
) -> dict:
    """Amplitude-binned, stacked step response from normal flight (PIDtoolbox-style).

    Unlike the chirp-FRF step (a single linear closed-loop response), this slices the flight
    into windows, deconvolves each setpoint->gyro window, and stacks them in two amplitude bins
    (small vs large stick steps). The split reveals non-linearity the linear FRF step hides:
    feedforward saturation, anti-gravity, iterm-relax. Each bin returns the median curve + a
    min/max confidence band built from the real windows (not a coherence proxy).

    Returns {axis: {"small": {...}|None, "large": {...}|None}} ; an empty axis dict means too
    little stick activity. Each bin: {t_ms, y, y_lo, y_hi, n, metrics}.
    """
    win = int(fs * win_s)
    if win < 256:
        return {}
    # On a chirp log the swept setpoint contaminates the deconvolution; drop any window that
    # overlaps the excited region so only genuine pilot steps feed the bins (and the score).
    excl = None if exclude_mask is None else np.asarray(exclude_mask, dtype=bool)
    out: dict = {}
    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue
        sp_col, gy_col = SETPOINT_COLS[i], GYRO_COLS[i]
        if sp_col not in df.columns or gy_col not in df.columns:
            continue
        sp_all = df[sp_col].to_numpy(float)
        gy_all = df[gy_col].to_numpy(float)
        bins: dict = {"small": [], "large": []}
        # non-overlapping windows; bin by peak |setpoint| in the window
        for s in range(0, len(sp_all) - win, win):
            if excl is not None and bool(excl[s:s + win].any()):
                continue
            sp_w = sp_all[s:s + win]
            peak = float(np.max(np.abs(sp_w)))
            if small_band[0] <= peak <= small_band[1]:
                key = "small"
            elif peak >= large_min:
                key = "large"
            else:
                continue
            r = _deconv_window(sp_w, gy_all[s:s + win], fs, horizon_ms)
            if r is None:
                continue
            t_ms, step, steady = r
            # Validity gate: keep only windows that deconvolved to a genuine step — positive DC
            # gain, a tail that has settled near 1.0, and no runaway ringing. Without this, noisy /
            # non-step windows (a chirp log has many) produce inverted or wildly ringing curves whose
            # median is meaningless. A bin that fails to gather enough clean steps falls back to None.
            if steady <= 0:
                continue
            tail = step[int(0.8 * len(step)):]
            if tail.size == 0 or not (0.6 <= float(np.mean(tail)) <= 1.6):
                continue
            if float(np.max(np.abs(step))) > 3.0:
                continue
            bins[key].append(step)
        axis_out: dict = {}
        t_ref = np.arange(min(int(horizon_ms * fs / 1000.0) + 1, win)) * 1000.0 / fs
        for key, stack in bins.items():
            stack = [s for s in stack if len(s) == len(t_ref)]
            if len(stack) < 3:
                axis_out[key] = None
                continue
            arr = np.vstack(stack)
            med = np.median(arr, axis=0)
            lo = np.percentile(arr, 20, axis=0)
            hi = np.percentile(arr, 80, axis=0)
            axis_out[key] = {
                "t_ms": [round(float(v), 1) for v in t_ref],
                "y": [round(float(v), 3) for v in med],
                "y_lo": [round(float(v), 3) for v in lo],
                "y_hi": [round(float(v), 3) for v in hi],
                "n": len(stack),
                "metrics": _metrics(t_ref, med),
            }
        if any(axis_out.values()):
            out[axis] = axis_out
    return out


def analyse(
    df: pd.DataFrame,
    fs: float,
    axes_filter=None,
    bandpass: bool = False,
    active_only: bool = False,
    nperseg: int | None = None,
) -> dict:
    if active_only:
        mask = _active_only_mask(df, fs)
    else:
        mask = _active_mask(df)
    results: dict = {}

    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue
        sp_col = SETPOINT_COLS[i]
        gy_col = GYRO_COLS[i]
        if sp_col not in df.columns or gy_col not in df.columns:
            continue

        sp = df.loc[mask, sp_col].to_numpy(float)
        gy = df.loc[mask, gy_col].to_numpy(float)
        if len(sp) < 512:
            continue

        times_ms, step, freqs, coh = _welch_step_response(
            sp, gy, fs, nperseg=nperseg, bandpass=bandpass
        )
        m = _metrics(times_ms, step)

        # Mean coherence in the PID-relevant band (5–80 Hz)
        band = (freqs >= 5) & (freqs <= 80)
        mean_coh = float(np.mean(coh[band])) if band.any() else float(np.mean(coh))

        coh_band = (freqs >= 5) & (freqs <= 80)
        results[axis] = {
            **m,
            "mean_coherence": round(mean_coh, 3),
            "diagnosis": _diagnose(m),
            # first 80 ms of step response curve (time_ms, normalised_value)
            "step_response": [
                [round(float(t), 2), round(float(s), 4)]
                for t, s in zip(times_ms, step)
                if t <= 80.0
            ],
            # coherence curve in the PID-relevant band (freq_hz, coherence)
            "coherence_curve": [
                [round(float(f), 2), round(float(c), 4)]
                for f, c in zip(freqs[coh_band], coh[coh_band])
            ],
        }

    return results


def _chart_payload(output: dict, file_name: str) -> dict:
    """Build a generate_line_chart payload (AntV mcp-server-chart).

    One series per axis via the `group` field, from the step-response curve.
    """
    data = []
    for axis, d in output["axes"].items():
        for t, v in d.get("step_response", []):
            data.append({"time": f"{t:.2f}", "value": round(float(v), 4), "group": axis})
    return {
        "mcp_tool": "generate_line_chart",
        "note": "Pass `arguments` to the AntV mcp-server-chart generate_line_chart tool.",
        "arguments": {
            "title": f"Step response — {file_name}",
            "axisXTitle": "Time (ms)",
            "axisYTitle": "Normalized response (1.0 = steady state)",
            "data": data,
        },
    }


def _plot_results(output: dict, title_suffix: str = "") -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("matplotlib required for --plot: pip install matplotlib")

    COLORS = {"roll": "#1f77b4", "pitch": "#ff7f0e", "yaw": "#2ca02c"}
    axes_data = output["axes"]

    fig, (ax_step, ax_coh) = plt.subplots(
        2, 1, figsize=(11, 8), gridspec_kw={"height_ratios": [3, 2]}
    )
    fig.suptitle(f"Betaflight Step Response{title_suffix}", fontsize=13, fontweight="bold")

    # --- Step response panel ---
    ax_step.axhline(1.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)
    ax_step.axhspan(0.98, 1.02, color="gray", alpha=0.12, label="±2 % settling band")
    max_val = 1.5
    for axis, data in axes_data.items():
        pts = data.get("step_response", [])
        if not pts:
            continue
        ts, vs = zip(*pts)
        max_val = max(max_val, max(vs))
        label = (
            f"{axis}  "
            f"rise={data.get('rise_time_ms', '?')} ms  "
            f"OS={data.get('overshoot_pct', 0):.0f}%  "
            f"coh={data.get('mean_coherence', 0):.2f}"
        )
        ax_step.plot(ts, vs, label=label, color=COLORS.get(axis), linewidth=1.8)

    ax_step.set_xlim(0, 80)
    ax_step.set_ylim(-0.15, min(max_val * 1.1, 2.0))
    ax_step.set_xlabel("Time (ms)")
    ax_step.set_ylabel("Normalized response")
    ax_step.set_title("Step response (setpoint → gyro)", fontsize=10)
    ax_step.legend(fontsize=9)
    ax_step.grid(True, alpha=0.3)

    # --- Coherence panel ---
    ax_coh.axhline(0.7, color="green",  linestyle="--", linewidth=0.9, alpha=0.8, label="0.70 — reliable")
    ax_coh.axhline(0.5, color="orange", linestyle="--", linewidth=0.9, alpha=0.8, label="0.50 — noisy")
    for axis, data in axes_data.items():
        pts = data.get("coherence_curve", [])
        if not pts:
            continue
        fs_arr, cs = zip(*pts)
        ax_coh.plot(fs_arr, cs, label=axis, color=COLORS.get(axis), linewidth=1.5)

    ax_coh.set_xlim(5, 80)
    ax_coh.set_ylim(0, 1.05)
    ax_coh.set_xlabel("Frequency (Hz)")
    ax_coh.set_ylabel("Coherence")
    ax_coh.set_title("Signal coherence (5–80 Hz)", fontsize=10)
    ax_coh.legend(fontsize=9)
    ax_coh.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

# Public aliases for CLI front-ends.
chart_payload = _chart_payload
plot_results = _plot_results
active_only_mask = _active_only_mask
