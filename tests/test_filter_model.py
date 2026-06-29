"""Filter-model sanity: cutoffs, delay budget, predicted magnitude."""
import numpy as np
import pytest

from betaflight_chirp_core.analysis import filter_model as fm

FS = 8000.0


def _cfg(**over):
    base = {
        "gyro_lpf1": {"dyn": None, "static": 150, "type": "PT1"},
        "gyro_lpf2": {"static": None, "type": "PT1"},
        "dterm_lpf1": {"dyn": None, "static": 100, "type": "PT1"},
        "dterm_lpf2": {"static": None, "type": "PT1"},
        "dyn_notch": {"count": 3, "q": 300, "min": 80, "max": 600},
    }
    base.update(over)
    return base


def test_empty_config_returns_empty():
    assert fm.build_filter_model({}, FS) == {}
    assert fm.build_filter_model(None, FS) == {}


def test_pt1_minus3db_at_cutoff():
    fc = 150.0
    b, a = fm._pt1_ba(fc, FS)
    db = fm._mag_db(b, a, np.array([fc]), FS)[0]
    assert -4.0 < db < -2.0  # ~-3 dB at the cutoff


def test_ptn_correction_keeps_minus3db_at_configured_cutoff():
    # A PT2 at fc should still be ~-3 dB at fc thanks to the cutoff correction.
    fc = 120.0
    b, a = fm._ptn_ba(fc, FS, 2)
    db = fm._mag_db(b, a, np.array([fc]), FS)[0]
    assert -4.5 < db < -2.0


def test_higher_order_means_more_delay():
    fc = 120.0
    d1 = fm._stage_delay_ms(*fm._ptn_ba(fc, FS, 1), FS)
    d2 = fm._stage_delay_ms(*fm._ptn_ba(fc, FS, 2), FS)
    d3 = fm._stage_delay_ms(*fm._ptn_ba(fc, FS, 3), FS)
    assert 0 < d1 < d2 < d3


def test_lower_cutoff_means_more_delay():
    d_low = fm._stage_delay_ms(*fm._pt1_ba(80.0, FS), FS)
    d_high = fm._stage_delay_ms(*fm._pt1_ba(300.0, FS), FS)
    assert d_low > d_high > 0


def test_model_shape_and_budget():
    m = fm.build_filter_model(_cfg(), FS)
    assert set(m) >= {"freqs", "gyro", "dterm", "delay_ref_hz"}
    assert len(m["freqs"]) == len(m["gyro"]["mag_db"]) == len(m["dterm"]["mag_db"])
    # gyro path: LPF1 + dyn_notch stages present, positive total delay
    names = [s["name"] for s in m["gyro"]["stages"]]
    assert "gyro LPF1" in names and "dyn_notch" in names
    assert m["gyro"]["total_delay_ms"] > 0
    assert m["dterm"]["total_delay_ms"] > 0


def test_predicted_magnitude_attenuates_high_freq():
    m = fm.build_filter_model(_cfg(), FS)
    f = np.array(m["freqs"])
    g = np.array(m["gyro"]["mag_db"])
    lo = g[f < 50].mean()
    hi = g[f > 500].mean()
    assert lo > hi  # high frequency is cut relative to the passband
    assert abs(lo) < 1.0  # passband near 0 dB


def test_dyn_lpf_uses_range_mean():
    cfg = _cfg(gyro_lpf1={"dyn": [120, 250], "static": None, "type": "PT1"})
    m = fm.build_filter_model(cfg, FS)
    fc = next(s["fc_hz"] for s in m["gyro"]["stages"] if s["name"] == "gyro LPF1")
    assert fc == pytest.approx(185.0, abs=1.0)
