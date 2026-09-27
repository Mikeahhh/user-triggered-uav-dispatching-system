import json
import unittest
from copy import deepcopy

from app_metadata import (
    APP_VERSION,
    PROTOCOL_SCHEMA_VERSION,
    SYSTEM_RELEASE_ID,
    WINDOWS_RELEASE_NAME,
)
from ground_station_demo import (
    DEMO_BANNER,
    DEMO_BOOKING_EVENT_ID,
    DEMO_BROKER_LABEL,
    DEMO_CONTACT_LABEL,
    DEMO_DATA_NOTICE,
    DEMO_DATABASE_STATUS,
    DEMO_EMERGENCY_CONTACT_LABEL,
    DEMO_EXPECTED_ORDER,
    DEMO_LOCATION_LABEL,
    DEMO_NOW_MS,
    DEMO_QUICK_EVENT_ID,
    DEMO_RAW_RECORDS_NOTICE,
    DEMO_SOS_EVENT_ID,
    DEMO_TEST_ONLY_WAIT_THRESHOLD_MS,
    DEMO_TEST_PARAMETER_NOTICE,
    build_demo_drone_status,
    build_demo_onboard_summary,
    build_demo_ordered_rescue_events,
    build_demo_pending_alerts,
    build_demo_rescue_events,
    build_demo_users_data,
)


class ReleaseMetadataTests(unittest.TestCase):
    def test_v7_metadata_is_centralized_and_consistent(self):
        self.assertEqual(APP_VERSION, "0.4.0")
        self.assertEqual(WINDOWS_RELEASE_NAME, "ground_station_V7")
        self.assertEqual(SYSTEM_RELEASE_ID, "MASS26-20260806")
        self.assertEqual(PROTOCOL_SCHEMA_VERSION, 1)


class OfflineDemoFixtureTests(unittest.TestCase):
    def test_contains_workflow_records_and_three_rescue_event_classes(self):
        users = build_demo_users_data()
        self.assertEqual(set(users), {"DEMO_USER"})
        user_data = users["DEMO_USER"]
        self.assertEqual(
            set(user_data),
            {
                "profile",
                "QuickStartSessions",
                "booked_events",
                "rescue_requests",
                "rescue_alerts",
                "rescue_events",
            },
        )
        self.assertEqual(len(user_data["QuickStartSessions"]), 1)
        self.assertEqual(len(user_data["booked_events"]), 2)
        self.assertEqual(len(user_data["rescue_requests"]), 1)
        self.assertEqual(len(user_data["rescue_alerts"]), 3)
        self.assertEqual(len(user_data["rescue_events"]), 3)
        self.assertEqual(
            {event["trigger_type"] for event in build_demo_rescue_events(users)},
            {"SOS", "EVENT_BOOKING_TIMEOUT", "QUICK_START_LOCATION_TIMEOUT"},
        )

    def test_demo_contains_one_pending_alert_and_converted_sources(self):
        users = build_demo_users_data()["DEMO_USER"]
        alerts = users["rescue_alerts"]
        pending = build_demo_pending_alerts({"DEMO_USER": users})
        self.assertEqual(
            [alert["alert_id"] for alert in pending],
            ["event_timeout__demo_event_pending_002"],
        )
        self.assertEqual(pending[0]["stage"], "RESOLUTION")
        self.assertEqual(pending[0]["emergency_contact_result"], "NOT_AVAILABLE")
        self.assertEqual(
            alerts["event_timeout__demo_event_001"]["status"], "CONVERTED"
        )
        self.assertEqual(
            alerts["quick_location_timeout__demo_session_001__1788580710000"][
                "status"
            ],
            "CONVERTED",
        )

    def test_fixed_test_only_time_orders_one_unified_queue(self):
        self.assertEqual(DEMO_NOW_MS, 1_788_580_860_000)
        self.assertEqual(DEMO_TEST_ONLY_WAIT_THRESHOLD_MS, 60_000)
        ordered = build_demo_ordered_rescue_events()
        self.assertEqual(
            tuple(event["event_id"] for event in ordered), DEMO_EXPECTED_ORDER
        )
        self.assertEqual(
            [event["effective_priority"] for event in ordered],
            ["HIGH", "NORMAL", "NORMAL"],
        )
        self.assertEqual(
            [event["queue_waiting_time_ms"] for event in ordered],
            [60_000, 40_000, 50_000],
        )
        self.assertEqual(
            [event["abnormal_duration_ms"] for event in ordered],
            [360_000, 150_000, None],
        )
        self.assertEqual(
            set(DEMO_EXPECTED_ORDER),
            {DEMO_BOOKING_EVENT_ID, DEMO_SOS_EVENT_ID, DEMO_QUICK_EVENT_ID},
        )

    def test_raw_records_are_read_only_and_never_direct_dispatch_targets(self):
        user = build_demo_users_data()["DEMO_USER"]
        for group_name in (
            "QuickStartSessions",
            "booked_events",
            "rescue_requests",
        ):
            for record in user[group_name].values():
                self.assertTrue(record["demo_read_only"])
                self.assertFalse(record["direct_dispatch_allowed"])

        self.assertEqual(len(user["QuickStartSessions"]["demo_session_001"]["points"]), 4)
        self.assertEqual(len(user["booked_events"]["demo_event_001"]["waypoints"]), 3)
        self.assertEqual(user["rescue_requests"]["demo_sos_001"]["demo_search_points"], 19)

    def test_demo_contacts_are_explicitly_non_dialable(self):
        profile = build_demo_users_data()["DEMO_USER"]["profile"]
        self.assertEqual(profile["phone"], DEMO_CONTACT_LABEL)
        self.assertEqual(profile["emergency_contacts"], [])
        self.assertEqual(
            profile["emergency_contact_status"], DEMO_EMERGENCY_CONTACT_LABEL
        )
        self.assertIn("NOT DIALABLE", profile["phone"])
        self.assertEqual(profile["emergency_contact_status"], "NOT_AVAILABLE")

    def test_callers_receive_an_isolated_copy(self):
        first = build_demo_users_data()
        first["DEMO_USER"]["booked_events"].clear()
        first["DEMO_USER"]["rescue_events"].clear()
        second = build_demo_users_data()
        self.assertEqual(len(second["DEMO_USER"]["booked_events"]), 2)
        self.assertEqual(len(second["DEMO_USER"]["rescue_events"]), 3)

    def test_queue_calculation_does_not_mutate_caller_data(self):
        users = build_demo_users_data()
        before = deepcopy(users)
        queue = build_demo_ordered_rescue_events(users)
        queue[0]["status"] = "CHANGED_BY_CALLER"
        self.assertEqual(users, before)
        self.assertNotIn(
            "effective_priority",
            users["DEMO_USER"]["rescue_events"][DEMO_BOOKING_EVENT_ID],
        )

    def test_visible_demo_text_has_required_boundaries_and_no_endpoint(self):
        visible_text = "\n".join(
            [
                DEMO_BANNER,
                DEMO_DATA_NOTICE,
                DEMO_BROKER_LABEL,
                DEMO_DATABASE_STATUS,
                DEMO_LOCATION_LABEL,
                DEMO_RAW_RECORDS_NOTICE,
                DEMO_TEST_PARAMETER_NOTICE,
                DEMO_CONTACT_LABEL,
                DEMO_EMERGENCY_CONTACT_LABEL,
                build_demo_drone_status(),
                build_demo_onboard_summary(),
            ]
        )
        self.assertIn("NO FIREBASE / NO MQTT / NO PHYSICAL UAV", visible_text)
        self.assertIn("LOCAL DEMO DISPATCH: DISABLED", visible_text)
        self.assertIn("NO DIRECT DISPATCH", visible_text)
        self.assertIn("NOT DIALABLE", visible_text)
        self.assertIn("not operational or validated values", visible_text.lower())
        self.assertIn("no physical UAV or touchdown", visible_text)
        self.assertIn("ACK_PUBLISHED", visible_text)
        self.assertNotIn("http://", visible_text)
        self.assertNotIn("https://", visible_text)
        self.assertNotRegex(visible_text, r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    def test_fixture_uses_demo_ids_and_not_known_experimental_ids(self):
        serialized = json.dumps(build_demo_users_data(), sort_keys=True)
        for experimental_id in (
            "26080101",
            "event_1",
            "session_2",
            "1785558519098",
        ):
            self.assertNotIn(experimental_id, serialized)
        self.assertIn("demo_event_001", serialized)
        self.assertIn("demo_session_001", serialized)
        self.assertIn("demo_sos_001", serialized)


if __name__ == "__main__":
    unittest.main()
