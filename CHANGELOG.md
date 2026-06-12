# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[Unreleased]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/SebGalina/betaflight-chirp-core/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SebGalina/betaflight-chirp-core/releases/tag/v0.1.0
