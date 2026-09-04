"""Clamping and range checks on everything that reaches the hardware."""

import unittest

from harness import Rig, RigTest

A = "sys/devices/platform/aorus_laptop"
P = "sys/devices/system/cpu/intel_pstate"
MMIO = "sys/class/powercap/intel-rapl-mmio:0/constraint_0_power_limit_uw"
MSR = "sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"


class TestFanDuty(RigTest):

    def test_full_speed_never_exceeds_the_ec_range(self):
        # Writing past the EC's top value stops the fans instead of speeding
        # them up, so 100 percent must land exactly on the ceiling.
        self.rig.fans.set_speed_pct(100)
        self.assertEqual(int(self.rig.read(f"{A}/fan_custom_speed")),
                         self.ac.FAN_DUTY_MAX)
        self.assertLess(self.ac.FAN_DUTY_MAX, 255)

    def test_scale_is_the_ec_range_not_a_byte(self):
        for pct, expected in [(0, 0), (50, 114), (85, 195), (100, 229)]:
            self.rig.fans.set_speed_pct(pct)
            self.assertEqual(int(self.rig.read(f"{A}/fan_custom_speed")), expected,
                             f"{pct}% mapped wrong")

    def test_out_of_range_percentages_are_clamped(self):
        self.rig.fans.set_speed_pct(-40)
        self.assertEqual(int(self.rig.read(f"{A}/fan_custom_speed")), 0)
        self.rig.fans.set_speed_pct(500)
        self.assertEqual(int(self.rig.read(f"{A}/fan_custom_speed")),
                         self.ac.FAN_DUTY_MAX)

    def test_percent_survives_a_write_read_round_trip(self):
        for pct in (0, 25, 50, 85, 100):
            self.rig.fans.set_speed_pct(pct)
            self.assertEqual(self.rig.fans.custom_speed_pct(), pct)

    def test_duty_ceiling_is_configurable(self):
        cfg = self.rig.root / "custom.toml"
        cfg.write_text("[fans]\nduty_max = 200\n")
        self.ac.CONFIG_FILE = cfg
        self.ac.apply_config_globals(self.ac.load_config())
        self.rig.fans.set_speed_pct(100)
        self.assertEqual(int(self.rig.read(f"{A}/fan_custom_speed")), 200)

    def test_a_silly_duty_ceiling_is_rejected(self):
        cfg = self.rig.root / "silly.toml"
        cfg.write_text("[fans]\nduty_max = 9000\n")
        self.ac.CONFIG_FILE = cfg
        before = self.ac.FAN_DUTY_MAX
        self.ac.apply_config_globals(self.ac.load_config())
        self.assertEqual(self.ac.FAN_DUTY_MAX, before)


class TestOtherFanLimits(RigTest):

    def test_unknown_fan_mode_is_refused(self):
        with self.assertRaises(self.ac.Err) as cm:
            self.rig.fans.set_mode("turbo")
        self.assertIn("unknown fan mode", str(cm.exception))
        self.assertEqual(self.rig.read(f"{A}/fan_mode"), "0")

    def test_charge_limit_is_clamped_to_the_firmware_range(self):
        self.rig.fans.set_charge_limit(10)
        self.assertEqual(self.rig.read(f"{A}/charge_limit"), "60")
        self.rig.fans.set_charge_limit(150)
        self.assertEqual(self.rig.read(f"{A}/charge_limit"), "100")

    def test_charge_limit_switches_to_custom_mode(self):
        self.rig.fans.set_charge_limit(80)
        self.assertEqual(self.rig.read(f"{A}/charge_mode"), "1")

    def test_gpu_boost_is_clamped(self):
        self.rig.fans.set_gpu_boost(9)
        self.assertEqual(self.rig.read(f"{A}/gpu_boost"), "2")
        self.rig.fans.set_gpu_boost(-3)
        self.assertEqual(self.rig.read(f"{A}/gpu_boost"), "0")


class TestCpuLimits(RigTest):

    def test_pl1_is_clamped_to_the_published_firmware_maximum(self):
        msgs = self.rig.cpu.set_pl1(100)
        self.assertEqual(self.rig.read(MMIO), "55000000")
        self.assertTrue(any("firmware caps" in m for m in msgs), msgs)

    def test_pl1_below_the_cap_is_written_exactly(self):
        self.rig.cpu.set_pl1(35)
        self.assertEqual(self.rig.read(MMIO), "35000000")
        self.assertEqual(self.rig.read(MSR), "35000000")

    def test_an_unpublished_maximum_is_not_treated_as_zero(self):
        # constraint_1_max_power_uw reads 0 on this firmware. Clamping to it
        # would set PL2 to zero watts.
        self.rig.cpu.set_pl2(157)
        self.assertEqual(
            self.rig.read("sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw"),
            "157000000")

    def test_missing_constraint_raises_rather_than_writing(self):
        for dom in ("intel-rapl:0", "intel-rapl-mmio:0"):
            (self.rig.root / f"sys/class/powercap/{dom}/constraint_1_name").write_text("peak\n")
        self.rig.ac.FANS = self.rig.ac.CPU = self.rig.ac.GPU = None
        with self.assertRaises(self.ac.Err) as cm:
            self.rig.cpu.set_pl2(90)
        self.assertIn("short_term", str(cm.exception))

    def test_max_perf_pct_is_clamped(self):
        self.rig.cpu.set_max_perf_pct(5)
        self.assertEqual(self.rig.read(f"{P}/max_perf_pct"), "10")
        self.rig.cpu.set_max_perf_pct(400)
        self.assertEqual(self.rig.read(f"{P}/max_perf_pct"), "100")

    def test_unavailable_governor_is_refused(self):
        with self.assertRaises(self.ac.Err) as cm:
            self.rig.cpu.set_governor("ondemand")
        self.assertIn("not available", str(cm.exception))
        self.assertEqual(
            self.rig.read("sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "powersave")

    def test_unavailable_epp_is_refused(self):
        with self.assertRaises(self.ac.Err):
            self.rig.cpu.set_epp("turbo_mode")

    def test_unavailable_platform_profile_is_refused(self):
        with self.assertRaises(self.ac.Err):
            self.rig.cpu.set_platform_profile("ludicrous")


class TestWriteErrors(unittest.TestCase):

    def test_eperm_as_root_is_not_reported_as_a_permission_problem(self):
        # aorus-laptop returns -1 for a failed WMI call, which the kernel
        # reports as EPERM, so it looks exactly like a permissions error.
        rig = Rig(root=True)
        self.addCleanup(rig.close)
        target = rig.root / "sys/devices/platform/aorus_laptop/gpu_boost"
        original = type(target).write_text

        def deny(self, data, *a, **k):
            if self.name == "gpu_boost":
                raise PermissionError(1, "Operation not permitted")
            return original(self, data, *a, **k)

        type(target).write_text = deny
        self.addCleanup(lambda: setattr(type(target), "write_text", original))

        with self.assertRaises(rig.ac.Err) as cm:
            rig.fans.set_gpu_boost(2)
        msg = str(cm.exception)
        self.assertIn("driver refused", msg)
        self.assertNotIn("need root", msg)

    def test_eperm_without_root_still_says_to_use_sudo(self):
        rig = Rig(root=False)
        self.addCleanup(rig.close)
        target = rig.root / "sys/devices/platform/aorus_laptop/gpu_boost"
        original = type(target).write_text

        def deny(self, data, *a, **k):
            if self.name == "gpu_boost":
                raise PermissionError(1, "Operation not permitted")
            return original(self, data, *a, **k)

        type(target).write_text = deny
        self.addCleanup(lambda: setattr(type(target), "write_text", original))

        with self.assertRaises(rig.ac.Err) as cm:
            rig.fans.set_gpu_boost(2)
        self.assertIn("need root", str(cm.exception))


class TestDryRun(RigTest):

    def test_dry_run_writes_nothing(self):
        before = self.rig.read(f"{A}/fan_custom_speed")
        code, out = self.rig.cli("--dry-run", "fan", "speed", "90")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out)
        self.assertEqual(self.rig.read(f"{A}/fan_custom_speed"), before)

    def test_dry_run_leaves_no_state_file(self):
        self.rig.cli("--dry-run", "cpu", "pl1", "30")
        self.assertFalse(self.ac.STATE_FILE.exists())


if __name__ == "__main__":
    unittest.main()
