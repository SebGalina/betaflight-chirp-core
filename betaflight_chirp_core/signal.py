"""Shared signal-loading helpers — the common starting point for every analysis.

Ported from the skill's `blackbox_signal.py`, with one deliberate change: the
core takes **bytes**, never a filesystem path. `decode_dataframe` decodes a
`.bbl`/`.bfl` byte buffer in-process via the pure-Python `decoder` module; there
is no subprocess / temp-file fallback (the worker has no guaranteed filesystem).

Requires: numpy, pandas.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from . import decoder as bb

# Column names in the decoded frame (named after the log's field definitions).
TIME_COL = "time"
THROTTLE_COL = "rcCommand[3]"
AXES = ["roll", "pitch", "yaw"]


def decode_dataframe(raw: bytes, session=None) -> pd.DataFrame:
    """Decode a `.bbl`/`.bfl` byte buffer straight to a DataFrame, in-process.

    The frame is identical to what `analyze_blackbox --csv` writes: raw decoded
    main frames, columns named after the log's field definitions (`time`,
    `gyroADC[0]`, `setpoint[0]`, ...).

    Session selection is 1-based; when `session` is None the first *decodable*
    session is returned (a session whose header/data fail to parse is skipped).
    """
    ranges = bb.split_sessions(raw)
    if not ranges:
        raise ValueError(
            "No blackbox log section markers found — file may be corrupt "
            "or not a Betaflight log"
        )
    if session is not None:
        if session < 1 or session > len(ranges):
            raise ValueError(f"session {session} out of range (1..{len(ranges)})")
        candidates = [ranges[session - 1]]
    else:
        candidates = ranges

    last_err: Exception | None = None
    for start, end in candidates:
        parser = bb.FlightLogParser(raw, start, end)
        try:
            parser.parse_header()
            parser.parse_data()
        except Exception as exc:  # noqa: BLE001 — try the next session
            last_err = exc
            continue
        if parser.main_frames:
            return pd.DataFrame(parser.main_frames, columns=parser.main_field_names)
    raise ValueError(
        "no decodable session found"
        + (f": {last_err}" if last_err else "")
    )


def load_csv(source) -> pd.DataFrame:
    """Read a decoded CSV (skips leading '#' comment lines, trims headers).

    `source` may be a path, a file-like object, or raw CSV bytes/str.
    """
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    df = pd.read_csv(source, comment="#")
    df.columns = [c.strip() for c in df.columns]
    return df


def sample_rate(df: pd.DataFrame) -> float:
    """Estimate the loop/log rate (Hz) from the time column (microseconds)."""
    dt_us = float(np.median(np.diff(df[TIME_COL].values[:4000])))
    return 1_000_000.0 / dt_us


def active_mask(df: pd.DataFrame, throttle_min: int = 1100) -> np.ndarray:
    """Keep only frames where the craft is actually flying (throttle above idle).

    Noise and closed-loop behaviour are throttle-dependent; idle/disarmed samples
    would dilute the spectrum. Falls back to "keep everything" when throttle is
    absent from the log.
    """
    if THROTTLE_COL in df.columns:
        return df[THROTTLE_COL].values > throttle_min
    return np.ones(len(df), dtype=bool)
