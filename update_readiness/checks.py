"""Update readiness checks. Read-only: nothing here downloads or installs updates."""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from .models import CheckResult, Outcome, Status, outcome
from .runner import CommandResult

SW_VERS = "/usr/bin/sw_vers"
UNAME = "/usr/bin/uname"
SOFTWAREUPDATE = "/usr/sbin/softwareupdate"
DF = "/bin/df"
PMSET = "/usr/bin/pmset"
PLUTIL = "/usr/bin/plutil"
PROFILES = "/usr/bin/profiles"

SU_PREFS = "/Library/Preferences/com.apple.SoftwareUpdate.plist"
SU_MANAGED = "/Library/Managed Preferences/com.apple.SoftwareUpdate.plist"
RESTRICTIONS_MANAGED = "/Library/Managed Preferences/com.apple.applicationaccess.plist"

# Apple does not publish exact free-space requirements for every update. These
# defaults are deliberately conservative and can be overridden with --min-free-gb.
DEFAULTS = {
    "min_free_gb_minor": 20.0,
    "min_free_gb_major": 35.0,
    "update_size_multiplier": 2.5,   # room to download, expand, and apply
    "min_battery_percent": 50,       # macOS requires AC power or >= 50% to install
    "catalog_stale_days": 14,
    "softwareupdate_timeout": 300,
}


@dataclass
class Options:
    skip_catalog: bool = False
    min_free_gb: Optional[float] = None


@dataclass
class Context:
    runner: Any
    now: datetime
    options: Options = field(default_factory=Options)
    state: Dict[str, Any] = field(default_factory=dict)


CheckFn = Callable[[Context], Outcome]
_REGISTRY: List[tuple] = []


def check(check_id: str, title: str, category: str) -> Callable[[CheckFn], CheckFn]:
    def decorator(fn: CheckFn) -> CheckFn:
        _REGISTRY.append((check_id, title, category, fn))
        return fn
    return decorator


def available_checks() -> List[tuple]:
    return [(cid, title, cat) for cid, title, cat, _ in _REGISTRY]


def run_checks(ctx: Context, only: Optional[Sequence[str]] = None) -> List[CheckResult]:
    results = []
    for check_id, title, category, fn in _REGISTRY:
        if only and check_id not in only:
            continue
        try:
            res = fn(ctx)
        except Exception as exc:
            res = outcome(Status.ERROR, "Unexpected error while evaluating check: {}: {}".format(
                type(exc).__name__, exc))
        results.append(CheckResult(check_id, title, category, res.status, res.summary,
                                   dict(res.details), res.remediation))
    return results


def command_failure(result: CommandResult) -> Outcome:
    if result.not_found:
        return outcome(Status.SKIP, "Required command is unavailable on this system.",
                       command=result.command, error=result.describe_failure())
    if result.needs_privileges:
        return outcome(Status.SKIP, "Insufficient privileges to read this setting.",
                       "Re-run with sudo if this check is required.",
                       command=result.command, error=result.describe_failure())
    return outcome(Status.ERROR, result.describe_failure(), command=result.command)


def parse_key_values(text: str) -> Dict[str, str]:
    values = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            values[key.strip()] = value.strip()
    return values


def major_version(version: str) -> Optional[int]:
    match = re.match(r"(\d+)", version or "")
    return int(match.group(1)) if match else None


def read_plist(ctx: Context, path: str) -> Optional[Dict[str, Any]]:
    """Read a plist via plutil (handles binary plists). Returns None if absent or unreadable."""
    res = ctx.runner.run([PLUTIL, "-convert", "xml1", "-o", "-", path])
    if not res.ok or not res.stdout.strip():
        return None
    try:
        data = plistlib.loads(res.stdout.encode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- platform

@check("os_version", "Installed macOS", "Platform")
def check_os_version(ctx: Context) -> Outcome:
    res = ctx.runner.run([SW_VERS])
    if not res.ok:
        return command_failure(res)
    info = parse_key_values(res.stdout)
    version = info.get("ProductVersion")
    if not version:
        return outcome(Status.ERROR, "Could not parse sw_vers output.")
    ctx.state["os_version"] = version
    build = info.get("BuildVersion", "unknown")
    return outcome(Status.INFO, "macOS {} ({})".format(version, build),
                   product_version=version, build_version=build)


@check("architecture", "Architecture", "Platform")
def check_architecture(ctx: Context) -> Outcome:
    res = ctx.runner.run([UNAME, "-m"])
    if not res.ok:
        return command_failure(res)
    arch = res.stdout.strip()
    ctx.state["architecture"] = arch
    if arch == "arm64":
        return outcome(Status.INFO, "Apple silicon (arm64)", architecture=arch,
                       note="MDM-initiated updates need a bootstrap token or volume owner authorization.")
    return outcome(Status.INFO, "Intel ({})".format(arch or "unknown"), architecture=arch)


# --------------------------------------------------------------------------- availability

def parse_softwareupdate(text: str) -> Optional[List[Dict[str, Any]]]:
    """Parse `softwareupdate --list` (macOS 10.15+ format).

    Returns a list of updates, [] when the system is up to date, or None when
    the output is not recognised (e.g. a network error).
    """
    if "No new software available" in text:
        return []
    updates: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("* Label:"):
            current = {"label": line.split(":", 1)[1].strip()}
            updates.append(current)
        elif current is not None and line.startswith("Title:"):
            for part in line.split(","):
                key, sep, value = part.partition(":")
                if sep:
                    current[key.strip().lower()] = value.strip()
    if not updates:
        return None
    parsed = []
    for item in updates:
        size_kib = None
        size_match = re.match(r"(\d+)\s*K", item.get("size", ""))
        if size_match:
            size_kib = int(size_match.group(1))
        parsed.append({
            "label": item["label"],
            "title": item.get("title", item["label"]),
            "version": item.get("version", ""),
            "size_gb": round(size_kib / 1024 ** 2, 2) if size_kib is not None else None,
            "recommended": item.get("recommended", "").upper() == "YES",
            "restart_required": "restart" in item.get("action", "").lower(),
        })
    return parsed


def is_macos_update(update: Dict[str, Any]) -> bool:
    return update.get("title", "").startswith("macOS") or update.get("label", "").startswith("macOS")


@check("available_updates", "Available updates", "Availability")
def check_available_updates(ctx: Context) -> Outcome:
    if ctx.options.skip_catalog:
        ctx.state["updates"] = None
        return outcome(Status.SKIP, "Update catalog lookup skipped (--skip-catalog).")
    res = ctx.runner.run([SOFTWAREUPDATE, "--list"], timeout=DEFAULTS["softwareupdate_timeout"])
    if res.error:
        ctx.state["updates"] = None
        return command_failure(res)
    text = res.stdout + "\n" + res.stderr
    updates = parse_softwareupdate(text)
    if updates is None:
        ctx.state["updates"] = None
        lines = [l.strip() for l in text.splitlines() if l.strip() and "Software Update Tool" not in l
                 and "Finding available software" not in l]
        return outcome(Status.ERROR, "Could not read the update catalog: {}".format(
            lines[0] if lines else "no recognisable output"),
            "Check network access to Apple's software update service and retry.",
            returncode=res.returncode)
    ctx.state["updates"] = updates
    if not updates:
        return outcome(Status.PASS, "No updates available; this Mac is current.", update_count=0)
    current_major = major_version(ctx.state.get("os_version", ""))
    # Only macOS updates can be major upgrades; Safari/XProtect/etc. have unrelated version numbers.
    major = [u for u in updates
             if current_major and is_macos_update(u) and (major_version(u["version"]) or 0) > current_major]
    ctx.state["major_upgrade"] = bool(major)
    restart = sum(1 for u in updates if u["restart_required"])
    summary = "{} update(s) available".format(len(updates))
    if restart:
        summary += ", {} requiring restart".format(restart)
    if major:
        summary += "; includes a major upgrade"
    return outcome(Status.INFO, summary, update_count=len(updates), restart_required=restart,
                   major_upgrade=bool(major), updates=updates)


# --------------------------------------------------------------------------- prerequisites

@check("disk_space", "Free space for update", "Prerequisites")
def check_disk_space(ctx: Context) -> Outcome:
    res = ctx.runner.run([DF, "-Pk", "/"])
    if not res.ok:
        return command_failure(res)
    lines = [l for l in res.stdout.splitlines() if l.strip()]
    try:
        avail_kb = int(lines[-1].split()[3])
    except (IndexError, ValueError):
        return outcome(Status.ERROR, "Could not parse df output.")
    free_gb = avail_kb / 1024 ** 2

    updates = ctx.state.get("updates") or []
    largest = max([u["size_gb"] for u in updates if u.get("size_gb")] or [0.0])
    if ctx.options.min_free_gb is not None:
        required, basis = ctx.options.min_free_gb, "--min-free-gb override"
    else:
        base = DEFAULTS["min_free_gb_major"] if ctx.state.get("major_upgrade") else DEFAULTS["min_free_gb_minor"]
        required = max(base, largest * DEFAULTS["update_size_multiplier"])
        basis = "major upgrade minimum" if ctx.state.get("major_upgrade") else "minor update minimum"
        if largest * DEFAULTS["update_size_multiplier"] > base:
            basis = "{}x largest update ({:.1f} GB)".format(DEFAULTS["update_size_multiplier"], largest)
    details = {"free_gb": round(free_gb, 1), "required_gb": round(required, 1), "basis": basis,
               "largest_update_gb": largest or None}
    summary = "{:.1f} GB free; {:.1f} GB recommended ({})".format(free_gb, required, basis)
    fix = "Free at least {:.0f} GB before installing updates.".format(max(0.0, required - free_gb) + 1)
    if free_gb < required:
        return Outcome(Status.FAIL, summary, details, fix)
    if free_gb < required * 1.5:
        return Outcome(Status.WARN, summary, details, "Space is sufficient but tight; consider freeing more.")
    return Outcome(Status.PASS, summary, details)


@check("power", "Power source", "Prerequisites")
def check_power(ctx: Context) -> Outcome:
    res = ctx.runner.run([PMSET, "-g", "batt"])
    if not res.ok:
        return command_failure(res)
    source_match = re.search(r"drawing from '([^']+)'", res.stdout)
    source = source_match.group(1) if source_match else "unknown"
    pct_match = re.search(r"(\d+)%", res.stdout)
    percent = int(pct_match.group(1)) if pct_match else None
    # The battery's id= value is intentionally not recorded.
    details = {"source": source, "battery_percent": percent}
    if source == "AC Power":
        return Outcome(Status.PASS, "On AC power" + (" ({}% battery)".format(percent) if percent is not None else ""),
                       details)
    if source == "Battery Power" and percent is not None:
        if percent < DEFAULTS["min_battery_percent"]:
            return Outcome(Status.FAIL, "On battery at {}%".format(percent), details,
                           "Connect to power: macOS requires AC power or at least 50% charge to install.")
        return Outcome(Status.WARN, "On battery at {}%".format(percent), details,
                       "Connect to power before starting the update.")
    return Outcome(Status.ERROR, "Could not determine power source from pmset output.", details)


@check("bootstrap_token", "Bootstrap token", "Prerequisites")
def check_bootstrap_token(ctx: Context) -> Outcome:
    res = ctx.runner.run([PROFILES, "status", "-type", "bootstraptoken"])
    if not res.ok:
        return command_failure(res)
    text = res.stdout + res.stderr
    supported = re.search(r"supported on server:\s*(YES|NO)", text, re.I)
    escrowed = re.search(r"escrowed to server:\s*(YES|NO)", text, re.I)
    if not (supported or escrowed):
        return outcome(Status.ERROR, "Could not parse bootstrap token status.")
    sup = supported.group(1).upper() == "YES" if supported else None
    esc = escrowed.group(1).upper() == "YES" if escrowed else None
    details = {"supported_on_server": sup, "escrowed_to_server": esc}
    apple_silicon = ctx.state.get("architecture") == "arm64"
    if esc:
        return Outcome(Status.PASS, "Bootstrap token escrowed to MDM", details)
    if not apple_silicon:
        return Outcome(Status.INFO, "Bootstrap token not escrowed (not required for updates on Intel)", details)
    fix = ("Escrow a bootstrap token (e.g. `sudo profiles install -type bootstraptoken`) so MDM can "
           "authorize updates on Apple silicon.")
    if sup is False:
        return Outcome(Status.WARN, "MDM server does not report bootstrap token support", details, fix)
    return Outcome(Status.WARN, "Bootstrap token not escrowed; MDM-managed updates may need user authorization",
                   details, fix)


# --------------------------------------------------------------------------- configuration

_SU_KEYS = ("AutomaticCheckEnabled", "AutomaticDownload", "AutomaticallyInstallMacOSUpdates",
            "CriticalUpdateInstall", "ConfigDataInstall")


@check("update_settings", "Software Update settings", "Configuration")
def check_update_settings(ctx: Context) -> Outcome:
    local = read_plist(ctx, SU_PREFS) or {}
    managed = read_plist(ctx, SU_MANAGED) or {}
    settings: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for key in _SU_KEYS:
        if key in managed:
            settings[key], sources[key] = bool(managed[key]), "managed"
        elif key in local:
            settings[key], sources[key] = bool(local[key]), "local"
    details: Dict[str, Any] = {"settings": settings, "sources": sources}
    last = local.get("LastSuccessfulDate")
    age_days = None
    if isinstance(last, datetime):
        stamp = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        age_days = round((ctx.now - stamp).total_seconds() / 86400, 1)
        details["last_successful_check"] = stamp.isoformat(timespec="seconds")
        details["last_check_age_days"] = age_days
    if not local and not managed:
        return Outcome(Status.INFO, "No Software Update preferences found (macOS defaults apply).", details)
    if settings.get("AutomaticCheckEnabled") is False:
        return Outcome(Status.WARN, "Automatic update checks are disabled", details,
                       "Enable automatic checks so the Mac learns about updates promptly.")
    if age_days is not None and age_days > DEFAULTS["catalog_stale_days"]:
        return Outcome(Status.WARN, "Last successful update check was {:.0f} days ago".format(age_days), details,
                       "Run `softwareupdate --list` or check network access to Apple's update service.")
    enabled = [k for k, v in settings.items() if v]
    return Outcome(Status.PASS, "Automatic checks on; {} of {} explicitly set options enabled".format(
        len(enabled), len(settings)), details)


_DEFERRAL_KEYS = {
    "forceDelayedSoftwareUpdates": "minor_updates_deferred",
    "forceDelayedMajorSoftwareUpdates": "major_upgrades_deferred",
    "enforcedSoftwareUpdateDelay": "default_delay_days",
    "enforcedSoftwareUpdateMinorOSDeferredInstallDelay": "minor_delay_days",
    "enforcedSoftwareUpdateMajorOSDeferredInstallDelay": "major_delay_days",
    "enforcedSoftwareUpdateNonOSDeferredInstallDelay": "non_os_delay_days",
}


@check("deferrals", "Managed update deferrals", "Configuration")
def check_deferrals(ctx: Context) -> Outcome:
    restrictions = read_plist(ctx, RESTRICTIONS_MANAGED)
    if not restrictions:
        return outcome(Status.INFO, "No MDM update deferrals configured.")
    details = {name: restrictions[key] for key, name in _DEFERRAL_KEYS.items() if key in restrictions}
    if not details:
        return outcome(Status.INFO, "No MDM update deferrals configured.")
    parts = []
    if details.get("minor_updates_deferred"):
        parts.append("minor updates deferred {} days".format(
            details.get("minor_delay_days", details.get("default_delay_days", "?"))))
    if details.get("major_upgrades_deferred"):
        parts.append("major upgrades deferred {} days".format(
            details.get("major_delay_days", details.get("default_delay_days", "?"))))
    summary = "MDM deferrals: " + ("; ".join(parts) if parts else "keys present, no deferral active")
    return Outcome(Status.INFO, summary, details)
