"""State recording and rollback.

The tool's safety claim is that it can put back anything it changed, so these
are the tests that claim has to survive.
"""

import json
import unittest

from harness import Rig, RigTest

A = "sys/devices/platform/aorus_laptop"
P = "sys/devices/system/cpu/intel_pstate"
MSR = "sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"
MMIO = "sys/class/powercap/intel-rapl-mmio:0/constraint_0_power_limit_uw"


class TestRemember(RigTest):

    def test_keeps_the_first_value_only(self):
        self.ac.STATE.remember("fan_mode", "normal")
        self.ac.STATE.remember("fan_mode", "gaming")
        self.assertEqual(self.ac.STATE.original("fan_mode"), "normal")

    def test_ignores_none(self):
        self.ac.STATE.remember("gpu_boost", None)
        self.assertIsNone(self.ac.STATE.original("gpu_boost"))
        self.assertNotIn("gpu_boost", self.ac.STATE.data.get("original", {}))

    def test_survives_a_reload_from_disk(self):
        self.ac.STATE.remember("governor", "powersave")
        reloaded = self.ac.State()
        self.assertEqual(reloaded.original("governor"), "powersave")

    def test_a_corrupt_state_file_does_not_crash(self):
        self.ac.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.ac.STATE_FILE.write_text("{not json")
        self.assertEqual(self.ac.State().data, {})


class TestRollback(RigTest):

    def _snapshot(self):
        return {
            "fan_mode": self.rig.read(f"{A}/fan_mode"),
            "fan_speed": self.rig.read(f"{A}/fan_custom_speed"),
            "gpu_boost": self.rig.read(f"{A}/gpu_boost"),
            "charge_limit": self.rig.read(f"{A}/charge_limit"),
            "max_perf": self.rig.read(f"{P}/max_perf_pct"),
            "no_turbo": self.rig.read(f"{P}/no_turbo"),
            "governor": self.rig.read("sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "epp": self.rig.read("sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"),
            "msr_pl1": self.rig.read(MSR),
            "mmio_pl1": self.rig.read(MMIO),
            "platform": self.rig.read("sys/firmware/acpi/platform_profile"),
        }

    def test_round_trip_restores_every_value(self):
        before = self._snapshot()

        self.rig.fans.set_mode("gaming")
        self.rig.fans.set_speed_pct(90)
        self.rig.fans.set_gpu_boost(2)
        self.rig.fans.set_charge_limit(70)
        self.rig.cpu.set_pl1(30)
        self.rig.cpu.set_max_perf_pct(50)
        self.rig.cpu.set_turbo(False)
        self.rig.cpu.set_governor("performance")
        self.rig.cpu.set_epp("power")
        self.rig.cpu.set_platform_profile("performance")

        self.assertNotEqual(self._snapshot(), before, "nothing actually changed")

        code, _ = self.rig.cli("reset")
        self.assertEqual(code, 0)
        self.assertEqual(self._snapshot(), before)

    def test_reset_clears_the_state_file(self):
        self.rig.cpu.set_pl1(30)
        self.assertTrue(self.ac.STATE_FILE.exists())
        self.rig.cli("reset")
        self.assertEqual(json.loads(self.ac.STATE_FILE.read_text()), {})

    def test_rapl_keys_survive_colons_in_the_domain_name(self):
        # 'intel-rapl:0' contains the separator, so a naive split loses the index
        self.rig.cpu.set_pl1(30)
        keys = list(self.ac.STATE.data["original"])
        self.assertIn("rapl|intel-rapl:0|0", keys)
        self.assertIn("rapl|intel-rapl-mmio:0|0", keys)
        self.rig.cli("reset")
        self.assertEqual(self.rig.read(MSR), "200000000")
        self.assertEqual(self.rig.read(MMIO), "55000000")

    def test_reset_with_no_recorded_changes_says_so(self):
        code, out = self.rig.cli("reset")
        self.assertEqual(code, 0)
        self.assertIn("nothing to undo", out)

    def test_reset_leaves_an_untouched_gpu_alone(self):
        # Unlocking clocks or restoring a power limit we never set would
        # override whatever the user had configured themselves.
        from harness import Rig
        rig = Rig(nvidia=True)
        self.addCleanup(rig.close)
        code, out = rig.cli("reset")
        self.assertEqual(code, 0)
        self.assertIn("nothing to undo", out)
        self.assertEqual(rig.run.argv("nvidia-smi"), [])

    def test_reset_does_restore_a_gpu_we_changed(self):
        from harness import Rig
        rig = Rig(nvidia=True)
        self.addCleanup(rig.close)
        rig.run.reply("--query-gpu=name", stdout=(
            "NVIDIA GeForce RTX 4070 Laptop GPU, 44, 0, 0, 2.7, 95.00, 95.00, "
            "5.00, 140.00, 210, 405, 1269, 8188, [N/A]"))
        rig.gpu.set_clocks(210, 1800)
        rig.run.calls.clear()
        _, out = rig.cli("reset")
        self.assertTrue(rig.run.ran("nvidia-smi", "-rgc"), out)

    def test_reset_restores_fan_mode_last(self):
        # Restoring duty after the mode would leave the fans briefly wrong
        self.rig.fans.set_mode("fixed")
        self.rig.fans.set_speed_pct(80)
        _, out = self.rig.cli("reset")
        lines = [l.strip() for l in out.splitlines() if "->" in l]
        self.assertTrue(lines[-1].startswith("fan mode"), lines)


class TestRollbackWithoutFanDriver(unittest.TestCase):

    def test_reset_skips_fan_writes_when_the_driver_is_absent(self):
        rig = Rig(fans=False)
        self.addCleanup(rig.close)
        rig.cpu.set_pl1(30)
        code, out = rig.cli("reset")
        self.assertEqual(code, 0)
        self.assertNotIn("fan mode", out)
        self.assertIn("long_term", out.replace("constraint 0", "long_term"))


if __name__ == "__main__":
    unittest.main()
