# macOS Update Readiness Checker

[![CI](https://github.com/ponzofonzo/macos-update-readiness/actions/workflows/ci.yml/badge.svg)](https://github.com/ponzofonzo/macos-update-readiness/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)

Find out whether a Mac **can** take its pending macOS updates before you push them. The tool
lists available updates, checks free space, power, and bootstrap token escrow, reads Software
Update and MDM deferral settings, and gives an overall readiness verdict. Reports are written as
JSON and HTML.

> **It never installs or downloads anything.** The only Software Update call is
> `softwareupdate --list`. A unit test fails the build if any install or download flag is ever
> passed.

## Readiness verdict

| Verdict | Meaning |
|---------|---------|
| `ready` | No blocking or cautionary findings. |
| `ready_with_warnings` | Nothing blocks the update, but something deserves attention (tight disk space, on battery above 50 %, no bootstrap token on Apple silicon, an unreadable setting). |
| `not_ready` | At least one blocker: not enough free space, or on battery below 50 %. |

The JSON summary also reports `updates_available` (`available`, `none`, or `unknown`), plus
`blockers` and `cautions` as lists of check IDs.

## Checks

| ID | Category | Source | Notes |
|----|----------|--------|-------|
| `os_version` | Platform | `sw_vers` | info |
| `architecture` | Platform | `uname -m` | info. Changes how `bootstrap_token` is judged |
| `available_updates` | Availability | `softwareupdate --list` | Lists updates with size, restart, and recommended flags. Detects major upgrades. Queries Apple's servers; skip with `--skip-catalog` |
| `disk_space` | Prerequisites | `df -Pk /` | Fails below the required space (see below) and warns below 1.5× that |
| `power` | Prerequisites | `pmset -g batt` | Fails on battery below 50 % and warns on battery above that |
| `bootstrap_token` | Prerequisites | `profiles status -type bootstraptoken` | Warns if not escrowed on Apple silicon. Needs root, otherwise `skip` |
| `update_settings` | Configuration | `com.apple.SoftwareUpdate` (local and managed) via `plutil` | Warns if automatic checks are off or the last successful check is more than 14 days old |
| `deferrals` | Configuration | Managed `com.apple.applicationaccess` | Reports MDM deferral restrictions (info) |

### Free-space requirement

Apple doesn't publish an exact free-space requirement for each update, so the defaults are
conservative:

- **Minor update:** 20 GB
- **Major upgrade:** 35 GB
- If 2.5 × the largest listed update is bigger than that, the larger number wins.
- `--min-free-gb N` overrides all of the above.

Change the values in the `DEFAULTS` table in
[`update_readiness/checks.py`](update_readiness/checks.py) to match your own experience.

## Requirements

- macOS with `python3` 3.9 or newer. Apple's `/usr/bin/python3` needs the Command Line Tools.
- Standard user rights cover everything except `bootstrap_token`, which needs `sudo` and reports
  `skip` without it.
- Network access to Apple's update service for `available_updates`. You can skip it with
  `--skip-catalog`.

## Usage

```bash
git clone https://github.com/ponzofonzo/macos-update-readiness.git
cd macos-update-readiness

python3 -m update_readiness                                   # console summary
sudo python3 -m update_readiness --json r.json --html r.html  # full report incl. bootstrap token
python3 -m update_readiness --skip-catalog                    # offline / fast: no catalog lookup
python3 -m update_readiness --min-free-gb 40 --fail-on fail   # exit 1 when not ready
python3 -m update_readiness --json - | jq '.summary.readiness'
```

| Option | Meaning |
|--------|---------|
| `--json PATH` / `--html PATH` | Write reports (`-` = JSON to stdout). |
| `--skip-catalog` | Don't run `softwareupdate --list`. Availability becomes `unknown`. |
| `--min-free-gb GB` | Override the free-space requirement. |
| `--check ID` | Run only this check. You can repeat it. `--list-checks` shows the IDs. |
| `--fail-on never\|warn\|fail` | Exit 1 when the overall status is at or above this level. `fail` means "exit 1 when not ready". |
| `--timeout SECONDS` | Timeout for each command (`softwareupdate` always gets 300 s). |
| `--replay FIXTURE` | Render from synthetic or recorded output (any OS). |

**Exit codes:** `0` success · `1` the `--fail-on` threshold was hit · `2` invalid arguments ·
`3` fatal.

### Jamf Pro pre-flight example

Run this as a policy script before an update policy, then branch on the exit code or collect the
JSON with an Extension Attribute:

```bash
#!/bin/bash
/usr/local/bin/python3 -m update_readiness --fail-on fail --quiet \
  --json "/Library/Application Support/UpdateReadiness/latest.json"
```

## Examples

Rendered from **synthetic fixtures** in [`examples/fixtures/`](examples/fixtures/):

| Scenario | Report |
|----------|--------|
| Updates pending, all prerequisites met | [`ready-report.html`](examples/ready-report.html) / [`.json`](examples/ready-report.json) |
| Major upgrade offered, low battery, low disk, no bootstrap token | [`not-ready-report.html`](examples/not-ready-report.html) / [`.json`](examples/not-ready-report.json) |
| Fully up to date (Intel desktop) | [`up-to-date-report.html`](examples/up-to-date-report.html) / [`.json`](examples/up-to-date-report.json) |

Rebuild them on any OS:
`python3 -m update_readiness --replay examples/fixtures/not-ready.json --html out.html`.

## Testing

| Layer | Where it runs | What it proves |
|-------|---------------|----------------|
| Unit tests with mocked output | Any OS: `python3 -m unittest discover -s tests -t .` | `softwareupdate` / `pmset` / `profiles` / plist parsing, thresholds, verdict logic, failure handling, no-install guard |
| Replay fixtures | Any OS | End-to-end JSON/HTML generation |
| CI live smoke run | GitHub-hosted `macos-latest` VM | The tool runs against real macOS commands and produces a valid report |
| **Manual validation** | **Physical Macs** | Parsers match real output; behavior with real MDM enrollment, batteries, and pending updates |

All parser tests use synthetic output modeled on each command's documented or commonly observed
format. See [`docs/VALIDATION.md`](docs/VALIDATION.md) for the real-Mac checklist.

### Known limitations

- `softwareupdate --list` doesn't always offer major upgrades. Whether it does depends on the macOS
  version, deferrals, and MDM settings. Use `--list-full-installers` yourself if you need that list.
- The tool doesn't decide whether a Mac model is compatible with a future major release.
- Output parsing targets macOS 11 and later. The pre-Catalina `softwareupdate` format isn't
  supported.

## About this project

I've built tooling like this for employers during my career; that code belongs to them. This
repository is a generic version written independently, from scratch, for public use. It contains
no employer source code, configurations, credentials, device data, or internal information, and it
relies only on built-in macOS commands and publicly documented behavior.

## License

MIT. See [LICENSE](LICENSE).
