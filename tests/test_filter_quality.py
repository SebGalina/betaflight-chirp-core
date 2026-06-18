"""Unit tests for the universal filter-quality score (_filter_quality).

Synthetic only — no .bbl fixture needed. A 1/f^2 colored-noise PSD with HF
harmonics stands in for the raw gyro; progressively stronger low-pass filtering
of that spectrum must drive the score down monotonically and place f_split in
the rising-noise region.
"""
from __future__ import annotations

import numpy as np

from betaflight_chirp_core.analysis import chirp


def _synthetic_psd(fs: float = 8000.0, n: int = 2049):
    """Return (f, raw_lin) — clean f^-1.8 background + a wide HF noise hump @240 Hz.

    A clean power-law background (no floor pedestal dragging the fit) plus a broad
    motor/frame hump so f_split detection triggers and the hybrid score spans 0..1.
    """
    f = np.linspace(0.0, fs / 2.0, n)
    bg = (f + 2.0) ** -1.8
    hump = 6e-4 * np.exp(-0.5 * ((f - 240.0) / 60.0) ** 2)
    raw = bg + hump + 1e-9
    return f, raw


def _lowpass(raw_lin: np.ndarray, f: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """2nd-order low-pass magnitude^2 applied to a PSD (simulates gyro filtering)."""
    h2 = 1.0 / (1.0 + (f / cutoff_hz) ** 4)
    return raw_lin * h2


def test_score_has_sweet_spot_and_usable_range():
    f, raw = _synthetic_psd()
    fs = 8000.0
    # hybrid score (harmonic mean of attenuation A and preservation P) is an
    # inverted-U: too light a filter -> low A, too heavy -> low P, best in between.
    cutoffs = [400.0, 200.0, 120.0, 80.0, 50.0, 30.0, 15.0]
    fqs = [chirp._filter_quality(f, raw, _lowpass(raw, f, c), fs) for c in cutoffs]
    assert all(q is not None for q in fqs)
    scores = [q["score"] for q in fqs]
    assert all(0.0 <= s <= 1.0 for s in scores)
    # peak sits in the interior, not at an extreme (under- or over-filtered ends)
    peak = scores.index(max(scores))
    assert 0 < peak < len(scores) - 1, scores
    # exploitable dynamic range across the sweep
    assert max(scores) - min(scores) > 0.5, scores
    # A rises and P falls as the filter gets stronger (cutoff drops)
    A = [q["score_attenuation"] for q in fqs]
    P = [q["score_preservation"] for q in fqs]
    assert A[0] < A[-1] and P[0] > P[-1], (A, P)


def test_recommendation_codes_cover_table():
    codes = {chirp._fq_reco(s) for s in (0.95, 0.8, 0.6, 0.4, 0.1)}
    assert codes == {"decrease_strong", "decrease_slight",
                     "sweet_spot", "increase_slight", "increase_strong"}


def test_fsplit_in_signal_region_or_fallback():
    f, raw = _synthetic_psd()
    fq = chirp._filter_quality(f, raw, _lowpass(raw, f, 150.0), 8000.0)
    assert fq is not None
    # the first emerging harmonic sits at 100 Hz; f_split should land near there
    # (or fall back cleanly to fs/4 = 2000 with the flag set)
    if fq["fallback"]:
        assert fq["f_split_hz"] == 2000.0
    else:
        assert 20.0 <= fq["f_split_hz"] <= 200.0


def test_returns_none_on_too_few_points():
    f = np.linspace(0.0, 4000.0, 10)
    raw = np.ones_like(f)
    assert chirp._filter_quality(f, raw, raw, 8000.0) is None


def test_fallback_no_verdict():
    """A pure power-law spectrum (no emerging hump) -> f_split fallback -> withheld verdict."""
    fs = 8000.0
    f = np.linspace(0.0, fs / 2.0, 2049)
    raw = (f + 2.0) ** -1.8 + 1e-9            # no emergence anywhere
    fq = chirp._filter_quality(f, raw, _lowpass(raw, f, 100.0), fs)
    assert fq is not None
    assert fq["fallback"] is True
    assert fq["confidence"] == "low"
    assert fq["recommendation"] == "insufficient_data"
    assert "reason" in fq
    # A/P/score are still computed and kept
    assert {"score", "score_attenuation", "score_preservation"} <= set(fq)


def test_aliasing_mask():
    """A peak in the aliasing zone (fs/2 - 20 Hz) must not become f_split."""
    fs = 8000.0
    f = np.linspace(0.0, fs / 2.0, 2049)
    raw = (f + 2.0) ** -1.8 + 1e-9
    f_alias = fs / 2.0 - 20.0
    raw = raw + 5e-4 * np.exp(-0.5 * ((f - f_alias) / 4.0) ** 2)   # narrow alias spike
    fq = chirp._filter_quality(f, raw, _lowpass(raw, f, 100.0), fs)
    assert fq is not None
    assert fq["masked_bins_count"] > 0
    assert abs(fq["f_split_hz"] - f_alias) > 50.0     # f_split did not land on the alias


def test_harmonic_mask():
    """A narrow motor harmonic at 180 Hz on a clean f^-1.8 must not bias alpha."""
    fs = 8000.0
    f = np.linspace(0.0, fs / 2.0, 2049)
    bg = (f + 2.0) ** -1.8 + 1e-9
    raw = bg + 4e-4 * np.exp(-0.5 * ((f - 180.0) / 3.0) ** 2)      # narrow spike
    fq = chirp._filter_quality(f, raw, _lowpass(raw, f, 100.0), fs)
    assert fq is not None
    assert fq["masked_bins_count"] > 0
    assert abs(fq["alpha"] - 1.78) <= 0.05, fq["alpha"]


def test_clean_signal_no_false_positive():
    """Ultra-clean signal (white-ish, flat) must not fabricate an emergence/verdict."""
    fs = 8000.0
    f = np.linspace(0.0, fs / 2.0, 2049)
    raw = np.full_like(f, 1e-3) + 1e-9        # flat: no power-law, no peaks
    fq = chirp._filter_quality(f, raw, _lowpass(raw, f, 100.0), fs)
    if fq is not None:
        assert fq["confidence"] == "low"
        assert fq["recommendation"] == "insufficient_data"
