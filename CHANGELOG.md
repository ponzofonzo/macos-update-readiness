# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-05

### Added
- Readiness verdict (`ready`, `ready_with_warnings`, `not_ready`) with blockers and cautions.
- Checks: installed macOS, architecture, available updates (`softwareupdate --list`), free space
  for the update, power source, bootstrap token, Software Update settings, and MDM deferrals.
- Detects major upgrades. Ignores version numbers of non-macOS updates such as Safari.
- `--skip-catalog` offline mode and `--min-free-gb` override.
- JSON, HTML, and console reports. Replay mode for synthetic fixtures.
- Unit tests, including a guard that fails if any install or download flag is ever used.
