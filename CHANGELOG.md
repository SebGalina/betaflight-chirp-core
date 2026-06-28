# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-06-28

Merge of the PIDscope-parity line into the filter-quality-rework line (v0.3.0). The
filter-quality metric is **v0.3.0's** (corner-anchored A, phase-cost preservation,
directional verdict); the independent fixed-split redesign from the PIDscope branch was
dropped in its favour. This release adds the PIDscope-parity analysis on top.

### Added
- Analysis: multi-session decode — `signal.decode_all_dataframes` / `decode_sessions` decode
  **every** flight in a concatenated `.bbl` (each with its own header config and sample rate).
  `run()` builds one report pass per session instead of silently keeping only the first;
  empty/aborted sessions are skipped, explicit `session=` still selects one.
- Analysis: `analysis/filter_model.py` — analytic Betaflight filter chain (PT1/PT2/PT3 with the
  BF cutoff correction, biquad lowpass, dynamic notch) reconstructed from the parsed config.
  Predicted magnitude curve + per-stage group-delay budget (ms) for the gyro and D-term paths.
  Exposed in `pass["filter_model"]`.
- Analysis: `pid_balance` block — per-axis P/I/D contribution split by **AC-RMS** (mean removed,
  so the I-term attitude/trim DC offset does not masquerade as loop authority) + the tracking
  error (`err_rms`, `err_ratio` = RMS(setpoint−gyro)/RMS(setpoint)). Exposed in `pass["pid_balance"]`.
- Analysis: `pass["tuning_suggestions"]` — reactivity-oriented headroom block (for freestyle).
  From `mt`, step overshoot, `track_err_ratio` and the filter cut-offs it flags where the loop is
  conservative and has room (raise P; raise a low D-term LPF cut-off when the gyro is clean above
  it). FR/EN, per axis; the D clause is dropped on axes that run no D (yaw).
- Analysis: `step.analyse_flight` — amplitude-binned, stacked real-flight step response
  (per-window deconvolution, small vs large stick bins, 20–80th-percentile band, validity-gated).
  Exposed in `pass["step_flight"]`.
- Analysis: `pass["is_chirp"]`, `pass["frf_reliable"]`/`frf_coherent_frac`, and
  `throttle_map["motor_orders"]` (per-throttle-bin motor fundamental from eRPM; `ms_throttle`
  carries `mt`). The flight step shows only on normal logs; a low-coherence FRF suppresses the
  Bode-derived score and evolution tiles.
- Scoring: new `track_err` sub-score (normalised tracking error, weight 0.05); the real-flight
  large-step overshoot replaces the chirp-step overshoot when it is a clean step.

### Fixed
- Analysis: filter-model group-delay budget — (1) the dynamic notch no longer reports a ~6.7e8 ms
  phantom delay (median over the band instead of a mean dominated by the group-delay singularity at
  the notch null); (2) `dyn_notch_q` is divided by 100 to match firmware (the modelled notch was
  100x too narrow); (3) a dynamic LPF with `dyn_min_hz = 0` is treated as OFF (firmware falls back
  to static), so a disabled stage no longer contributes a phantom cut-off and delay.

### Notes
- Renderer: the PIDscope-parity visual blocks (filter delay-budget gauge, P/I/D balance bars,
  real-flight step panel, Mt-vs-throttle tile, motor-order overlay) are **not yet ported** to the
  reworked v0.3.0 renderer — their data is exposed in the pass JSON; the rendering is a follow-up.
  The current report renderer is v0.3.0's (filter-quality gauge, D-term SNR tile, reoriented
  throttle map).

## [0.3.0] - 2026-06-22

### Added
- Analysis: `_lpf_group_delay_ms()` — analytic mean in-control-band group delay (ms) of the gyro
  low-pass cascade, derived from the (known) filter config: deterministic, order-aware (PT1/PT2/PT3/
  BIQUAD), and identical across axes. `_filter_corners()` now carries `group_delay_ms`.
- Analysis: `_dterm_snr_db()` — D-term signal/noise ratio (dB) from the **pre-filter** (gyroUnfilt)
  spectrum, splitting f²-weighted derivative power at 100 Hz (useful D motion below, amplified noise
  above). Higher = more headroom to raise/disable `dterm_lpf2`. Surfaced per pass as `dterm_snr_db`,
  with a new glossary entry (`dterm_snr`) and an evolution tile (★ on the best pass).
- Renderer: per-pass gyro raw/filtered PSD overlay on the noise panel keeps non-primary passes'
  curves (report.py slims those passes to the per-axis curves + D-term SNR instead of dropping
  `noise_spectrum` entirely).

### Changed
- Analysis: **Preservation (P)** now comes from the analytic config-derived group delay as the
  primary metric instead of the measured FRF group delay, which was noisy / non-stationary and could
  swing negative axis-to-axis. The measured FRF lag is retained as a guard-rail (`phase_lag_frf_ms`).
  FRF group delay is now a robust linear phase-vs-ω fit (clamped ≥0) rather than a pointwise −dφ/dω.
- Analysis: block-level P / phase-lag aggregation uses the **median** across axes (one filter shared
  by all axes → per-axis spread is measurement noise; one bad axis can no longer drag the verdict).

### Fixed
- Renderer: gyro noise PSD panel — deselecting the primary pass's pill now actually hides its curve.
  The primary curve (and its peak dots / legend entry) was drawn unconditionally, ignoring the
  `NSEL` toggle set, so the last/primary pass could not be hidden.

## [0.2.0] - 2026-06-19

### Changed
- Analysis: filter-quality rework so the score reflects the *real* cost/benefit of filtering
  instead of pegging on power ratios. Band split is now anchored on the actual gyro low-pass
  corner (threaded from `config`) instead of `fs/4`, which previously let the preservation band
  swallow the intended roll-off.
  - **Attenuation (A)** now measures how much of the noise that *emerges as peaks above the
    broadband floor* the filter removes (floor-referenced, same peak detection as the noise
    panel), not a raw/filtered power ratio over the whole band. `A` is `None` when nothing
    emerges (clean spectrum) rather than a misleading value.
  - **Preservation (P)** is now the filter's **phase cost** in the control band — group delay it
    adds (coherence-gated FRF of gyroUnfilt→gyroADC), mapped 0.5→2.5 ms onto 1→0 — instead of a
    magnitude ratio. Falls back to a corner-bounded magnitude ratio when time signals are absent.
  - **Verdict** is derived from A and P **directly** (low A = under-filtered → tighten; low P =
    over-filtered → loosen), no longer from the harmonic score (which could not tell an
    under-filtered low score from an over-filtered one). New codes `loosen_candidate` and
    `na_motion_dominated`; an undefined score reads as an honest "n/a", never a low "tighten".
- Renderer: filter-quality recommendation strings (FR/EN) rewritten to be direction-unambiguous;
  tooltip and caption updated to the new A (emergent-noise removed) / P (phase cost) meaning.

### Added
- Analysis: `_filter_corners(config, fs)` — effective gyro corner frequencies (dynamic `gyro_lpf1`
  resolved to its lower bound for the low-throttle quiet window). `config` is now threaded through
  `analyse`/`build_pass` into the noise spectrum.
- Analysis: new `filter_quality` fields per axis — `phase_lag_ms`, `mag_droop_db`, `corner_hz`,
  `f_ctrl_max_hz`, `alpha_regime`, `excess_present`, and `worst_resid_db`/`worst_resid_hz`
  (highest peak still above the floor after filtering). Aggregated on the mean row.
- Renderer: filter-quality gauge readability — per-row direction badge (▲ tighten · ▼ loosen ·
  ● balanced), phase lag in ms next to Preservation and on the verdict line, a control-band /
  LPF-corner legend, and a residual-peak warning line (⚠ when a peak survives above the floor —
  surfaces e.g. a too-low dyn_notch Q that the energy-based A cannot see). Null scores render "—".

### Fixed
- Analysis: filter-quality `A` no longer collapses to `None`/uninformative on real broken-power-law
  spectra (the fit absorbed the noise hump, and an `alpha_hf` gate plus a corner floor on the
  attenuation band suppressed it). Now keyed on floor-referenced peaks.
- Analysis: `_filter_quality_block` mean verdict could invert a preservation-only score (a high P,
  i.e. low phase lag, read as "over-filtered, signal loss"); axis and mean now share one
  composition path. Mean aggregations tolerate `None` per-axis values.

## [0.1.9] - 2026-06-18

### Added
- Analysis: `filter_quality` block per axis — harmonic-mean score (attenuation × preservation),
  `f_split_hz` emergence frequency, power-law slopes `alpha`/`alpha_lf`, `f_knee_hz` breakpoint,
  and a plain-language `recommendation`/`confidence`/`reason` verdict. Exposed in `pass["filter_quality"]`.
- Analysis: `mt` (peak complementary sensitivity max|T|) and `f_mt_hz` per axis — closed-loop
  resonance indicator and delay-robustness complement to `ms`. Added to `pass["axes"][ax]`
  and to `tune_score` sub-scores.
- Renderer: filter-quality gauge block between the throttle map and the noise PSD. Full-width
  canvas with one row per axis: three horizontal bars (Attenuation · Preservation · Global score)
  with red/amber/green zone backgrounds and threshold guide lines at 0.6 and 0.8. The Score bar
  splits at 0.8 into solid green fill + diagonal amber hatch for the over-specified excess, with
  a ← arrow when the recommendation is `decrease_*`. Mean row with translated recommendation line.
  Glossary entry + FR/EN strings for all new keys.

## [0.1.8] - 2026-06-17

### Changed
- Renderer: noise-PSD axis chips now use radio behaviour — clicking a chip
  switches to that axis (raw or filtered) instead of additive overlay.
- Renderer: glossary fixes — motor-harmonic formula clarified
  (eRPM_raw × 100 = eRPM / 100), "Crossover 0 dB" now specifies
  closed-loop gain (FR + EN).
- Renderer: tuning frieze reordered to methodological sequence (noise /
  filtering before identification; "Marges & Robustesse (Ms/Mt)",
  "Réponse indicielle") (FR + EN).

## [0.1.7] - 2026-06-15

### Added
- Renderer: interactive legend → plot highlighting. Hovering a filter entry in the
  Bode-gain, phase, coherence or noise-PSD legend emphasises that item on the plot
  via a stacked transparent overlay (`mkCanvasHL`/`emphV`/`emphBand`/`bindHL`): LPF
  cut-off lines, the dyn_notch min–max band, and motor-harmonic bands. `bindHL` can
  drive several overlays at once, so shared markers echo across plots — f(Ms) on
  both gain and phase, the untrusted (coherence < gate) band on coherence + gain +
  phase. The base plot is never redrawn, so the highlight is cheap and clears on
  mouse-out.
- Renderer: the coherence reliability note became an interactive “untrusted zone”
  legend entry — hover shows its tooltip and highlights the grey band on all three
  Bode plots.
- Renderer: hovering the gyro noise-PSD curve shows a zoom tooltip of the nearest
  local peak with its immediate neighbourhood (linear-frequency mini-plot, raw vs
  filtered). The peak freq/height labels were moved off the busy full-band plot
  (yellow dots kept) into this zoom.
- Renderer: hovering the f(Ms) line on the Bode plot shows a zoom of the gain |T|
  and the sensitivity |S| = |1 − T| (computed from gain + phase) with the Ms peak
  marked; on the phase plot, a ±10 Hz zoom of the measured margin (phase vs the
  −180° line at f(Ms), interval shaded). Both carry a local ordinate scale.

### Changed
- Renderer: clicking a pass pill (show/hide) now preserves the scroll position
  instead of jumping back to the top of the report.
- Renderer (`report_assets/chirp_report.js`): the source `.bbl` file name now
  surfaces in multi-pass identification. File names are reduced to their basename
  (`baseName`) everywhere they appear — the rich pass tooltip (`cfgHTML`), the
  settings-comparison column header (shown under `P{n}`), and the config tooltip.
  The pass pills and comparison header use the single rich `cfgHTML` tooltip
  (config + file); the redundant native `title` is dropped so only one tooltip
  shows. No `p.file` → identical to before; mono-pass unchanged. Renderer signature,
  IIFE/global export and dual classic-script/ES-module loading untouched.
- `report.py` `_assemble_report`: each assembled pass' `file` is normalised to its
  bare basename so the skill and standalone report always have a clean name to show.

## [0.1.6] - 2026-06-12

### Documentation
- README `## Output` section: return-surface table and the full pass-dict /
  `AnalysisResult` shape (top-level keys, per-axis Bode/step/diagnosis fields,
  `tune_score` grade, `noise_spectrum`). Doc-only release to refresh the PyPI page.

## [0.1.5] - 2026-06-12

### Added
- First public PyPI release.
- PyPI packaging metadata: authors, keywords, trove classifiers, project URLs.
- `CHANGELOG.md`.
- README badges (PyPI version, supported Python versions, license).
- GitHub Actions: `ci.yml` (pytest matrix on 3.10–3.12) and `publish.yml`
  (build + publish to PyPI on release via OIDC trusted publishing).
- `tests/data/8.bbl` committed as a GPS-free pipeline fixture so the test
  suite runs in CI (verified: no GPS frame defined, no home-point coordinates).

### Changed
- Genericized docs/docstrings ahead of the public release (removed named
  downstream consumers).

## [0.1.4] - 2026-06-11

### Changed
- Extracted the HTML report renderer into shared, mountable assets
  (`report_assets/{chirp_report.js,chirp_report.css,glossary.json,strings.json}`),
  inlined by `report.py` and mountable by a web front.

## [0.1.3] - 2026-06-08

### Changed
- Lazy package init — numpy/scipy/pandas load only when an analysis runs;
  `from betaflight_chirp_core import decoder` stays stdlib-only.

## [0.1.2] - 2026-06-08

### Added
- Spectral and step-response analyses ported into the core (phase 6).

## [0.1.1] - 2026-06-08

### Added
- Exposed `assemble_report` and `noise_margin_db` for CLI front-ends.

## [0.1.0] - 2026-06-08

### Added
- Initial release: package scaffold, `.bbl` decoder, `signal`/`config`,
  chirp FRF/Bode analysis, and the self-contained HTML report.

[Unreleased]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.9...v0.2.0
[0.1.9]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SebGalina/betaflight-chirp-core/releases/tag/v0.1.0
