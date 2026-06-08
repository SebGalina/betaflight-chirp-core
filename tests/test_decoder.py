"""Decode tests — run against any `.bbl`/`.bfl` fixtures dropped in tests/data/.

Real flight logs are git-ignored (they carry GPS home-point coordinates), so
these tests skip cleanly when no fixture is present.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from betaflight_chirp_core import decode, signal

DATA = Path(__file__).parent / "data"
LOGS = sorted(p for p in DATA.glob("*.bbl")) + sorted(p for p in DATA.glob("*.bfl"))

pytestmark = pytest.mark.skipif(not LOGS, reason="no .bbl/.bfl fixtures in tests/data/")


@pytest.fixture(params=LOGS, ids=lambda p: p.name)
def log_bytes(request) -> bytes:
    return request.param.read_bytes()


def test_decode_returns_frame_rate_and_config(log_bytes):
    df, fs, config = decode(log_bytes)
    assert len(df) > 512, "expected a non-trivial number of decoded frames"
    assert signal.TIME_COL in df.columns
    assert 100.0 < fs < 32_000.0, f"implausible loop rate {fs} Hz"
    assert isinstance(config, dict)


def test_gyro_and_time_columns_present(log_bytes):
    df, _, _ = decode(log_bytes)
    assert "gyroADC[0]" in df.columns
    # time must be monotonic non-decreasing (microseconds)
    t = df[signal.TIME_COL].to_numpy()
    assert (t[1:] >= t[:-1]).all()


def test_session_out_of_range_raises(log_bytes):
    with pytest.raises(ValueError):
        decode(log_bytes, session=999)
