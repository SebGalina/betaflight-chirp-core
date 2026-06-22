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
    """A pure power-law spectrum (no emerging hump) -> no excess -> honest null verdict.

    With no corner and no time signals, A is None (nothing emerged to attenuate) and P is
    None (the fallback band is meaningless), so the score is None and the verdict is the
    neutral 'loosen_candidate' — never a low number that would read as 'tighten'."""
    fs = 8000.0
    f = np.linspace(0.0, fs / 2.0, 2049)
    raw = (f + 2.0) ** -1.8 + 1e-9            # no emergence anywhere
    fq = chirp._filter_quality(f, raw, _lowpass(raw, f, 100.0), fs)
    assert fq is not None
    assert fq["fallback"] is True
    assert fq["confidence"] == "low"
    assert fq["recommendation"] == "loosen_candidate"
    assert fq["excess_present"] is False
    assert fq["score"] is None and fq["score_attenuation"] is None and fq["score_preservation"] is None
    assert "reason" in fq
    # keys are still present (the front-end reads them and renders n/a)
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
        assert fq["recommendation"] in {"loosen_candidate", "na_motion_dominated"}
        assert fq["score"] is None


# ── Phase 0: _filter_corners ─────────────────────────────────────────────────
def test_filter_corners_dynamic_uses_lower_bound():
    """A dynamic gyro_lpf1 [lo, hi] resolves its effective corner to the LOWER bound."""
    cfg = {"gyro_lpf1": {"dyn": [120, 250]}, "gyro_lpf2": {"static": 0},
           "dyn_notch": {"min": 90, "max": 600}}
    c = chirp._filter_corners(cfg, 8000.0)
    assert c is not None
    assert c["lpf1"] == 120                 # lower dynamic bound, not 250
    assert c["corner"] == 120.0
    assert c["notch_min"] == 90 and c["notch_max"] == 600


def test_filter_corners_lowest_lpf_wins():
    """corner = the lowest active low-pass stage (where the roll-off actually starts)."""
    cfg = {"gyro_lpf1": {"static": 250}, "gyro_lpf2": {"static": 180}}
    assert chirp._filter_corners(cfg, 8000.0)["corner"] == 180.0


def test_analytic_group_delay_decreases_with_higher_cutoff():
    """Analytic in-band group delay falls as the LPF cutoff rises (less filtering = less lag),
    and a higher-order type adds more lag — the physical ordering the FRF estimate failed on."""
    gd = lambda fc, order=1.0: chirp._lpf_group_delay_ms([(fc, order)], 90.0)
    assert gd(700) < gd(500) < gd(250) < gd(120)          # higher cutoff → less delay
    assert gd(250, 2.0) > gd(250, 1.0)                    # PT2 (order 2) > PT1
    assert chirp._lpf_group_delay_ms([], 90.0) is None    # no stage → None


def test_corners_group_delay_lower_cutoff_means_more_lag():
    """`_filter_corners` carries an analytic `group_delay_ms`; a lighter filter (higher gyro_lpf2)
    yields a smaller delay than a heavier one — the report5-vs-report6 case."""
    light = chirp._filter_corners({"gyro_lpf2": {"static": 700, "type": "PT1"}}, 8000.0)
    heavy = chirp._filter_corners({"gyro_lpf1": {"dyn": [200, 400], "type": "PT1"},
                                   "gyro_lpf2": {"static": 400, "type": "PT1"}}, 8000.0)
    assert light["group_delay_ms"] < heavy["group_delay_ms"]


def test_filter_corners_missing_keys_degrade():
    """No config / empty config / no usable cutoff -> None (clean degrade to the old path)."""
    assert chirp._filter_corners(None, 8000.0) is None
    assert chirp._filter_corners({}, 8000.0) is None
    assert chirp._filter_corners({"motor_poles": 14}, 8000.0) is None


# ── Phase 1: band anchored on the real corner ────────────────────────────────
def test_band_anchored_on_corner_no_fs4_fallback():
    """With a corner, a clean spectrum anchors f_split on the corner, never fs/4."""
    f, raw = _synthetic_psd()
    fs = 8000.0
    clean = (f + 2.0) ** -1.8 + 1e-9          # no emergence -> would fallback to fs/4 without a corner
    corners = {"corner": 150.0, "lpf1": 150, "lpf2": None, "notch_min": 90, "notch_max": 600}
    fq = chirp._filter_quality(f, clean, _lowpass(clean, f, 150.0), fs, corners=corners)
    assert fq is not None
    assert fq["fallback"] is False            # corner anchored, not fs/4
    assert fq["f_split_hz"] == 150.0
    assert fq["corner_hz"] == 150.0
    assert fq["f_ctrl_max_hz"] == 90.0        # min(corner, FQ_F_CTRL_MAX)


def test_ctrl_max_capped_at_hard_ceiling():
    """f_ctrl_max never exceeds FQ_F_CTRL_MAX even with a high corner."""
    f, raw = _synthetic_psd()
    corners = {"corner": 400.0, "lpf1": 400, "lpf2": None, "notch_min": None, "notch_max": None}
    fq = chirp._filter_quality(f, raw, _lowpass(raw, f, 400.0), 8000.0, corners=corners)
    assert fq["f_ctrl_max_hz"] == chirp.FQ_F_CTRL_MAX


# ── Phase 2: attenuation on the emergent excess ──────────────────────────────
def test_attenuation_none_when_nothing_emerges():
    """Clean power-law (no excess) -> A is None, not 1.0 (filter has nothing to attenuate)."""
    fs = 8000.0
    f = np.linspace(0.0, fs / 2.0, 2049)
    clean = (f + 2.0) ** -1.8 + 1e-9
    corners = {"corner": 150.0, "lpf1": 150, "lpf2": None, "notch_min": None, "notch_max": None}
    fq = chirp._filter_quality(f, clean, _lowpass(clean, f, 150.0), fs, corners=corners)
    assert fq["score_attenuation"] is None
    assert fq["excess_present"] is False


def test_attenuation_rises_with_stronger_filter_on_excess():
    """With a real hump, A (on the excess) rises as the filter cuts harder."""
    f, raw = _synthetic_psd()                 # hump @240
    fs = 8000.0
    corners = {"corner": 200.0, "lpf1": 200, "lpf2": None, "notch_min": None, "notch_max": None}
    a_light = chirp._filter_quality(f, raw, _lowpass(raw, f, 300.0), fs, corners=corners)["score_attenuation"]
    a_hard = chirp._filter_quality(f, raw, _lowpass(raw, f, 120.0), fs, corners=corners)["score_attenuation"]
    assert a_light is not None and a_hard is not None
    assert a_hard > a_light


# ── Phase 4/5: None propagation through the block ────────────────────────────
def test_block_handles_null_axes():
    """_filter_quality_block must not crash when some/all axes have null scores."""
    f, raw = _synthetic_psd()
    fs = 8000.0
    clean = (f + 2.0) ** -1.8 + 1e-9
    fq_null = chirp._filter_quality(f, clean, _lowpass(clean, f, 100.0), fs)          # score None
    fq_real = chirp._filter_quality(f, raw, _lowpass(raw, f, 120.0), fs)             # score defined
    # mixed: one real axis, one null axis
    block = chirp._filter_quality_block({"axes": {
        "roll": {"filter_quality": fq_real},
        "pitch": {"filter_quality": fq_null},
    }})
    assert block["mean"]["score"] is not None                  # the real axis carries the mean
    assert "recommendation" in block["mean"]
    # all-null: mean score None, neutral verdict, no crash
    block2 = chirp._filter_quality_block({"axes": {
        "roll": {"filter_quality": fq_null},
        "pitch": {"filter_quality": fq_null},
    }})
    assert block2["mean"]["score"] is None
    assert block2["mean"]["recommendation"] in {"loosen_candidate", "na_motion_dominated"}
