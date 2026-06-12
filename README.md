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

# single call: decode + analyse + report in one shot (used by mcp_local + worker)
result = run(open("log.bbl", "rb").read())
result.metrics       # per-axis indicators the web front renders as-is
result.report_html   # the self-contained HTML report (LLM path returns its link)
```

Importing the package is **light**: numpy/scipy/pandas load lazily, only when an
analysis runs. `from betaflight_chirp_core import decoder` stays stdlib-only, so
decode-only callers pull no heavy deps.

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

Tests look for `.bbl` fixtures in `tests/data/` (git-ignored — **never commit a
real flight log**, it carries GPS home-point coordinates). Drop your own logs
there to run the decode tests.

## License

Apache-2.0.
