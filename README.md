# betaflight-chirp-core

The compute core for Betaflight closed-loop **chirp** / blackbox analysis:
decode a `.bbl`/`.bfl`, estimate the frequency response (FRF/Bode), step
response and noise spectrum, and render a self-contained HTML report.

Written **once**, consumed as a thin shell everywhere:

- the **Betaflight skill** (public) vendors this package into its zip;
- the **FPVLogForge** Oracle worker (private) imports it as a pip dependency.

> `betaflight-chirp-core` knows nothing about MCP, HTTP, Docker, the CLI or the
> filesystem. **Input: bytes. Output: objects + HTML.**

## Install

```bash
pip install "betaflight-chirp-core @ git+https://github.com/SebGalina/betaflight-chirp-core@v0.1.0"
```

## Usage

```python
from betaflight_chirp_core import decode

df, fs, config = decode(open("log.bbl", "rb").read())
# df: decoded frames (pandas)   fs: loop/log rate (Hz)   config: PID/filter settings
```

Coming next (phase 3): `analyse_log(df, fs, config, **params)`,
`build_report(passes)`, and the single-call `run(bbl_bytes)`.

## Layout

| Module | Role |
|---|---|
| `decoder.py` | pure-Python `.bbl` frame decoder (stdlib only) |
| `signal.py`  | `decode_dataframe` (bytes → frames), `sample_rate`, `active_mask` |
| `config.py`  | PID / filter settings parsed from the header |
| `analysis/`  | chirp (FRF/Bode), spectral, step response *(phase 3)* |
| `report.py`  | self-contained HTML report *(phase 3)* |

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
