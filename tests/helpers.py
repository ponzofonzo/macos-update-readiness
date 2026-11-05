"""Test helpers: fake runners built from synthetic command output."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from update_readiness.checks import Context, Options, run_checks
from update_readiness.runner import ReplayRunner

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "examples", "fixtures")

SU_LOCAL = "/usr/bin/plutil -convert xml1 -o - /Library/Preferences/com.apple.SoftwareUpdate.plist"
SU_MANAGED = "/usr/bin/plutil -convert xml1 -o - '/Library/Managed Preferences/com.apple.SoftwareUpdate.plist'"
RESTRICTIONS = ("/usr/bin/plutil -convert xml1 -o - "
                "'/Library/Managed Preferences/com.apple.applicationaccess.plist'")


def plist(body):
    return '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>{}</dict></plist>'.format(body)


def fake_runner(mapping):
    commands = {}
    for cmd, value in mapping.items():
        commands[cmd] = {"returncode": value[0], "stdout": value[1],
                         "stderr": value[2] if len(value) > 2 else ""}
    return ReplayRunner(commands)


def run_many(mapping, only=None, options=None, state=None, now=NOW):
    ctx = Context(runner=fake_runner(mapping), now=now, options=options or Options())
    if state:
        ctx.state.update(state)
    return {r.id: r for r in run_checks(ctx, only=only)}, ctx


def run_one(check_id, mapping, **kwargs):
    results, _ = run_many(mapping, only=[check_id], **kwargs)
    return results[check_id]
