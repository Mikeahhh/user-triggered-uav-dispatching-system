import importlib.util
from pathlib import Path
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flight_clearance", ROOT / "simulation/flight_clearance.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
flight_clearance = module.flight_clearance


class FlightClearanceTests(unittest.TestCase):
    def test_clearance_uses_aircraft_altitude_not_ground_elevation(self):
        terrain = lambda east, north: np.full_like(east, 100)
        result = flight_clearance([0, 10], [0, 0], [102, 102], terrain)
        self.assertTrue(result["passed"])
        self.assertEqual(result["minimum_clearance_m"], 2)
        self.assertFalse(flight_clearance([0, 10], [0, 0], [101.999, 101.999], terrain)["passed"])

    def test_interior_ridge_is_checked_even_when_endpoints_clear(self):
        terrain = lambda east, north: 10 * np.maximum(0, 1 - np.abs(east - 5) / 5)
        result = flight_clearance([0, 10], [0, 0], [10, 10], terrain)
        self.assertFalse(result["passed"])
        self.assertEqual(result["minimum_clearance_m"], 0)
        self.assertEqual(result["sample_count"], 3)
        self.assertLessEqual(result["maximum_sample_spacing_m"], 5)

    def test_hover_and_invalid_terrain(self):
        flat = lambda east, north: np.zeros_like(east)
        self.assertTrue(flight_clearance([0, 0], [0, 0], [2, 2], flat)["passed"])
        void = lambda east, north: np.full_like(east, np.nan)
        result = flight_clearance([0, 10], [0, 0], [100, 100], void)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["minimum_clearance_m"])

    def test_invalid_limits_and_coordinates_are_rejected(self):
        flat = lambda east, north: np.zeros_like(east)
        for altitude, minimum, step in (([2, np.nan], 2, 5), ([2, 2], 0, 5), ([2, 2], 2, 0)):
            with self.subTest(altitude=altitude, minimum=minimum, step=step):
                with self.assertRaises(ValueError):
                    flight_clearance([0, 10], [0, 0], altitude, flat, minimum, step)


if __name__ == "__main__":
    unittest.main()
