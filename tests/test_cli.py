"""End-to-end CLI tests driven by synthetic replay fixtures (no macOS required)."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from update_readiness import cli
from update_readiness.runner import CommandRunner

from tests.helpers import FIXTURES


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def replay_json(name, *extra):
    code, out, _ = run_cli("--replay", os.path.join(FIXTURES, name), "--json", "-", *extra)
    return code, json.loads(out)


class ReadinessVerdictTests(unittest.TestCase):
    def test_ready(self):
        code, rep = replay_json("ready.json")
        self.assertEqual(code, 0)
        self.assertEqual(rep["summary"]["readiness"], "ready")
        self.assertEqual(rep["summary"]["updates_available"], "available")
        self.assertEqual(rep["summary"]["update_count"], 2)

    def test_not_ready(self):
        code, rep = replay_json("not-ready.json", "--fail-on", "fail")
        self.assertEqual(code, 1)
        s = rep["summary"]
        self.assertEqual(s["readiness"], "not_ready")
        self.assertIn("power", s["blockers"])
        self.assertIn("disk_space", s["blockers"])
        self.assertIn("bootstrap_token", s["cautions"])
        self.assertIn("NOT READY", s["headline"])

    def test_up_to_date(self):
        code, rep = replay_json("up-to-date.json")
        self.assertEqual(rep["summary"]["updates_available"], "none")
        self.assertEqual(rep["summary"]["readiness"], "ready")

    def test_skip_catalog_makes_availability_unknown(self):
        _, rep = replay_json("ready.json", "--skip-catalog")
        self.assertEqual(rep["summary"]["updates_available"], "unknown")


class CliBehaviourTests(unittest.TestCase):
    def test_writes_html_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            j, h = os.path.join(tmp, "r.json"), os.path.join(tmp, "r.html")
            code, out, _ = run_cli("--replay", os.path.join(FIXTURES, "ready.json"), "--json", j, "--html", h)
            self.assertEqual(code, 0)
            with open(h, encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn("Readiness: READY", page)
            self.assertIn("Synthetic data", page)
            self.assertTrue(os.path.getsize(j) > 0)
            self.assertIn("Readiness: READY", out)

    def test_never_invokes_install_flags(self):
        """Guard against regressions that would install or download updates."""
        calls = []
        real_run = CommandRunner.run

        def spy(self, args, timeout=None):
            calls.append(list(args))
            return real_run(self, ["/nonexistent/stub"], timeout)

        with mock.patch.object(cli.sys, "platform", "darwin"), mock.patch.object(CommandRunner, "run", spy):
            run_cli("--quiet")
        flat = [a for call in calls for a in call]
        for forbidden in ("--install", "-i", "--download", "-d", "--fetch-full-installer", "--background"):
            self.assertNotIn(forbidden, flat)
        self.assertIn(["/usr/sbin/softwareupdate", "--list"], calls)

    def test_refuses_live_run_off_macos(self):
        with mock.patch.object(cli.sys, "platform", "linux"):
            code, _, err = run_cli()
        self.assertEqual(code, 3)
        self.assertIn("macOS only", err)

    def test_negative_min_free(self):
        self.assertEqual(run_cli("--min-free-gb", "-1")[0], 3)

    def test_list_checks(self):
        code, out, _ = run_cli("--list-checks")
        self.assertEqual(code, 0)
        self.assertIn("available_updates", out)


if __name__ == "__main__":
    unittest.main()
