# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (PIDscope-parity)
- Analysis: `analysis/filter_model.py` — analytic Betaflight filter chain (PT1/PT2/PT3 with the
  BF cutoff correction, biquad lowpass, dynamic notch) reconstructed from the parsed config.
  Returns a predicted magnitude curve and a per-stage group-delay budget (ms) for the gyro and
  D-term paths, averaged over 0–100 Hz. Exposed in `pass["filter_model"]`.
- Analysis: `pid_balance` block — per-axis RMS contribution of axisP/axisI/axisD (+ percentages)
  and the tracking error (`err_rms`, plus `err_ratio` = RMS(setpoint−gyro)/RMS(setpoint)).
- Analysis: `step.analyse_flight` — amplitude-binned, stacked real-flight step response
  (per-window Wiener deconvolution, small vs large stick bins, 20–80th-percentile band). Each window
  is gated for validity (positive DC gain, tail settled near 1.0, no runaway ringing). Exposed in
  `pass["step_flight"]`.
- Analysis: `pass["is_chirp"]` flag (debug[1] axis flag or debug[0] phase channel). The flight step
  is computed and shown ONLY on a normal flight log; on a chirp log the closed-loop chirp-FRF step is
  authoritative and the flight step is suppressed (and does not feed the score).
- Analysis: `pass["frf_reliable"]` / `frf_coherent_frac` — fraction of the analysed band clearing the
  coherence gate (a chirp drives a contiguous coherent band, normal flight does not). When too low
  (< 0.10), the renderer shows a warning banner and suppresses the Bode-derived composite score and
  the per-axis evolution tiles, since the FRF/Ms/margin are meaningless without excitation. The
  real-flight blocks (flight step, noise, P/I/D balance, filter-delay budget) stay visible.
- Analysis: `throttle_map["motor_orders"]` — per-throttle-bin motor rotation fundamental (Hz) from
  eRPM, for the order lines that climb with throttle. `ms_throttle` entries also carry `mt`.
- Scoring: new `track_err` sub-score (normalised tracking error, weight 0.05); the real-flight
  large-step overshoot replaces the chirp-step overshoot in the score when it is a clean step
  (≥15 windows and a measurable rise time), otherwise the chirp overshoot stands. No double counting.
- Renderer: filter delay-budget gauge + predicted-attenuation overlay on the noise PSD; motor-order
  lines on the throttle-map heatmap; P/I/D balance bar block; real-flight step panel (small/large);
  Mt-vs-throttle companion tile. Glossary entries (`filter_delay`, `step_flight`, `pid_balance`)
  + FR/EN strings for all new keys.

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

[Unreleased]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.8...HEAD
[0.1.8]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SebGalina/betaflight-chirp-core/releases/tag/v0.1.0
