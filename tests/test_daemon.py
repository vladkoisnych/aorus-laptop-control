"""The fan daemon's decision logic and its handover behaviour.

The curve maths is pure, so it is tested directly rather than by running the
loop and watching the clock.
"""

import unittest

from harness import RigTest, load_module

CURVE = [(0, 0), (45, 0), (55, 22), (65, 35), (72, 48), (80, 65), (86, 85), (92, 100)]


class TestCurveInterpolation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ac = load_module()

    def speed(self, t):
        return self.ac.curve_speed(CURVE, t)

    def test_exact_points_return_their_own_duty(self):
        for t, s in CURVE:
            self.assertAlmostEqual(self.speed(t), s, msg=f"at {t}C")

    def test_between_points_is_linear(self):
        # halfway from (55, 22) to (65, 35)
        self.assertAlmostEqual(self.speed(60), 28.5)

    def test_below_the_first_point_is_flat(self):
        self.assertEqual(self.speed(-40), 0)
        self.assertEqual(self.speed(10), 0)

    def test_above_the_last_point_is_flat(self):
        self.assertEqual(self.speed(120), 100)

    def test_an_empty_curve_does_not_divide_by_zero(self):
        self.assertEqual(self.ac.curve_speed([], 70), 0)

    def test_a_repeated_temperature_does_not_divide_by_zero(self):
        self.assertEqual(self.ac.curve_speed([(50, 20), (50, 60), (80, 90)], 50), 20)


class TestDaemonTarget(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ac = load_module()

    def target(self, t, applied=None, **kw):
        return self.ac.daemon_target(CURVE, t, applied, **kw)

    def test_a_whole_percentage_comes_back(self):
        v = self.target(63)
        self.assertIsInstance(v, int)
        self.assertGreaterEqual(v, 0)
        self.assertLessEqual(v, 100)

    def test_the_guard_overrides_the_curve(self):
        self.assertEqual(self.target(93, guard_temp=92, guard_speed=100), 100)

    def test_the_guard_ignores_the_floor_and_the_previous_value(self):
        self.assertEqual(self.target(95, applied=20, min_speed=0), 100)

    def test_the_floor_applies_below_the_guard(self):
        self.assertEqual(self.target(20, min_speed=15), 15)
        self.assertEqual(self.target(20, min_speed=0), 0)

    def test_duty_rises_immediately(self):
        self.assertGreater(self.target(80, applied=20), 20)

    def test_duty_holds_when_the_temperature_dips_slightly(self):
        # 65 -> 35%, and 65 + 3 hysteresis still asks for more than 40
        self.assertEqual(self.target(70, applied=44, hysteresis=3), 44)

    def test_duty_drops_once_the_temperature_really_falls(self):
        self.assertLess(self.target(50, applied=65, hysteresis=3), 65)

    def test_hysteresis_of_zero_tracks_the_curve_exactly(self):
        self.assertEqual(self.target(65, applied=65, hysteresis=0), 35)

    def test_a_curve_asking_for_more_than_full_is_clamped(self):
        self.assertEqual(self.ac.daemon_target([(0, 0), (50, 400)], 60, None), 100)


class TestDaemonCleanup(RigTest):
    """ExecStopPost must not undo a mode the user just chose."""

    def test_a_kill_hands_the_fans_back(self):
        self.rig.fans.set_mode("fixed")
        code, out = self.rig.cli("daemon-cleanup")
        self.assertEqual(code, 0)
        self.assertEqual(self.rig.fans.mode(), "normal")
        self.assertIn("fan mode -> normal", out)

    def test_a_deliberate_handover_leaves_the_chosen_mode_alone(self):
        self.rig.fans.set_mode("fixed")
        self.ac.YIELD_FLAG.parent.mkdir(parents=True, exist_ok=True)
        self.ac.YIELD_FLAG.write_text("silent")
        self.rig.fans.set_mode("silent")
        code, out = self.rig.cli("daemon-cleanup")
        self.assertEqual(code, 0)
        self.assertEqual(self.rig.fans.mode(), "silent")
        self.assertIn("silent", out)

    def test_the_flag_is_consumed(self):
        self.ac.YIELD_FLAG.parent.mkdir(parents=True, exist_ok=True)
        self.ac.YIELD_FLAG.write_text("gaming")
        self.rig.cli("daemon-cleanup")
        self.assertFalse(self.ac.YIELD_FLAG.exists())

    def test_cleanup_without_a_fan_driver_is_harmless(self):
        from harness import Rig
        rig = Rig(fans=False)
        self.addCleanup(rig.close)
        self.assertEqual(rig.cli("daemon-cleanup")[0], 0)


class TestDaemonConfig(RigTest):

    def test_the_shipped_daemon_curve_is_usable(self):
        cfg = self.ac.load_config()["daemon"]
        curve = sorted((float(t), float(s)) for t, s in cfg["curve"])
        self.assertGreaterEqual(len(curve), 2)
        duties = [s for _, s in curve]
        self.assertEqual(duties, sorted(duties), "curve duty goes backwards")
        self.assertLessEqual(max(duties), 100)
        self.assertLessEqual(cfg["guard_temp"], 100)
        self.assertEqual(self.ac.daemon_target(curve, cfg["guard_temp"] + 1, None,
                                               guard_temp=cfg["guard_temp"],
                                               guard_speed=cfg["guard_speed"]),
                         int(cfg["guard_speed"]))

    def test_daemon_refuses_to_start_without_a_fan_driver(self):
        from harness import Rig
        rig = Rig(fans=False)
        self.addCleanup(rig.close)
        code, out = rig.cli("daemon")
        self.assertEqual(code, 1)
        self.assertIn("unavailable", out)


if __name__ == "__main__":
    unittest.main()
