import copy
import unittest
from unittest.mock import patch
from test_ground_station_rescue_flow import gs, _event, _FirebaseReference, NOW_MS
from rescue_event_manager import (
    detect_pending_sos, record_user_contact, convert_alert_to_rescue_event,
    SAFETY_UNCONFIRMED, require_sos_verification, scan_user_records,
)
from active_event_queue import current_event


class SosVerificationTests(unittest.TestCase):
    def setUp(self):
        self.request = {"status": "PENDING", "latitude": 22.4, "longitude": 114.3}
        self.user = {"rescue_requests": {"request_1": copy.deepcopy(self.request)}}

    def test_unverified_legacy_event_cannot_be_prepared_or_selected(self):
        event = _event("sos__request_1", source_alert_id=None, search_confirmed_at_ms=None)
        self.user["rescue_events"] = {event["event_id"]: event}
        with self.assertRaises(ValueError):
            gs.prepare_mission_for_rescue_event(self.user, event)
        with self.assertRaises(ValueError):
            current_event(self.user, "USER_A", event["event_id"])

    def test_unverified_legacy_pending_event_returns_to_contact_review(self):
        event = _event("sos__request_1", source_alert_id=None, search_confirmed_at_ms=None)
        self.user["rescue_events"] = {event["event_id"]: event}
        scan = scan_user_records("USER_A", self.user, NOW_MS)
        alert = scan["alerts"]["sos_review__request_1"]
        resolved = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        _, verified = convert_alert_to_rescue_event(resolved, NOW_MS, True, self.user["rescue_events"])
        require_sos_verification(verified)
        self.assertEqual(verified["event_id"], event["event_id"])
        route = gs.prepare_mission_for_rescue_event(self.user, verified)
        self.assertEqual(len(route["waypoints"]), 19)

    def test_confirmation_cancelled_does_not_create_event(self):
        alert = detect_pending_sos("USER_A", "request_1", self.request, NOW_MS)
        alert = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        self.user["rescue_alerts"] = {alert["alert_id"]: alert}
        root = _FirebaseReference({"users": {"USER_A": self.user}})
        with patch.object(gs.messagebox, "askyesno", return_value=False):
            self.assertFalse(gs._convert_alert_atomically(root, "USER_A", alert["alert_id"], self.user, alert))
        self.assertNotIn("rescue_events", root.data["users"]["USER_A"])

    def test_cancelled_request_cannot_be_confirmed_from_stale_alert(self):
        alert = detect_pending_sos("USER_A", "request_1", self.request, NOW_MS)
        alert = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        self.user["rescue_alerts"] = {alert["alert_id"]: alert}
        self.user["rescue_requests"]["request_1"]["status"] = "CANCELLED"
        root = _FirebaseReference({"users": {"USER_A": self.user}})
        with patch.object(gs.messagebox, "askyesno", return_value=True):
            with self.assertRaisesRegex(ValueError, "no longer pending"):
                gs._convert_alert_atomically(root, "USER_A", alert["alert_id"], self.user, alert)
        self.assertNotIn("rescue_events", root.data["users"]["USER_A"])
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            gs.prepare_mission_for_rescue_event(self.user, _event("sos__request_1"))

    def test_malformed_confirmation_does_not_allow_dispatch(self):
        event = _event("sos__request_1", user_contact_result="SAFE_CONFIRMED")
        with self.assertRaises(ValueError):
            require_sos_verification(event)
        event = _event("sos__request_1", source_alert_id="sos_review__another_request")
        with self.assertRaises(ValueError):
            require_sos_verification(event)


if __name__ == "__main__":
    unittest.main(verbosity=2)
