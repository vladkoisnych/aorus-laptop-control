"""What the tool actually shells out, and how it reads the replies.

nvidia-smi and systemctl are the two external programs, so these tests assert on
the exact argv rather than on the effect, which no test machine can observe.
"""

import unittest

from harness import Rig, RigTest

GPU_CSV = ("NVIDIA GeForce RTX 4070 Laptop GPU, 46, 2, 1, 16.35, {limit}, 95.00, "
           "5.00, 140.00, 2175, 8000, 1197, 8188, [N/A]")


def gpu_rig(testcase, *, limit="[N/A]", enforced=95.0, persistence="Disabled",
            powerd="inactive"):
    rig = Rig(nvidia=True)
    testcase.addCleanup(rig.close)
    rig.run.reply("--query-gpu=name", stdout=GPU_CSV.format(limit=limit))
    rig.run.reply("--query-gpu=persistence_mode", stdout=persistence)
    rig.run.reply("-q -d POWER", stdout=(
        "    GPU Power Readings\n"
        f"        Current Power Limit               : {enforced:.2f} W\n"
        "        Default Power Limit               : 95.00 W\n"))
    rig.run.reply("is-active", stdout=powerd,
                  returncode=0 if powerd == "active" else 3)
    return rig


class TestGpuPowerLimit(unittest.TestCase):

    def test_persistence_is_enabled_before_setting_a_limit(self):
        # Without it the driver tears down GPU state on idle and the limit
        # goes with it, which looks like the write silently failing.
        rig = gpu_rig(self, enforced=120.0)
        rig.gpu.set_power_limit(120)
        self.assertTrue(rig.run.ran("nvidia-smi", "-pm", "1"))
        order = [" ".join(c) for c in rig.run.argv("nvidia-smi")]
        pm = next(i for i, c in enumerate(order) if "-pm 1" in c)
        pl = next(i for i, c in enumerate(order) if "-pl" in c)
        self.assertLess(pm, pl, "persistence must be enabled first")

    def test_persistence_is_not_touched_when_already_on(self):
        rig = gpu_rig(self, persistence="Enabled", enforced=120.0)
        rig.gpu.set_power_limit(120)
        self.assertFalse(rig.run.ran("nvidia-smi", "-pm"))

    def test_the_requested_wattage_is_clamped_to_the_vbios_range(self):
        rig = gpu_rig(self, enforced=140.0)
        rig.gpu.set_power_limit(400)
        self.assertTrue(rig.run.ran("-pl", "140"))

    def test_a_limit_that_did_not_stick_is_an_error_not_a_success(self):
        rig = gpu_rig(self, enforced=95.0)          # firmware ignores the write
        with self.assertRaises(rig.ac.Err) as cm:
            rig.gpu.set_power_limit(120)
        self.assertIn("still at 95 W", str(cm.exception))

    def test_the_failure_points_at_powerd_when_it_is_running(self):
        rig = gpu_rig(self, enforced=95.0, powerd="active")
        with self.assertRaises(rig.ac.Err) as cm:
            rig.gpu.set_power_limit(120)
        self.assertIn("nvidia-powerd is running", str(cm.exception))

    def test_the_failure_points_at_the_vbios_when_powerd_is_off(self):
        rig = gpu_rig(self, enforced=95.0, powerd="inactive")
        rig.run.reply("is-enabled", stdout="disabled", returncode=1)
        with self.assertRaises(rig.ac.Err) as cm:
            rig.gpu.set_power_limit(120)
        msg = str(cm.exception)
        self.assertIn("vBIOS", msg)
        self.assertIn("nvidia-powerd is installed here but not running", msg)

    def test_the_enforced_limit_falls_back_to_the_q_output(self):
        # power.limit reads N/A on this vBIOS, so the CSV cannot confirm a write
        rig = gpu_rig(self, limit="[N/A]", enforced=120.0)
        self.assertEqual(rig.gpu.enforced_limit_w(), 120.0)


class TestGpuOther(unittest.TestCase):

    def test_clock_lock_argv(self):
        rig = gpu_rig(self)
        rig.gpu.set_clocks(210, 1800)
        self.assertTrue(rig.run.ran("nvidia-smi", "-lgc", "210,1800"))

    def test_unlock_argv(self):
        rig = gpu_rig(self)
        rig.gpu.unlock_clocks()
        self.assertTrue(rig.run.ran("nvidia-smi", "-rgc"))

    def test_reset_unlocks_and_restores_the_default_limit(self):
        rig = gpu_rig(self, enforced=120.0)
        rig.gpu.set_power_limit(120)
        rig.run.calls.clear()
        rig.gpu.reset()
        self.assertTrue(rig.run.ran("-rgc"))
        self.assertTrue(rig.run.ran("-pl", "95"))

    def test_nothing_runs_when_nvidia_smi_is_missing(self):
        rig = Rig(nvidia=False)
        self.addCleanup(rig.close)
        with self.assertRaises(rig.ac.Err) as cm:
            rig.gpu.set_power_limit(100)
        self.assertIn("not found", str(cm.exception))
        self.assertEqual(rig.run.argv("nvidia-smi"), [])

    def test_powerd_state_has_three_answers(self):
        for stdout_active, stdout_enabled, rc_enabled, expected in [
            ("active", "enabled", 0, "active"),
            ("inactive", "disabled", 1, "disabled"),
            ("inactive", "", 4, "absent"),
        ]:
            with self.subTest(expected=expected):
                rig = Rig(nvidia=True)
                self.addCleanup(rig.close)
                rig.run.reply("is-active", stdout=stdout_active,
                              returncode=0 if stdout_active == "active" else 3)
                rig.run.reply("is-enabled", stdout=stdout_enabled,
                              returncode=rc_enabled)
                self.assertEqual(rig.gpu.powerd_state(), expected)


class TestDaemonHandover(RigTest):

    def test_picking_a_fan_mode_stops_the_daemon_first(self):
        self.ac.daemon_active = lambda: True
        code, out = self.rig.cli("fan", "mode", "silent")
        self.assertEqual(code, 0)
        self.assertTrue(self.rig.run.ran("systemctl", "stop", "aorusctld"))
        self.assertIn("stopped aorusctld", out)
        self.assertEqual(self.rig.read("sys/devices/platform/aorus_laptop/fan_mode"), "1")

    def test_nothing_is_stopped_when_the_daemon_is_not_running(self):
        code, out = self.rig.cli("fan", "mode", "silent")
        self.assertEqual(code, 0)
        self.assertFalse(self.rig.run.ran("systemctl", "stop"))
        self.assertNotIn("stopped aorusctld", out)

    def test_setting_a_fixed_duty_also_takes_over(self):
        self.ac.daemon_active = lambda: True
        self.rig.cli("fan", "speed", "60")
        self.assertTrue(self.rig.run.ran("systemctl", "stop", "aorusctld"))

    def test_dry_run_does_not_stop_anything(self):
        self.ac.daemon_active = lambda: True
        self.rig.cli("--dry-run", "fan", "mode", "silent")
        self.assertFalse(self.rig.run.ran("systemctl", "stop"))


if __name__ == "__main__":
    unittest.main()
