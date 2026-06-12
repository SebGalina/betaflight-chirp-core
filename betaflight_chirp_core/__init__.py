"""betaflight-chirp-core — Betaflight blackbox / chirp analysis core.

Single source of truth for the compute layer: vendor it into a tool's bundle
or import it as a pip dependency from any front-end.

Entry: **bytes in, objects out.** No filesystem, no subprocess, no CLI, no MCP.

    df, fs, config = decode(bbl_bytes)
    a_pass         = analyse_log(df, fs, config)        # one log -> one pass dict
    html           = build_report([a_pass])             # passes -> self-contained HTML
    result         = run(bbl_bytes)                     # decode + analyse + report, single pass

Importing this package is **light**: numpy/scipy/pandas are pulled lazily, only
when an analysis function runs. In particular ``from betaflight_chirp_core import
decoder`` stays stdlib-only — the `.bbl` frame decoder has no heavy deps, so
callers that only decode (e.g. the skill's analyze_blackbox) need nothing more.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass

__all__ = ["decode", "analyse_log", "assemble_report", "build_report", "run",
           "AnalysisResult", "decoder", "signal", "config", "analyse", "build_pass",
           "noise_margin_db"]


@dataclass
class AnalysisResult:
    """Full single-log result: the raw pass dict + the rendered HTML report."""
    metrics: dict      # the per-axis indicators the web front shows as-is
    report_html: str   # the self-contained HTML report (LLM path returns the link)
    raw: dict          # the complete pass dict (metrics + throttle map + noise + synthesis)


def decode(bbl_bytes: bytes, session=None):
    """Decode a `.bbl`/`.bfl` byte buffer.

    Returns (decoded DataFrame, estimated loop/log rate in Hz, tuning config
    parsed from the header — `{}` when the header carries no PID lines).
    """
    from . import config as _config
    from . import signal as _signal
    df = _signal.decode_dataframe(bbl_bytes, session)
    fs = _signal.sample_rate(df)
    cfg = _config.parse_header_config(bbl_bytes)
    return df, fs, cfg


def analyse_log(df, fs, config, **params) -> dict:
    """One decoded log -> one self-contained 'pass' dict.

    params: file, input_col, fmin, fmax, nperseg, axis (see analysis.chirp.build_pass).
    """
    from .analysis.chirp import build_pass
    return build_pass(df, fs, config, **params)


def assemble_report(passes, lang: str = "fr") -> dict:
    """Trim + annotate passes into the report dict CLI front-ends render themselves."""
    from .report import assemble_report as _assemble
    return _assemble(passes, lang)


def build_report(passes, lang: str = "fr") -> str:
    """Assemble + render one or more passes into a self-contained HTML report."""
    from .report import build_report as _build
    return _build(passes, lang)


def run(bbl_bytes: bytes, params: dict | None = None) -> AnalysisResult:
    """Full single-pass pipeline: decode + analyse + render, in one call."""
    params = dict(params or {})
    session = params.pop("session", None)
    df, fs, cfg = decode(bbl_bytes, session)
    a_pass = analyse_log(df, fs, cfg, **params)
    html = build_report([a_pass])
    return AnalysisResult(metrics=a_pass["axes"], report_html=html, raw=a_pass)


def __getattr__(name):
    # Lazy attribute access keeps `import betaflight_chirp_core` free of heavy deps.
    # (Submodules decoder/signal/config are imported automatically by `from ... import`.)
    if name in ("analyse", "build_pass", "noise_margin_db"):
        return getattr(importlib.import_module(".analysis.chirp", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
