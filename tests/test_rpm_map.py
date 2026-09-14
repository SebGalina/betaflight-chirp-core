"""Regression test: _rpm_map must tolerate a read-only numpy view.

pandas 3.0 made copy-on-write unconditional, so `df[cols].to_numpy(float)` hands
back a NON-writeable array. `_rpm_map` masked its eRPM array in place, which
raised `ValueError: assignment destination is read-only` on any log carrying
eRPM — killing the whole report pipeline. The package pins only
`pandas>=2.0.0`, so a fresh install resolves pandas 3 and hits it.

pandas 2 always copies, so the fault is simply unreachable there — and the CI
matrix still runs 3.10, where pandas 3 is not installable. Rather than skip half
the matrix (a test that silently does nothing is worse than no test), the fixture
below imposes the pandas 3 contract on whatever pandas is installed. The test
then fails on the unfixed code and passes on the fixed code, on every version.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from betaflight_chirp_core.analysis.chirp import _rpm_map

FS = 2000.0
N = 4096          # > win*4 with win=512 at this sample rate
POLES = 14


@pytest.fixture
def readonly_to_numpy(monkeypatch):
    """Make every to_numpy() return a read-only view, as pandas >= 3 does.

    An explicit `copy=True` — what the fix passes — must still come back
    writeable, exactly as the real implementation behaves.
    """
    original = pd.DataFrame.to_numpy

    def patched(self, *args, **kwargs):
        out = original(self, *args, **kwargs)
        asked_for_copy = kwargs.get("copy", False) or (len(args) > 1 and bool(args[1]))
        if asked_for_copy:
            return out
        view = out.view()          # freeze the view, never the caller's buffer
        view.flags.writeable = False
        return view

    monkeypatch.setattr(pd.DataFrame, "to_numpy", patched)


def _frame() -> pd.DataFrame:
    """A decoded-looking frame: gyro + four eRPM channels at a steady 857 Hz fundamental."""
    t = np.arange(N) / FS
    rng = np.random.default_rng(0)
    cols = {"time": (t * 1e6).astype(np.int64)}
    for i in range(3):
        cols[f"gyroUnfilt[{i}]"] = rng.normal(0, 10, N)
        cols[f"gyroADC[{i}]"] = rng.normal(0, 10, N)
    for i in range(4):
        cols[f"eRPM[{i}]"] = np.full(N, 60.0)   # 60 * 100 eRPM -> 857 Hz at 14 poles
    return pd.DataFrame(cols)


def test_rpm_map_tolerates_a_readonly_to_numpy(readonly_to_numpy):
    df = _frame()
    assert not df[[f"eRPM[{i}]" for i in range(4)]].to_numpy(float).flags.writeable, (
        "fixture is not in effect — the regression would not be observable"
    )
    _rpm_map(df, FS, 0, 1.0, 1000.0, poles=POLES)  # raised ValueError before the fix


def test_rpm_map_leaves_the_source_frame_untouched():
    """The in-place masking must not reach back into the caller's DataFrame.

    Version-independent: `copy=True` guarantees this on every pandas, whereas
    before the fix it held only by accident, wherever pandas happened to copy.
    """
    df = _frame()
    before = df["eRPM[0]"].copy()
    _rpm_map(df, FS, 0, 1.0, 1000.0, poles=POLES)
    pd.testing.assert_series_equal(df["eRPM[0]"], before)
