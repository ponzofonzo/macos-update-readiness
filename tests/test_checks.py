"""Unit tests for readiness checks using synthetic command output (no macOS required)."""

import unittest

from update_readiness.checks import Options, parse_softwareupdate
from update_readiness.models import Status

from tests.helpers import RESTRICTIONS, SU_LOCAL, SU_MANAGED, plist, run_many, run_one

SU = "/usr/sbin/softwareupdate --list"
DF = "/bin/df -Pk /"
GB = 1024 ** 2

LIST_TWO = (
    "Software Update Tool\n\nFinding available software\n"
    "Software Update found the following new or updated software:\n"
    "* Label: macOS Sequoia 15.2-24C101\n"
    "\tTitle: macOS Sequoia 15.2, Version: 15.2, Size: 3290764KiB, Recommended: YES, Action: restart, \n"
    "* Label: Safari18.2SequoiaAuto-18.2\n"
    "\tTitle: Safari, Version: 18.2, Size: 165432KiB, Recommended: YES, \n"
)


def df(avail_gb):
    return "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/disk3s1 500000000 1 {} 5% /\n".format(
        int(avail_gb * GB))


class SoftwareUpdateParserTests(unittest.TestCase):
    def test_two_updates(self):
        updates = parse_softwareupdate(LIST_TWO)
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0]["label"], "macOS Sequoia 15.2-24C101")
        self.assertEqual(updates[0]["version"], "15.2")
        self.assertTrue(updates[0]["restart_required"])
        self.assertTrue(updates[0]["recommended"])
        self.assertAlmostEqual(updates[0]["size_gb"], 3.14, places=2)
        self.assertFalse(updates[1]["restart_required"])

    def test_up_to_date(self):
        self.assertEqual(parse_softwareupdate("Software Update Tool\n\nNo new software available.\n"), [])

    def test_unrecognised(self):
        self.assertIsNone(parse_softwareupdate("Can't connect to the Software Update server\n"))

    def test_missing_title_line(self):
        updates = parse_softwareupdate("* Label: Something-1.0\n")
        self.assertEqual(updates[0]["title"], "Something-1.0")
        self.assertIsNone(updates[0]["size_gb"])


class AvailableUpdatesTests(unittest.TestCase):
    def test_updates_available_is_info(self):
        results, ctx = run_many({SU: (0, LIST_TWO), "/usr/bin/sw_vers": (0, "ProductVersion: 15.1\n")},
                                only=["os_version", "available_updates"])
        r = results["available_updates"]
        self.assertEqual(r.status, Status.INFO)
        self.assertEqual(r.details["update_count"], 2)
        self.assertFalse(r.details["major_upgrade"])
        self.assertEqual(len(ctx.state["updates"]), 2)

    def test_non_os_version_number_is_not_major_upgrade(self):
        safari_only = ("* Label: Safari18.2-18.2\n"
                       "\tTitle: Safari, Version: 18.2, Size: 165432KiB, Recommended: YES, \n")
        results, _ = run_many({SU: (0, safari_only), "/usr/bin/sw_vers": (0, "ProductVersion: 15.1\n")},
                              only=["os_version", "available_updates"])
        self.assertFalse(results["available_updates"].details["major_upgrade"])

    def test_major_upgrade_detected(self):
        results, ctx = run_many({SU: (0, LIST_TWO), "/usr/bin/sw_vers": (0, "ProductVersion: 14.7\n")},
                                only=["os_version", "available_updates"])
        self.assertTrue(results["available_updates"].details["major_upgrade"])
        self.assertTrue(ctx.state["major_upgrade"])

    def test_up_to_date_on_stderr(self):
        r = run_one("available_updates", {SU: (0, "Software Update Tool\n", "No new software available.\n")})
        self.assertEqual(r.status, Status.PASS)

    def test_network_failure_is_error(self):
        r = run_one("available_updates", {SU: (0, "", "Can't connect to the Software Update server\n")})
        self.assertEqual(r.status, Status.ERROR)
        self.assertIn("Can't connect", r.summary)

    def test_skip_catalog_runs_nothing(self):
        results, ctx = run_many({}, only=["available_updates"], options=Options(skip_catalog=True))
        self.assertEqual(results["available_updates"].status, Status.SKIP)
        self.assertEqual(ctx.runner.calls, [])
        self.assertIsNone(ctx.state["updates"])

    def test_missing_binary_is_skip(self):
        self.assertEqual(run_one("available_updates", {}).status, Status.SKIP)


class DiskSpaceTests(unittest.TestCase):
    def test_minor_minimum(self):
        self.assertEqual(run_one("disk_space", {DF: (0, df(100))}).status, Status.PASS)
        self.assertEqual(run_one("disk_space", {DF: (0, df(25))}).status, Status.WARN)
        self.assertEqual(run_one("disk_space", {DF: (0, df(15))}).status, Status.FAIL)

    def test_major_upgrade_raises_requirement(self):
        r = run_one("disk_space", {DF: (0, df(30))}, state={"major_upgrade": True})
        self.assertEqual(r.status, Status.FAIL)
        self.assertEqual(r.details["required_gb"], 35.0)

    def test_large_update_scales_requirement(self):
        state = {"updates": [{"size_gb": 12.0}]}
        r = run_one("disk_space", {DF: (0, df(28))}, state=state)
        self.assertEqual(r.details["required_gb"], 30.0)
        self.assertEqual(r.status, Status.FAIL)

    def test_override(self):
        r = run_one("disk_space", {DF: (0, df(15))}, options=Options(min_free_gb=5))
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.details["basis"], "--min-free-gb override")

    def test_bad_output(self):
        self.assertEqual(run_one("disk_space", {DF: (0, "nonsense")}).status, Status.ERROR)


class PowerTests(unittest.TestCase):
    CMD = "/usr/bin/pmset -g batt"

    def test_ac(self):
        r = run_one("power", {self.CMD: (0, "Now drawing from 'AC Power'\n -InternalBattery-0 (id=123)\t80%; "
                                            "charging\n")})
        self.assertEqual(r.status, Status.PASS)
        self.assertNotIn("123", str(r.details), "battery id must not be recorded")

    def test_desktop_ac_without_battery(self):
        self.assertEqual(run_one("power", {self.CMD: (0, "Now drawing from 'AC Power'\n")}).status, Status.PASS)

    def test_battery_levels(self):
        high = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t75%; discharging\n"
        low = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t30%; discharging\n"
        self.assertEqual(run_one("power", {self.CMD: (0, high)}).status, Status.WARN)
        self.assertEqual(run_one("power", {self.CMD: (0, low)}).status, Status.FAIL)

    def test_unparseable(self):
        self.assertEqual(run_one("power", {self.CMD: (0, "???")}).status, Status.ERROR)


class BootstrapTokenTests(unittest.TestCase):
    CMD = "/usr/bin/profiles status -type bootstraptoken"

    def out(self, supported, escrowed):
        return ("profiles: Bootstrap Token supported on server: {}\n"
                "profiles: Bootstrap Token escrowed to server: {}\n".format(supported, escrowed))

    def test_escrowed(self):
        self.assertEqual(run_one("bootstrap_token", {self.CMD: (0, self.out("YES", "YES"))}).status, Status.PASS)

    def test_not_escrowed_apple_silicon(self):
        r = run_one("bootstrap_token", {self.CMD: (0, self.out("YES", "NO"))}, state={"architecture": "arm64"})
        self.assertEqual(r.status, Status.WARN)

    def test_not_escrowed_intel_is_info(self):
        r = run_one("bootstrap_token", {self.CMD: (0, self.out("NO", "NO"))}, state={"architecture": "x86_64"})
        self.assertEqual(r.status, Status.INFO)

    def test_requires_root_is_skip(self):
        r = run_one("bootstrap_token", {self.CMD: (1, "", "profiles: this command requires root\n")})
        self.assertEqual(r.status, Status.SKIP)


class UpdateSettingsTests(unittest.TestCase):
    def test_enabled_and_recent(self):
        body = ("<key>AutomaticCheckEnabled</key><true/><key>AutomaticDownload</key><true/>"
                "<key>LastSuccessfulDate</key><date>2026-01-14T12:00:00Z</date>")
        r = run_one("update_settings", {SU_LOCAL: (0, plist(body))})
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.details["last_check_age_days"], 1.0)

    def test_managed_overrides_local(self):
        r = run_one("update_settings", {
            SU_LOCAL: (0, plist("<key>AutomaticCheckEnabled</key><true/>")),
            SU_MANAGED: (0, plist("<key>AutomaticCheckEnabled</key><false/>")),
        })
        self.assertEqual(r.status, Status.WARN)
        self.assertEqual(r.details["sources"]["AutomaticCheckEnabled"], "managed")

    def test_stale_catalog(self):
        body = "<key>AutomaticCheckEnabled</key><true/><key>LastSuccessfulDate</key><date>2025-12-01T00:00:00Z</date>"
        self.assertEqual(run_one("update_settings", {SU_LOCAL: (0, plist(body))}).status, Status.WARN)

    def test_no_prefs(self):
        self.assertEqual(run_one("update_settings", {}).status, Status.INFO)

    def test_corrupt_plist_treated_as_absent(self):
        self.assertEqual(run_one("update_settings", {SU_LOCAL: (0, "<plist><dict>")}).status, Status.INFO)


class DeferralTests(unittest.TestCase):
    def test_none(self):
        self.assertEqual(run_one("deferrals", {}).status, Status.INFO)

    def test_major_deferral(self):
        body = ("<key>forceDelayedMajorSoftwareUpdates</key><true/>"
                "<key>enforcedSoftwareUpdateMajorOSDeferredInstallDelay</key><integer>60</integer>")
        r = run_one("deferrals", {RESTRICTIONS: (0, plist(body))})
        self.assertIn("major upgrades deferred 60 days", r.summary)
        self.assertEqual(r.details["major_delay_days"], 60)


if __name__ == "__main__":
    unittest.main()
