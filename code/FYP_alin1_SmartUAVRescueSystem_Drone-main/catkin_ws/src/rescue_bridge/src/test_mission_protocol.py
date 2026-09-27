import unittest

from mission_protocol import (
    LAND_REQUEST_SEMANTICS,
    PHASE_LAND_REQUESTED,
    MissionValidationError,
    append_rtl_waypoint,
    build_status_payload,
    normalize_multi_payload,
)


class MissionProtocolTests(unittest.TestCase):
    def test_zero_coordinates_are_valid_and_not_replaced_by_aliases(self):
        mission = normalize_multi_payload({
            "mission_id": "DEMO_USER/event_1",
            "waypoints": [
                {"latitude": 0, "longitude": 0, "lat": 22.0, "lon": 114.0},
            ],
            "return_to_launch": False,
        })
        self.assertEqual(mission["waypoints"], [(0.0, 0.0)])

    def test_return_to_launch_requires_a_json_boolean(self):
        for value in ("false", "true", 0, 1, None):
            with self.subTest(value=value):
                with self.assertRaises(MissionValidationError):
                    normalize_multi_payload({
                        "mission_id": "DEMO_USER/event_1",
                        "waypoints": [{"latitude": 22.0, "longitude": 114.0}],
                        "return_to_launch": value,
                    })

    def test_invalid_waypoint_rejects_whole_mission_instead_of_silent_drop(self):
        with self.assertRaises(MissionValidationError):
            normalize_multi_payload({
                "mission_id": "DEMO_USER/event_1",
                "waypoints": [
                    {"latitude": 22.0, "longitude": 114.0},
                    {"latitude": 999.0, "longitude": 114.0},
                ],
                "return_to_launch": False,
            })

    def test_malformed_mission_id_is_rejected(self):
        with self.assertRaises(MissionValidationError):
            normalize_multi_payload({
                "mission_id": "DEMO_USER//event_1",
                "waypoints": [{"latitude": 22.0, "longitude": 114.0}],
                "return_to_launch": False,
            })

    def test_rtl_append_is_explicit_and_counted(self):
        queue, appended = append_rtl_waypoint(
            [(22.0, 114.0), (22.1, 114.1)], True, (21.9, 113.9)
        )
        self.assertTrue(appended)
        self.assertEqual(len(queue), 3)
        self.assertEqual(queue[-1], (21.9, 113.9))

    def test_rtl_without_launch_fix_does_not_invent_a_coordinate(self):
        queue, appended = append_rtl_waypoint([(22.0, 114.0)], True, None)
        self.assertFalse(appended)
        self.assertEqual(queue, [(22.0, 114.0)])

    def test_landing_compatibility_status_has_precise_land_requested_semantics(self):
        payload = build_status_payload(
            "LANDING",
            "mission queue complete; LAND command request published",
            "DEMO_USER/event_1",
            waypoint_index=4,
            waypoint_total=4,
            queue_remaining=0,
            phase=PHASE_LAND_REQUESTED,
            land_command_requested=True,
        )
        self.assertEqual(payload["status"], "LANDING")
        self.assertEqual(payload["phase"], "LAND_REQUESTED")
        self.assertTrue(payload["land_command_requested"])
        self.assertFalse(payload["touchdown_confirmed"])
        self.assertEqual(payload["semantics"], LAND_REQUEST_SEMANTICS)


if __name__ == "__main__":
    unittest.main()
