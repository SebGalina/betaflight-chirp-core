"""Smoke/structure tests for the spectral and step analyses on real fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from betaflight_chirp_core import decode
from betaflight_chirp_core.analysis import spectral, step

DATA = Path(__file__).parent / "data"
LOGS = sorted(DATA.glob("*.bbl")) + sorted(DATA.glob("*.bfl"))

pytestmark = pytest.mark.skipif(not LOGS, reason="no .bbl/.bfl fixtures in tests/data/")


@pytest.fixture(params=LOGS, ids=lambda p: p.name)
def decoded(request):
    df, fs, _ = decode(request.param.read_bytes())
    return df, fs


def test_spectral_analyse(decoded):
    df, fs = decoded
    res = spectral.analyse(df, fs, "gyro", None,
                           fmin=spectral.DEFAULT_FMIN, fmax=spectral.DEFAULT_FMAX)
    assert res, "expected at least one axis"
    for axis, d in res.items():
        assert {"noise_floor_db", "band_rms", "peaks", "harmonic_series"} <= set(d)


def test_step_analyse(decoded):
    df, fs = decoded
    res = step.analyse(df, fs, None)
    assert res
    for axis, d in res.items():
        assert "overshoot_pct" in d
