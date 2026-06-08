"""Analysis pipeline tests — decode -> analyse_log -> build_report, on real fixtures.

Skips cleanly when no `.bbl`/`.bfl` is present in tests/data/ (git-ignored).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from betaflight_chirp_core import analyse_log, build_report, decode, run

DATA = Path(__file__).parent / "data"
LOGS = sorted(DATA.glob("*.bbl")) + sorted(DATA.glob("*.bfl"))

pytestmark = pytest.mark.skipif(not LOGS, reason="no .bbl/.bfl fixtures in tests/data/")


@pytest.fixture(params=LOGS, ids=lambda p: p.name)
def raw(request) -> bytes:
    return request.param.read_bytes()


def test_pipeline(raw):
    df, fs, cfg = decode(raw)
    p = analyse_log(df, fs, cfg, file="x.bbl")
    assert {"axes", "tune_score", "noise_spectrum", "synthesis", "band_hz"} <= set(p)
    assert p["axes"], "expected at least one analysed axis"
    html = build_report([p])
    assert "<html" in html.lower() or html.lstrip().startswith("<!")
    assert len(html) > 10_000


def test_run_single_call(raw):
    res = run(raw, {"file": "x.bbl"})
    assert res.report_html and res.raw["axes"]
    assert res.metrics is res.raw["axes"]
