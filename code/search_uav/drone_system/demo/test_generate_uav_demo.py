import json
import tempfile
import unittest
from pathlib import Path

from generate_uav_demo import generate


class UavDemoTests(unittest.TestCase):
    def test_demo_is_hardware_free_and_exposes_land_request_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / 'new-demo'
            state = generate(output)
            dashboard = (output / "uav_demo_dashboard.html").read_text(encoding="utf-8")
            state_disk = json.loads((output / "uav_demo_state.json").read_text(encoding="utf-8"))

            self.assertFalse(state["demo"]["physical_uav_connected"])
            self.assertFalse(state["demo"]["ros_started"])
            self.assertFalse(state["demo"]["camera_invoked"])
            self.assertEqual(state["status"]["phase"], "LAND_REQUESTED")
            self.assertTrue(state["status"]["land_command_requested"])
            self.assertTrue(state["status"]["all_waypoints_completed"])
            self.assertTrue(state["status"]["delivery_eligible"])
            self.assertEqual(state["status"]["waypoint_index"], 3)
            self.assertFalse(state["status"]["touchdown_confirmed"])
            self.assertEqual(state["mission"]["source_waypoints"], 3)
            self.assertEqual(state["mission"]["queue_total"], 4)
            self.assertEqual(state["record"]["delivery_state"], "ACKNOWLEDGED_BY_GS")
            self.assertEqual(state_disk, state)
            self.assertIn("NO PHYSICAL UAV", dashboard)
            self.assertIn("touchdown_confirmed", dashboard)
            self.assertNotIn("serviceAccountKey", dashboard)
            self.assertTrue((output / "SHA256SUMS.txt").is_file())
            before = (output / 'uav_demo_state.json').read_bytes()
            with self.assertRaises(FileExistsError):
                generate(output)
            self.assertEqual((output / 'uav_demo_state.json').read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
