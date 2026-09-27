import math
import unittest

from sos_pattern import generate_grid, generate_spiral, waypoints_to_mqtt_payload


class SosPatternTests(unittest.TestCase):
    def test_default_spiral_produces_the_expected_19_search_points(self):
        points = generate_spiral(0.0, 0.0, radius_m=200.0, spacing_m=30.0)
        self.assertEqual(len(points), 19)
        self.assertEqual(points[0], {"latitude": 0.0, "longitude": 0.0})

    def test_grid_covers_both_edges_with_bounded_spacing(self):
        width_m = 300.0
        requested_spacing_m = 40.0
        points = generate_grid(
            0.0,
            0.0,
            width_m=width_m,
            spacing_m=requested_spacing_m,
        )
        sweep_longitudes = [points[index]["longitude"] for index in range(0, len(points), 2)]
        east_offsets_m = [value * 111320.0 for value in sweep_longitudes]
        self.assertAlmostEqual(east_offsets_m[0], -width_m / 2.0, places=6)
        self.assertAlmostEqual(east_offsets_m[-1], width_m / 2.0, places=6)
        gaps = [right - left for left, right in zip(east_offsets_m, east_offsets_m[1:])]
        self.assertTrue(all(gap <= requested_spacing_m + 1e-9 for gap in gaps))

    def test_rejects_invalid_coordinates_and_nonfinite_dimensions(self):
        for latitude, longitude in ((91, 0), (0, 181), (math.nan, 0), (90, 0)):
            with self.assertRaises(ValueError):
                generate_spiral(latitude, longitude)
        with self.assertRaises(ValueError):
            generate_grid(0, 0, width_m=math.inf)
        with self.assertRaises(ValueError):
            generate_grid(0, 0, spacing_m=0)

    def test_payload_preserves_explicit_bounded_mission_fields(self):
        waypoints = generate_spiral(0, 0, radius_m=30, spacing_m=30)
        payload = waypoints_to_mqtt_payload(
            waypoints,
            mission_id="DEMO_USER/demo_sos_001",
            altitude=5.0,
            hover_seconds=5.0,
            return_to_launch=True,
        )
        self.assertEqual(payload["mission_type"], "rescue")
        self.assertEqual(payload["mission_id"], "DEMO_USER/demo_sos_001")
        self.assertEqual(payload["waypoints"], waypoints)
        self.assertTrue(payload["return_to_launch"])


if __name__ == "__main__":
    unittest.main()
