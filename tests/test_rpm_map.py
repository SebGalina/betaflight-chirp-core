"""Regression test: _rpm_map must not write into a read-only numpy view.

pandas 3.0 made copy-on-write unconditional, so `df[cols].to_numpy(float)` hands
back a NON-writeable array for every frame shape. `_rpm_map` masks its eRPM array
in place, which raised `ValueError: assignment destination is read-only` on any
log with eRPM once pandas 3 was installed (the core pins only `pandas>=2.0.0`).

Synthetic frame — no fixture needed, so this runs in every environment.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from betaflight_chirp_core.analysis.chirp import _rpm_map

FS = 2000.0
N = 4096          # > win*4 with win=512 at this sample rate
POLES = 14


def _frame() -> pd.DataFrame:
    """A decoded-looking frame whose to_numpy() views are read-only under pandas 3."""
    t = np.arange(N) / FS
    rng = np.random.default_rng(0)
    erpm = np.full(N, 60.0)                       # 60 * 100 eRPM -> 857 Hz fundamental at 14 poles
    cols = {"time": (t * 1e6).astype(np.int64)}
    for i in range(3):
        cols[f"gyroUnfilt[{i}]"] = rng.normal(0, 10, N)
        cols[f"gyroADC[{i}]"] = rng.normal(0, 10, N)
    for i in range(4):
        cols[f"eRPM[{i}]"] = erpm
    return pd.DataFrame(cols)


def test_rpm_map_does_not_write_into_a_readonly_view():
    df = _frame()
    assert not df[[f"eRPM[{i}]" for i in range(4)]].to_numpy(float).flags.writeable, (
        "this pandas returns a writeable view — the regression cannot be observed here"
    )
    _rpm_map(df, FS, 0, 1.0, 1000.0, poles=POLES)  # raised ValueError before the fix


def test_rpm_map_leaves_the_source_frame_untouched():
    """The in-place masking must not reach back into the caller's DataFrame."""
    df = _frame()
    before = df["eRPM[0]"].copy()
    _rpm_map(df, FS, 0, 1.0, 1000.0, poles=POLES)
    pd.testing.assert_series_equal(df["eRPM[0]"], before)
