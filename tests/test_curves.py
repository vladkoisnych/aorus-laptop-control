"""Fan curve parsing and encoding.

The firmware curve is written point by point, so a curve that fails validation
half way through would leave the hardware with a mixture of old and new points.
Validation therefore has to complete before the first write.
"""

import unittest

from harness import RigTest


class TestCurveValidation(RigTest):

    def assertNoWrites(self):
        self.assertEqual(self.rig.writes, [],
                         f"a rejected curve still wrote {self.rig.writes}")

    def test_empty_curve_is_refused(self):
        with self.assertRaises(self.ac.Err):
            self.rig.fans.set_curve([])
        self.assertNoWrites()

    def test_more_than_fifteen_points_is_refused(self):
        pts = [(i * 5, i * 5) for i in range(16)]
        with self.assertRaises(self.ac.Err) as cm:
            self.rig.fans.set_curve(pts)
        self.assertIn("15", str(cm.exception))
        self.assertNoWrites()

    def test_speed_going_backwards_is_refused_before_any_write(self):
        with self.assertRaises(self.ac.Err) as cm:
            self.rig.fans.set_curve([(40, 30), (50, 40), (60, 20), (70, 60)])
        self.assertIn("non-decreasing", str(cm.exception))
        self.assertNoWrites()

    def test_a_late_bad_point_still_writes_nothing(self):
        # The offending point is last, so a validate-as-you-go implementation
        # would have written the first fourteen before noticing.
        pts = [(t, t) for t in range(10, 80, 5)] + [(85, 1)]
        with self.assertRaises(self.ac.Err):
            self.rig.fans.set_curve(pts)
        self.assertNoWrites()

    def test_temperatures_and_speeds_are_clamped(self):
        self.rig.fans.set_curve([(-20, -5), (50, 50), (300, 400)])
        data = [int(v) for k, v in self.rig.writes if k == "fan_curve_data"]
        temps = [d & 0xFF for d in data]
        speeds = [d >> 8 for d in data]
        self.assertEqual(min(temps), 0)
        self.assertEqual(max(temps), 100)
        self.assertLessEqual(max(speeds), self.ac.FAN_DUTY_MAX)


class TestCurveEncoding(RigTest):

    def test_fifteen_points_are_always_written(self):
        self.rig.fans.set_curve([(40, 20), (60, 50), (80, 90)])
        idx = [v for k, v in self.rig.writes if k == "fan_curve_index"]
        data = [v for k, v in self.rig.writes if k == "fan_curve_data"]
        # The first fifteen index writes belong to the snapshot of the old
        # curve, since reading a point requires selecting it first.
        self.assertEqual(idx[-15:], [str(i) for i in range(15)])
        self.assertEqual(len(data), 15)

    def test_the_old_curve_is_only_snapshotted_once(self):
        self.rig.fans.set_curve([(40, 20), (60, 50)])
        first = len([k for k, _ in self.rig.writes if k == "fan_curve_index"])
        self.rig.writes.clear()
        self.rig.fans.set_curve([(45, 25), (65, 55)])
        second = len([k for k, _ in self.rig.writes if k == "fan_curve_index"])
        self.assertEqual(first, 30, "expected a snapshot plus fifteen writes")
        self.assertEqual(second, 15, "the second set should not re-read the curve")

    def test_reading_the_curve_returns_percentages(self):
        pts = self.rig.fans.curve()
        self.assertEqual(len(pts), 15)
        for _, duty in pts:
            self.assertLessEqual(duty, 100)

    def test_short_curves_are_padded_with_the_last_point(self):
        self.rig.fans.set_curve([(40, 20), (60, 50)])
        data = [int(v) for k, v in self.rig.writes if k == "fan_curve_data"]
        last = data[-1]
        self.assertEqual(last & 0xFF, 60)
        self.assertEqual(data[2:], [last] * 13)

    def test_encoding_packs_speed_high_and_temperature_low(self):
        self.rig.fans.set_curve([(55, 50)])
        first = int([v for k, v in self.rig.writes if k == "fan_curve_data"][0])
        self.assertEqual(first & 0xFF, 55)
        self.assertEqual(first >> 8, round(50 * self.ac.FAN_DUTY_MAX / 100))

    def test_full_speed_point_stays_inside_the_ec_range(self):
        self.rig.fans.set_curve([(90, 100)])
        first = int([v for k, v in self.rig.writes if k == "fan_curve_data"][0])
        self.assertEqual(first >> 8, self.ac.FAN_DUTY_MAX)
        self.assertLess(first >> 8, 255)

    def test_points_are_sorted_before_writing(self):
        self.rig.fans.set_curve([(80, 90), (40, 20), (60, 50)])
        data = [int(v) for k, v in self.rig.writes if k == "fan_curve_data"]
        temps = [d & 0xFF for d in data[:3]]
        self.assertEqual(temps, [40, 60, 80])


class TestCurveCli(RigTest):

    def test_a_valid_curve_switches_to_custom_mode(self):
        code, out = self.rig.cli("fan", "curve", "45:15,55:25,65:40,80:70,92:100")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rig.fans.mode(), "custom")
        self.assertIn("5 curve points", out)

    def test_a_malformed_curve_is_an_error_not_a_traceback(self):
        for bad in ("garbage", "45:15,nonsense", "45-15", "45:15,60:", ":50", "45:x"):
            with self.subTest(bad=bad):
                rig = type(self.rig)()
                self.addCleanup(rig.close)
                code, out = rig.cli("fan", "curve", bad)
                self.assertEqual(code, 1, f"{bad!r} should fail cleanly: {out}")
                self.assertNotIn("Traceback", out)
                self.assertEqual(rig.writes, [], f"{bad!r} wrote {rig.writes}")

    def test_a_backwards_curve_from_the_cli_writes_nothing(self):
        code, out = self.rig.cli("fan", "curve", "40:50,50:20")
        self.assertEqual(code, 1)
        self.assertIn("non-decreasing", out)
        self.assertEqual(self.rig.writes, [])


if __name__ == "__main__":
    unittest.main()
