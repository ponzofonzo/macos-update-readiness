"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import __version__
from .checks import Context, Options, available_checks, run_checks
from .models import SEVERITY, CheckResult, Status
from .report import build_report, to_html, to_json, to_text
from .runner import CommandRunner, ReplayRunner

TOOL_NAME = "macos-update-readiness"
REPORT_TITLE = "macOS Update Readiness Report"

EXIT_OK = 0
EXIT_THRESHOLD = 1
EXIT_FATAL = 3

VERDICT_TEXT = {
    "ready": "READY",
    "ready_with_warnings": "READY WITH WARNINGS",
    "not_ready": "NOT READY",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Evaluate macOS update availability and readiness. Never downloads or installs updates.",
    )
    parser.add_argument("--json", metavar="PATH", help="write the JSON report to PATH ('-' for stdout)")
    parser.add_argument("--html", metavar="PATH", help="write the HTML report to PATH")
    parser.add_argument("--skip-catalog", action="store_true",
                        help="do not query Apple's update catalog (offline / fast mode)")
    parser.add_argument("--min-free-gb", type=float, metavar="GB",
                        help="override the free-space requirement")
    parser.add_argument("--check", action="append", metavar="ID", help="run only this check (repeatable)")
    parser.add_argument("--list-checks", action="store_true", help="list available checks and exit")
    parser.add_argument("--fail-on", choices=["never", "warn", "fail"], default="never",
                        help="exit 1 when overall status is at or above this level (default: never)")
    parser.add_argument("--timeout", type=float, default=30, metavar="SECONDS",
                        help="per-command timeout (default: 30; softwareupdate uses 300)")
    parser.add_argument("--replay", metavar="FIXTURE",
                        help="render from a recorded/synthetic fixture instead of running commands")
    parser.add_argument("--quiet", action="store_true", help="suppress the console summary")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser


def readiness(results: List[CheckResult], state: Dict[str, Any]) -> Dict[str, Any]:
    blockers = [r.id for r in results if r.status == Status.FAIL]
    cautions = [r.id for r in results if r.status in (Status.WARN, Status.ERROR)]
    updates = state.get("updates")
    if updates is None:
        availability = "unknown"
    else:
        availability = "available" if updates else "none"
    if blockers:
        verdict = "not_ready"
    elif cautions:
        verdict = "ready_with_warnings"
    else:
        verdict = "ready"
    avail_text = {"unknown": "not checked", "none": "none", "available": str(len(updates or []))}[availability]
    return {
        "readiness": verdict,
        "updates_available": availability,
        "update_count": len(updates) if updates else 0,
        "blockers": blockers,
        "cautions": cautions,
        "headline": "Readiness: {} · Updates available: {}".format(VERDICT_TEXT[verdict], avail_text),
    }


def _write(path: str, content: str) -> None:
    if path == "-":
        sys.stdout.write(content)
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _exit_code(overall: str, fail_on: str) -> int:
    if fail_on == "never":
        return EXIT_OK
    threshold = SEVERITY[Status.WARN] if fail_on == "warn" else SEVERITY[Status.FAIL]
    return EXIT_THRESHOLD if SEVERITY[overall] >= threshold else EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_checks:
        for check_id, title, category in available_checks():
            print("{:<18} {:<28} {}".format(check_id, title, category))
        return EXIT_OK

    known = {c[0] for c in available_checks()}
    unknown = [c for c in (args.check or []) if c not in known]
    if unknown:
        print("error: unknown check id(s): {}".format(", ".join(unknown)), file=sys.stderr)
        return EXIT_FATAL
    if args.min_free_gb is not None and args.min_free_gb < 0:
        print("error: --min-free-gb must be >= 0", file=sys.stderr)
        return EXIT_FATAL

    if args.replay:
        try:
            runner = ReplayRunner.from_file(args.replay)
        except (OSError, ValueError) as exc:
            print("error: cannot load replay fixture: {}".format(exc), file=sys.stderr)
            return EXIT_FATAL
        now = runner.now or datetime.now(timezone.utc)
        source = "replay:{}".format(os.path.basename(args.replay))
    else:
        if sys.platform != "darwin":
            print("error: {} collects data on macOS only. Use --replay FIXTURE to render a report "
                  "from synthetic data on other platforms.".format(TOOL_NAME), file=sys.stderr)
            return EXIT_FATAL
        runner = CommandRunner(timeout=args.timeout)
        now = datetime.now(timezone.utc)
        source = "live"

    ctx = Context(runner=runner, now=now,
                  options=Options(skip_catalog=args.skip_catalog, min_free_gb=args.min_free_gb))
    results = run_checks(ctx, only=args.check)
    report = build_report(results, tool=TOOL_NAME, version=__version__, generated_at=now,
                          data_source=source, summary_extra=readiness(results, ctx.state))

    try:
        if args.json:
            _write(args.json, to_json(report))
        if args.html:
            _write(args.html, to_html(report, REPORT_TITLE))
    except OSError as exc:
        print("error: cannot write report: {}".format(exc), file=sys.stderr)
        return EXIT_FATAL

    if not args.quiet and args.json != "-":
        sys.stdout.write(to_text(report))

    return _exit_code(report["summary"]["overall_status"], args.fail_on)
