"""End-to-end coverage for the PIDscope-parity blocks added to the pass dict:
filter_model, pid_balance, step_flight, throttle_map.motor_orders, and the
track_err / flight-overshoot score wiring.
"""
import pathlib

import numpy as np
import pytest

from betaflight_chirp_core import decode, analyse_log
from betaflight_chirp_core.analysis import step as step_mod

DATA = pathlib.Path(__file__).parent / "data" / "8.bbl"


@pytest.fixture(scope="module")
def pass_dict():
    df, fs, cfg = decode(DATA.read_bytes())
    return analyse_log(df, fs, cfg)


def test_filter_model_present(pass_dict):
    fm = pass_dict["filter_model"]
    assert fm and "freqs" in fm
    assert fm["gyro"]["total_delay_ms"] > 0
    assert fm["dterm"]["total_delay_ms"] > 0
    # D-term path is filtered harder than gyro -> more delay (typical BF tune)
    assert fm["dterm"]["total_delay_ms"] >= fm["gyro"]["total_delay_ms"]


def test_pid_balance_present(pass_dict):
    pb = pass_dict["pid_balance"]
    assert set(pb) <= {"roll", "pitch", "yaw"} and pb
    for ax, e in pb.items():
        assert abs(e["pct_p"] + e["pct_i"] + e["pct_d"] - 100) <= 2
        assert e["rms_p"] >= 0 and e["err_rms"] >= 0


def test_chirp_log_hides_flight_step(pass_dict):
    # 8.bbl is a chirp log: the chirp-FRF step is authoritative, the flight step is suppressed,
    # and the FRF reads as reliable (a real swept coherent band).
    assert pass_dict["is_chirp"] is True
    assert pass_dict["step_flight"] == {}
    assert pass_dict["frf_reliable"] is True
    assert pass_dict["frf_coherent_frac"] >= 0.10


def test_normal_log_populates_flight_step():
    # Simulate a normal flight log by killing the chirp phase channel debug[0].
    df, fs, cfg = decode(DATA.read_bytes())
    df = df.copy()
    if "debug[0]" in df.columns:
        df["debug[0]"] = 0.0
    from betaflight_chirp_core.analysis.chirp import build_pass
    p = build_pass(df, fs, cfg, file="normal.bbl")
    assert p["is_chirp"] is False
    # no excitation -> FRF unreliable -> banner + score suppressed in the report
    assert p["frf_reliable"] is False
    assert p["frf_coherent_frac"] < 0.10
    sf = p["step_flight"]
    assert sf  # flight step now available
    for ax, bins in sf.items():
        for k in ("small", "large"):
            b = bins.get(k)
            if b is not None:
                assert b["n"] >= 3
                assert len(b["t_ms"]) == len(b["y"]) == len(b["y_lo"]) == len(b["y_hi"])
                # gated: a kept window settles near 1.0 and doesn't run away
                assert max(abs(v) for v in b["y"]) <= 3.0


def test_throttle_map_motor_orders(pass_dict):
    mo = pass_dict["throttle_map"].get("motor_orders")
    assert mo and len(mo) == len(pass_dict["throttle_map"]["throttle_bins"])
    vals = [v for v in mo if v is not None]
    # fundamental rises with throttle -> last bin >= first bin
    assert vals[-1] >= vals[0]


def test_score_has_track_err_sub(pass_dict):
    ts = pass_dict["tune_score"]
    for ax, s in ts["axes"].items():
        assert "track_err" in s["subs"]


def test_flight_step_excludes_chirp_windows():
    # With an exclude mask covering everything, no windows qualify -> empty.
    df, fs, _ = decode(DATA.read_bytes())
    full = np.ones(len(df), dtype=bool)
    out = step_mod.analyse_flight(df, fs, exclude_mask=full)
    assert out == {}
