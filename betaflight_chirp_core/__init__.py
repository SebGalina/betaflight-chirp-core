"""betaflight-chirp-core — Betaflight blackbox / chirp analysis core.

Single source of truth for the compute layer shared by the Betaflight skill
(vendored into its zip) and the FPVLogForge Oracle worker (pip dependency).

Entry: **bytes in, objects out.** No filesystem, no subprocess, no CLI, no MCP.

    df, fs, config = decode(bbl_bytes)
    a_pass         = analyse_log(df, fs, config)        # one log -> one pass dict
    html           = build_report([a_pass])             # passes -> self-contained HTML
    result         = run(bbl_bytes)                     # decode + analyse + report, single pass
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config as _config
from . import decoder, signal
from .analysis.chirp import analyse, build_pass, noise_margin_db
from .report import assemble_report, build_report

__all__ = ["decode", "analyse_log", "assemble_report", "build_report", "run",
           "AnalysisResult", "decoder", "signal", "config", "analyse", "build_pass",
           "noise_margin_db"]
config = _config


@dataclass
class AnalysisResult:
    """Full single-log result: the raw pass dict + the rendered HTML report."""
    metrics: dict      # the per-axis indicators the web front shows as-is
    report_html: str   # the self-contained HTML report (LLM path returns the link)
    raw: dict          # the complete pass dict (metrics + throttle map + noise + synthesis)


def decode(bbl_bytes: bytes, session=None) -> tuple[pd.DataFrame, float, dict]:
    """Decode a `.bbl`/`.bfl` byte buffer.

    Returns (decoded DataFrame, estimated loop/log rate in Hz, tuning config
    parsed from the header — `{}` when the header carries no PID lines).
    """
    df = signal.decode_dataframe(bbl_bytes, session)
    fs = signal.sample_rate(df)
    cfg = _config.parse_header_config(bbl_bytes)
    return df, fs, cfg


def analyse_log(df, fs, config, **params) -> dict:
    """One decoded log -> one self-contained 'pass' dict.

    params: file, input_col, fmin, fmax, nperseg, axis (see build_pass).
    """
    return build_pass(df, fs, config, **params)


def run(bbl_bytes: bytes, params: dict | None = None) -> AnalysisResult:
    """Full single-pass pipeline used by mcp_local and the Oracle worker."""
    params = dict(params or {})
    session = params.pop("session", None)
    df, fs, cfg = decode(bbl_bytes, session)
    a_pass = analyse_log(df, fs, cfg, **params)
    html = build_report([a_pass])
    return AnalysisResult(metrics=a_pass["axes"], report_html=html, raw=a_pass)
