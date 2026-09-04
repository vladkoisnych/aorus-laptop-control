"""Profiles: parsing, validation, and what happens when the hardware says no."""

import tomllib
import unittest
from pathlib import Path

from harness import REPO, Rig, RigTest

A = "sys/devices/platform/aorus_laptop"


def write_config(rig, text):
    p = rig.root / "config.toml"
    p.write_text(text)
    rig.ac.CONFIG_FILE = p
    return p


class TestShippedConfig(unittest.TestCase):
    """The config that ships with the repository has to be correct."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = tomllib.loads((REPO / "config.toml").read_text())
        cls.profiles = cls.cfg["profiles"]

    def test_it_parses(self):
        self.assertTrue(self.profiles)

    def test_every_profile_uses_known_keys_only(self):
        ac = Rig().ac
        for name, prof in self.profiles.items():
            with self.subTest(profile=name):
                self.assertEqual(set(prof) - ac.PROFILE_KEYS, set())

    def test_fan_modes_are_real_modes(self):
        ac = Rig().ac
        for name, prof in self.profiles.items():
            if "fan_mode" in prof:
                with self.subTest(profile=name):
                    self.assertIn(prof["fan_mode"], ac.FAN_MODES)

    def test_numeric_settings_are_in_range(self):
        for name, prof in self.profiles.items():
            with self.subTest(profile=name):
                if "max_perf_pct" in prof:
                    self.assertGreaterEqual(prof["max_perf_pct"], 10)
                    self.assertLessEqual(prof["max_perf_pct"], 100)
                if "charge_limit" in prof:
                    self.assertGreaterEqual(prof["charge_limit"], 60)
                    self.assertLessEqual(prof["charge_limit"], 100)
                if "fan_speed" in prof:
                    self.assertGreaterEqual(prof["fan_speed"], 0)
                    self.assertLessEqual(prof["fan_speed"], 100)
                if "cpu_pl1" in prof and "cpu_pl2" in prof:
                    self.assertLessEqual(prof["cpu_pl1"], prof["cpu_pl2"],
                                         "sustained limit above the burst limit")

    def test_fan_curves_are_non_decreasing(self):
        for name, prof in self.profiles.items():
            if "fan_curve" not in prof:
                continue
            with self.subTest(profile=name):
                pts = prof["fan_curve"]
                self.assertLessEqual(len(pts), 15)
                temps = [p[0] for p in pts]
                duties = [p[1] for p in pts]
                self.assertEqual(temps, sorted(temps))
                self.assertEqual(duties, sorted(duties))
                self.assertLessEqual(max(duties), 100)

    def test_gpu_clocks_are_a_pair_or_the_reset_keyword(self):
        for name, prof in self.profiles.items():
            if "gpu_clocks" not in prof:
                continue
            with self.subTest(profile=name):
                v = prof["gpu_clocks"]
                if isinstance(v, str):
                    self.assertIn(v, ("reset", "auto"))
                else:
                    self.assertEqual(len(v), 2)
                    self.assertLess(v[0], v[1])

    def test_every_shipped_profile_applies_cleanly(self):
        for name in self.profiles:
            with self.subTest(profile=name):
                rig = Rig(nvidia=True)
                self.addCleanup(rig.close)
                rig.run.reply("--query-gpu=name", stdout=(
                    "NVIDIA GeForce RTX 4070 Laptop GPU, 44, 0, 0, 2.7, 95.00, "
                    "95.00, 5.00, 140.00, 210, 405, 1269, 8188, [N/A]"))
                code, out = rig.cli("profile", "apply", name)
                self.assertEqual(code, 0, out)
                self.assertNotIn("unknown setting", out)


class TestProfileApplication(RigTest):

    def test_settings_reach_the_hardware(self):
        write_config(self.rig, """
[profiles.t]
fan_mode = "silent"
cpu_pl1 = 30
max_perf_pct = 60
turbo = false
epp = "power"
charge_limit = 75
""")
        code, out = self.rig.cli("profile", "apply", "t")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rig.read(f"{A}/fan_mode"), "1")
        self.assertEqual(self.rig.read(
            "sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"), "30000000")
        self.assertEqual(self.rig.read(
            "sys/devices/system/cpu/intel_pstate/max_perf_pct"), "60")
        self.assertEqual(self.rig.read(
            "sys/devices/system/cpu/intel_pstate/no_turbo"), "1")
        self.assertEqual(self.rig.read(f"{A}/charge_limit"), "75")

    def test_a_refused_setting_is_skipped_and_the_rest_still_applies(self):
        write_config(self.rig, """
[profiles.t]
cpu_pl1 = 30
governor = "ondemand"
max_perf_pct = 60
""")
        code, out = self.rig.cli("profile", "apply", "t")
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)
        self.assertIn("governor", out)
        self.assertEqual(self.rig.read(
            "sys/devices/system/cpu/intel_pstate/max_perf_pct"), "60")

    def test_a_skipped_reason_is_one_line(self):
        write_config(self.rig, """
[profiles.t]
fan_mode = "warp_speed"
""")
        _, out = self.rig.cli("profile", "apply", "t")
        skipped = [l for l in out.splitlines() if "skipped" in l]
        self.assertEqual(len(skipped), 1)
        self.assertLess(len(skipped[0]), 200, "the summary should not be an essay")

    def test_unknown_keys_are_reported_rather_than_ignored(self):
        write_config(self.rig, """
[profiles.t]
cpu_pl1 = 30
cpu_p11 = 40
undervolt = -50
""")
        _, out = self.rig.cli("profile", "apply", "t")
        self.assertIn("unknown settings", out)
        self.assertIn("cpu_p11", out)
        self.assertIn("undervolt", out)

    def test_applying_a_profile_records_state_for_reset(self):
        write_config(self.rig, '[profiles.t]\ncpu_pl1 = 30\nmax_perf_pct = 55\n')
        self.rig.cli("profile", "apply", "t")
        self.rig.cli("reset")
        self.assertEqual(self.rig.read(
            "sys/devices/system/cpu/intel_pstate/max_perf_pct"), "100")
        self.assertEqual(self.rig.read(
            "sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"), "200000000")

    def test_dry_run_applies_nothing(self):
        write_config(self.rig, '[profiles.t]\nfan_mode = "gaming"\ncpu_pl1 = 30\n')
        code, out = self.rig.cli("--dry-run", "profile", "apply", "t")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out)
        self.assertEqual(self.rig.read(f"{A}/fan_mode"), "0")

    def test_a_curve_in_a_profile_is_validated_too(self):
        write_config(self.rig, """
[profiles.t]
fan_curve = [[40, 50], [50, 20]]
cpu_pl1 = 30
""")
        code, out = self.rig.cli("profile", "apply", "t")
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)
        self.assertIn("fan curve", out)
        self.assertEqual([w for w in self.rig.writes if w[0].startswith("fan_curve")], [])


class TestProfileErrors(RigTest):

    def test_unknown_profile_lists_the_defined_ones(self):
        write_config(self.rig, '[profiles.alpha]\ncpu_pl1 = 30\n[profiles.beta]\ncpu_pl1 = 40\n')
        code, out = self.rig.cli("profile", "apply", "nope")
        self.assertEqual(code, 1)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_show_on_an_unknown_profile_fails(self):
        write_config(self.rig, '[profiles.alpha]\ncpu_pl1 = 30\n')
        code, _ = self.rig.cli("profile", "show", "nope")
        self.assertEqual(code, 1)

    def test_broken_toml_names_the_file(self):
        p = write_config(self.rig, "[profiles.t\nbroken = ")
        code, out = self.rig.cli("profile", "list")
        self.assertEqual(code, 1)
        self.assertIn(str(p), out)

    def test_no_profiles_defined_is_not_an_error(self):
        write_config(self.rig, "[daemon]\ninterval = 2.0\n")
        code, out = self.rig.cli("profile", "list")
        self.assertEqual(code, 0)
        self.assertIn("no profiles", out)

    def test_a_broken_config_does_not_stop_status(self):
        write_config(self.rig, "not valid toml at all [[[")
        code, _ = self.rig.cli("status")
        self.assertEqual(code, 0, "sensors should still be readable")


if __name__ == "__main__":
    unittest.main()
