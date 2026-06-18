# betaflight-chirp-core

[![PyPI](https://img.shields.io/pypi/v/betaflight-chirp-core)](https://pypi.org/project/betaflight-chirp-core/)
[![Python](https://img.shields.io/pypi/pyversions/betaflight-chirp-core)](https://pypi.org/project/betaflight-chirp-core/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

The compute core for Betaflight closed-loop **chirp** / blackbox analysis:
decode a `.bbl`/`.bfl`, estimate the frequency response (FRF/Bode), step
response and noise spectrum, and render a self-contained HTML report.

> `betaflight-chirp-core` knows nothing about MCP, HTTP, Docker, the CLI or the
> filesystem. **Input: bytes. Output: objects + HTML.**

## Install

```bash
pip install betaflight-chirp-core
```

Or pin an exact commit straight from git:

```bash
pip install "betaflight-chirp-core @ git+https://github.com/SebGalina/betaflight-chirp-core@v0.1.5"
```

## Usage

```python
from betaflight_chirp_core import decode, analyse_log, build_report, run

# low-level: decode -> analyse -> render, step by step
df, fs, config = decode(open("log.bbl", "rb").read())
# df: decoded frames (pandas)   fs: loop/log rate (Hz)   config: PID/filter settings
a_pass = analyse_log(df, fs, config)        # one log  -> one self-contained pass dict
html   = build_report([a_pass])             # passes   -> self-contained HTML report

# single call: decode + analyse + report in one shot
result = run(open("log.bbl", "rb").read())
result.metrics       # == result.raw["axes"] — per-axis indicators, render as-is
result.report_html   # the self-contained HTML report (a full <html> string)
result.raw           # the complete pass dict (see Output below)
```

Importing the package is **light**: numpy/scipy/pandas load lazily, only when an
analysis runs. `from betaflight_chirp_core import decoder` stays stdlib-only, so
decode-only callers pull no heavy deps.

## Output

Four return surfaces, from raw to ready-to-render:

| Call | Returns | Is |
|---|---|---|
| `decode(bytes)`        | `(df, fs, config)`           | pandas frames, log rate (Hz), header tune dict |
| `analyse_log(df,…)`    | **pass dict**                | one log → all indicators (below) |
| `build_report(passes)` | `str`                        | one self-contained `<html>` page |
| `run(bytes)`           | `AnalysisResult`             | `.metrics` (= `raw["axes"]`), `.report_html`, `.raw` (pass dict) |

### The pass dict

`analyse_log()` / `result.raw` — one self-contained analysis of one log:

```python
{
  "timestamp":   "2026-06-12T09:00:00",   # ISO, when analysed
  "file":        "log.bbl",
  "sample_rate_hz": 8000,                  # loop/log rate
  "input_col":   "debug[3]",               # FRF input column (chirp setpoint channel)
  "band_hz":     [1.0, 1000.0],            # analysed frequency band [fmin, fmax]
  "throttle_max": 1850,                    # peak flying throttle (or None)
  "config":      {…},                      # PID / filter settings parsed from the header
  "axes":        {"roll": {…}, "pitch": {…}, "yaw": {…}},   # per-axis, see below
  "tune_score":  {"overall": 76.0, "grade": "B", "axes": {"roll": {"score": …, "subs": {…}}}},
  "throttle_map":   {…},                   # resonance vs throttle (heatmap payload)
  "noise_spectrum": {…},                   # gyro PSD raw vs filtered (see below)
  "spectrogram":    {…},                   # chirp sweep time×freq (heatmap payload)
  "synthesis":      [{"fr": "...", "en": "..."}, …],   # plain-language read, bilingual
  "filter_suggestions": [ … ],             # filter change hints (only when config present)
  "noise_suggestions":  [ … ],             # noise/peak hints
}
```

**Per axis** (`axes["roll"]` etc.) — the Bode + step + verdict for one axis:

```python
{
  "band_hz": [1.0, 1000.0], "n_samples": 48000,
  "freq": [...], "gain_db": [...], "phase_deg": [...], "coherence": [...],  # Bode curves
  "peaks": [ … ],                          # gain-resonance peaks in band
  "crossover_hz": 32.0,                    # 0 dB crossover
  "phase_margin_deg": 41.0, "phase_margin_unc_deg": 6.0,
  "ms": 4.8, "f_ms_hz": 70.0, "pm_guaranteed_deg": 34.0,   # peak sensitivity (robustness)
  "mt": 1.2, "f_mt_hz": 28.0,              # peak complementary sensitivity max|T| (closed-loop resonance / delay robustness)
  "step": {                                # setpoint→gyro step response
    "t_ms": [...], "y": [...], "y_lo": [...], "y_hi": [...],
    "metrics": {"overshoot_pct": 12.0, "rise_ms": 18.0, "delay_ms": 3.0,
                "settle_ms": 60.0, "peak": 1.12},
  },
  "diagnosis": [ … ], "step_diagnosis": [ … ],   # short verdict strings
}
```

`tune_score.grade` is an **A–F** letter (`A` ≥ 85 … `F` < 40); `overall` is the mean
of the per-axis scores. `noise_spectrum` carries `freqs`, `raw_db`/`filt_db` curves
(0 dB = raw broadband floor) and a `peaks` list with `above_floor_db` / `resid_db`
(filtered residual) / `atten_db` (raw→filtered cut) per peak.

> Array fields (`freq`, `gain_db`, `*_db`, …) are JSON-ready (rounded floats), so the
> whole pass dict serialises straight to a front-end or a history store. For the exact
> nested fields, read `analysis/chirp.py:build_pass`.

## Layout

| Module | Role |
|---|---|
| `decoder.py`      | pure-Python `.bbl` frame decoder (stdlib only) |
| `signal.py`       | `decode_dataframe` (bytes → frames), `sample_rate`, `active_mask` |
| `config.py`       | PID / filter settings parsed from the header |
| `analysis/`       | chirp (FRF/Bode), spectral, step response |
| `report.py`       | self-contained HTML report (inlines the renderer assets) |
| `report_assets/`  | shared report renderer (`chirp_report.{js,css}` + glossary/strings JSON) — inlined by `report.py`, mountable by a web front |

## Develop

```bash
pip install -e ".[test]"
pytest
```

Tests run on `.bbl` fixtures in `tests/data/`. One GPS-free log (`8.bbl`) ships so
the suite runs out of the box; drop your own logs there for more coverage. Every
other `.bbl`/`.bfl` is git-ignored — **never commit a real flight log**, it
carries GPS home-point coordinates (only `8.bbl` is whitelisted, after verifying
it has no GPS frame).

## License

Apache-2.0.
