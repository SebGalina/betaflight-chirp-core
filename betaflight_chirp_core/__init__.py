"""betaflight-chirp-core — Betaflight blackbox / chirp analysis core.

Single source of truth for the compute layer shared by the Betaflight skill
(vendored into its zip) and the FPVLogForge Oracle worker (pip dependency).

Entry: **bytes in, objects out.** No filesystem, no subprocess, no CLI, no MCP.

Available now (phase 2): decoding + header config.
    decode(bbl_bytes) -> (DataFrame, sample_rate_hz, config)

Coming next (phase 3): analyse_log(), build_report(), run().
"""
from __future__ import annotations

import pandas as pd

from . import config as _config
from . import decoder, signal

__all__ = ["decode", "decoder", "signal", "config"]
config = _config


def decode(bbl_bytes: bytes, session=None) -> tuple[pd.DataFrame, float, dict]:
    """Decode a `.bbl`/`.bfl` byte buffer.

    Returns (decoded DataFrame, estimated loop/log rate in Hz, tuning config
    parsed from the header — `{}` when the header carries no PID lines).
    """
    df = signal.decode_dataframe(bbl_bytes, session)
    fs = signal.sample_rate(df)
    cfg = _config.parse_header_config(bbl_bytes)
    return df, fs, cfg
