"""Tests for the command runner and report rendering (no macOS required)."""

import json
import sys
import unittest

from update_readiness.models import CheckResult, Status
from update_readiness.report import build_report, to_html, to_json
from update_readiness.runner import CommandRunner

from tests.helpers import NOW


class CommandRunnerTests(unittest.TestCase):
    """Real subprocesses (the Python interpreter) on any OS."""

    def test_success(self):
        self.assertTrue(CommandRunner().run([sys.executable, "-c", "pass"]).ok)

    def test_missing_binary(self):
        self.assertTrue(CommandRunner().run(["/nonexistent/x"]).not_found)

    def test_timeout(self):
        r = CommandRunner(timeout=0.5).run([sys.executable, "-c", "import time; time.sleep(5)"])
        self.assertTrue(r.timed_out)


class ReportTests(unittest.TestCase):
    def test_headline_and_escaping(self):
        results = [CheckResult("x", "X", "Cat", Status.FAIL, "<b>bad</b>")]
        rep = build_report(results, tool="t", version="1", generated_at=NOW, data_source="live",
                           summary_extra={"headline": "Readiness: NOT READY", "readiness": "not_ready"})
        page = to_html(rep, "T")
        self.assertIn("Readiness: NOT READY", page)
        self.assertNotIn("<b>bad</b>", page)
        self.assertEqual(json.loads(to_json(rep))["summary"]["readiness"], "not_ready")
        self.assertEqual(rep["summary"]["overall_status"], Status.FAIL)


if __name__ == "__main__":
    unittest.main()
