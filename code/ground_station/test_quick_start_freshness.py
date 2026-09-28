import copy
import unittest

from rescue_event_manager import (
    CONTACT_USER, PENDING, QUICK_START_SESSIONS, RESOLUTION,
    SAFETY_UNCONFIRMED, NOT_AVAILABLE, convert_alert_to_rescue_event,
    record_user_contact, scan_user_records,
)
from rescue_repository import reconcile_user


USER = "SYNTHETIC_USER"
SESSION = "synthetic_session"
TIMEOUT = 100


def point(timestamp, latitude=22.3, longitude=114.1):
    return {"latitude": latitude, "longitude": longitude, "timestamp": timestamp}


def user_record(points=None):
    return {QUICK_START_SESSIONS: {SESSION: {
        "status": "ACTIVE", "startTime": 1000,
        "points": points or {},
    }}}


class Database:

    def __init__(self, user, retry=False, fail=False):
        self.user = copy.deepcopy(user)
        self.retry = retry
        self.fail = fail

    def child(self, _name):
        return self

    def transaction(self, callback):
        if self.retry:
            callback(copy.deepcopy(self.user))
        updated = callback(copy.deepcopy(self.user))
        if self.fail:
            raise RuntimeError("synthetic commit failure")
        self.user = copy.deepcopy(updated)
        return copy.deepcopy(updated)


class LocationFreshnessTests(unittest.TestCase):
    def scan(self, user, now, timeout=TIMEOUT):
        database = Database(user, retry=True)
        committed, result = reconcile_user(
            database, USER, now, {"quick_start_timeout_ms": timeout})
        self.assertEqual(result["issues"], [])
        return committed, result

    def state(self, user):
        return user["quick_start_monitoring"][SESSION]

    def single_alert(self, user):
        self.assertEqual(len(user["rescue_alerts"]), 1)
        return next(iter(user["rescue_alerts"].values()))

    def test_fresh_stationary_samples_do_not_alert(self):
        user, _ = self.scan(user_record({"a": point(1000), "b": point(1199)}), 1200)
        self.assertEqual(user["rescue_alerts"], {})
        self.assertEqual(self.state(user)["latest_sample_at_ms"], 1199)
        self.assertEqual(self.state(user)["monitoring_status"], "FRESH")

    def test_threshold_before_equal_and_after(self):
        for now, expected in ((1099, 0), (1100, 1), (1101, 1)):
            with self.subTest(now=now):
                user, _ = self.scan(user_record({"a": point(1000)}), now)
                self.assertEqual(len(user["rescue_alerts"]), expected)
                if expected:
                    alert = self.single_alert(user)
                    self.assertEqual(alert["abnormal_since_ms"], 1100)
                    self.assertEqual(alert["sample_at_ms"], 1000)
                    self.assertEqual(alert["timeout_ms"], TIMEOUT)

    def test_no_valid_sample_is_not_ready_and_has_no_invented_time(self):
        for points in ({}, {"old": point(999)}, {"bad": point(True)},
                       {"bad": {"latitude": 22.3, "longitude": 114.1}}):
            with self.subTest(points=points):
                user, _ = self.scan(user_record(points), 1200)
                self.assertEqual(self.state(user)["monitoring_status"], "NOT_READY")
                self.assertIsNone(self.state(user).get("latest_sample_at_ms"))
                self.assertEqual(user["rescue_alerts"], {})

    def test_duplicate_out_of_order_and_stale_backfill_keep_episode(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        original = copy.deepcopy(self.state(user))
        alert_id = self.single_alert(user)["alert_id"]
        user[QUICK_START_SESSIONS][SESSION]["points"].update({
            "duplicate": point(1000), "backfill": point(1020), "old": point(1005)})
        user, result = self.scan(user, 1200)
        state = self.state(user)
        self.assertEqual(state["latest_sample_at_ms"], 1020)
        self.assertEqual(state["episode_id"], original["episode_id"])
        self.assertEqual(state["episode_first_onset_ms"], 1100)
        self.assertEqual(self.single_alert(user)["alert_id"], alert_id)
        self.assertEqual(self.single_alert(user)["abnormal_since_ms"], 1120)
        self.assertEqual(result["alerts"], {})
        user[QUICK_START_SESSIONS][SESSION]["points"] = {"older": point(1001)}
        user, _ = self.scan(user, 1300)
        self.assertEqual(self.state(user)["latest_sample_at_ms"], 1020)

    def test_recovery_retains_human_state_and_reuses_pending_alert(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        alert = self.single_alert(user)
        alert_id = alert["alert_id"]
        user["rescue_alerts"][alert_id] = record_user_contact(
            alert, SAFETY_UNCONFIRMED, False)
        user[QUICK_START_SESSIONS][SESSION]["points"]["fresh"] = point(1190)
        user, _ = self.scan(user, 1200)
        recovered = self.single_alert(user)
        self.assertEqual(recovered["status"], PENDING)
        self.assertEqual(recovered["stage"], RESOLUTION)
        self.assertEqual(recovered["location_status"], "RECOVERED")
        self.assertEqual(recovered["abnormal_since_ms"], 1100)
        user, result = self.scan(user, 1290)
        recurrent = self.single_alert(user)
        self.assertEqual(recurrent["alert_id"], alert_id)
        self.assertEqual(recurrent["abnormal_since_ms"], 1290)
        self.assertEqual(recurrent["location_episode_count"], 2)
        self.assertEqual(recurrent["stage"], RESOLUTION)
        self.assertEqual(result["alerts"], {})
        _, event = convert_alert_to_rescue_event(recurrent, 1300, True)
        self.assertEqual(event["created_at_ms"], 1300)
        self.assertEqual(event["abnormal_since_ms"], 1290)

    def test_future_sample_is_not_laundered_when_clock_catches_up(self):
        user, _ = self.scan(user_record({"future": point(1100)}), 1010)
        self.assertEqual(self.state(user)["monitoring_status"], "NOT_READY")
        self.assertEqual(len(self.state(user)["rejected_future_samples"]), 1)
        user, _ = self.scan(user, 1110)
        self.assertEqual(self.state(user)["monitoring_status"], "NOT_READY")
        user[QUICK_START_SESSIONS][SESSION]["points"]["new_key"] = point(1100)
        user, _ = self.scan(user, 1111)
        self.assertEqual(self.state(user)["monitoring_status"], "NOT_READY")
        user[QUICK_START_SESSIONS][SESSION]["points"]["future"] = point(1112)
        user, _ = self.scan(user, 1113)
        self.assertEqual(self.state(user)["latest_sample_at_ms"], 1112)

    def test_completed_session_never_creates_alert(self):
        user = user_record({"a": point(1000)})
        user[QUICK_START_SESSIONS][SESSION]["status"] = "COMPLETED"
        user, _ = self.scan(user, 1500)
        self.assertEqual(user["rescue_alerts"], {})

    def test_legacy_pending_alert_blocks_second_type_without_mutation(self):
        user = user_record({"a": point(1000)})
        legacy = {"alert_id": "quick_inactivity__synthetic_session__1000",
                  "user_id": USER, "primary_record_type": QUICK_START_SESSIONS,
                  "primary_record_id": SESSION, "trigger_type": "QUICK_START_INACTIVITY",
                  "status": PENDING, "stage": CONTACT_USER, "abnormal_since_ms": 1000}
        user["rescue_alerts"] = {legacy["alert_id"]: copy.deepcopy(legacy)}
        user, result = self.scan(user, 1500)
        self.assertEqual(user["rescue_alerts"], {legacy["alert_id"]: legacy})
        self.assertEqual(result["alerts"], {})

    def test_converted_event_is_not_cancelled_or_recreated_on_recovery(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        alert = self.single_alert(user)
        alert.update(stage=RESOLUTION, user_contact_result=SAFETY_UNCONFIRMED,
                     emergency_contact_result=NOT_AVAILABLE)
        updated, event = convert_alert_to_rescue_event(alert, 1105, True)
        user["rescue_alerts"][alert["alert_id"]] = updated
        event["status"] = "DISPATCHED"
        user["rescue_events"] = {event["event_id"]: copy.deepcopy(event)}
        user[QUICK_START_SESSIONS][SESSION]["points"]["fresh"] = point(1200)
        user, _ = self.scan(user, 1201)
        user, _ = self.scan(user, 1300)
        self.assertEqual(user["rescue_events"], {event["event_id"]: event})
        self.assertEqual(len(user["rescue_alerts"]), 1)

    def test_closed_safe_stale_episode_waits_for_genuine_recovery(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        self.single_alert(user)["status"] = "CLOSED_SAFE"
        user, _ = self.scan(user, 1200)
        self.assertEqual(len(user["rescue_alerts"]), 1)
        user[QUICK_START_SESSIONS][SESSION]["points"]["fresh"] = point(1299)
        user, _ = self.scan(user, 1300)
        user, _ = self.scan(user, 1399)
        self.assertEqual(len(user["rescue_alerts"]), 2)

    def test_configuration_change_does_not_reinterpret_open_episode(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        user, _ = self.scan(user, 1150, timeout=500)
        state = self.state(user)
        self.assertEqual(state["configured_timeout_ms"], 500)
        self.assertEqual(state["effective_timeout_ms"], 100)
        self.assertEqual(state["monitoring_status"], "TIMED_OUT")
        self.assertEqual(self.single_alert(user)["timeout_ms"], 100)

    def test_old_configuration_never_enables_new_production_scan(self):
        result = scan_user_records(USER, user_record({"a": point(1000)}), 1500,
                                   movement_threshold_m=5, inactivity_threshold_ms=100)
        self.assertEqual(result["alerts"], {})
        self.assertTrue(result["issues"])

    def test_failed_commit_does_not_persist_future_rejection_or_alert(self):
        original = user_record({"a": point(1000), "future": point(2000)})
        database = Database(original, retry=True, fail=True)
        with self.assertRaises(RuntimeError):
            reconcile_user(database, USER, 1500, {"quick_start_timeout_ms": TIMEOUT})
        self.assertEqual(database.user, original)

    def test_scan_is_pure_and_retry_does_not_duplicate_episode(self):
        original = user_record({"a": point(1000)})
        snapshot = copy.deepcopy(original)
        scan_user_records(USER, original, 1100, quick_start_timeout_ms=TIMEOUT)
        self.assertEqual(original, snapshot)
        committed, _ = self.scan(original, 1100)
        repeated, result = self.scan(committed, 1100)
        self.assertEqual(repeated, committed)
        self.assertEqual(result["alerts"], {})

    def test_corrupt_persisted_sample_is_reported_without_blocking_sos(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1050)
        self.state(user)["latest_sample"]["timestamp_ms"] = 1040
        user["rescue_requests"] = {"help": {"status": PENDING}}
        result = scan_user_records(USER, user, 1100, quick_start_timeout_ms=TIMEOUT)
        self.assertTrue(result["issues"])
        self.assertEqual(set(result["alerts"]), {"sos_review__help"})
        self.assertEqual(result["events"], {})

    def test_clock_rollback_preserves_state_and_reports_issue(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        database = Database(user)
        committed, result = reconcile_user(database, USER, 1099,
                                          {"quick_start_timeout_ms": TIMEOUT})
        self.assertTrue(result["issues"])
        self.assertEqual(committed, user)

    def test_new_fresh_sample_applies_pending_configuration_as_new_epoch(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        user, _ = self.scan(user, 1150, timeout=500)
        self.assertTrue(self.state(user)["configuration_pending"])
        user[QUICK_START_SESSIONS][SESSION]["points"]["fresh"] = point(1190)
        user, _ = self.scan(user, 1200, timeout=500)
        state = self.state(user)
        self.assertFalse(state["configuration_pending"])
        self.assertEqual(state["effective_timeout_ms"], 500)
        self.assertEqual(state["policy_epoch"], 2)
        self.assertEqual(state["policy_history"][0]["timeout_ms"], 100)
        self.assertEqual(self.single_alert(user)["timeout_ms"], 100)
        self.assertEqual(self.single_alert(user)["location_status"], "RECOVERED")

    def test_removed_suppression_reason_is_not_retained_by_repository(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        self.state(user)["suppression_reason"] = "OLD_DIAGNOSTIC"
        self.state(user)["unrelated_metadata"] = {"preserved": True}
        user, _ = self.scan(user, 1101)
        self.assertNotEqual(self.state(user).get("suppression_reason"), "OLD_DIAGNOSTIC")
        self.assertEqual(self.state(user)["unrelated_metadata"], {"preserved": True})

    def test_incomplete_active_episode_is_reported_without_crashing(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        del self.state(user)["episode_first_onset_ms"]
        result = scan_user_records(USER, user, 1200, quick_start_timeout_ms=TIMEOUT)
        self.assertTrue(result["issues"])
        self.assertEqual(result["alert_updates"], {})

    def test_conversion_rejects_tampered_time_identity_and_policy(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        alert = self.single_alert(user)
        alert.update(stage=RESOLUTION, user_contact_result=SAFETY_UNCONFIRMED,
                     emergency_contact_result=NOT_AVAILABLE)
        for field, value in (("sample_at_ms", 1001), ("timeout_ms", 101),
                             ("episode_version", True), ("policy_epoch", None),
                             ("location_episode_count", None),
                             ("primary_record_id", "different_session")):
            with self.subTest(field=field):
                changed = copy.deepcopy(alert)
                changed[field] = value
                with self.assertRaises(ValueError):
                    convert_alert_to_rescue_event(changed, 1101, True)

    def test_pure_reconciliation_refreshes_conversion_snapshot_without_nested_transaction(self):
        from rescue_repository import apply_user_reconciliation
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        alert = self.single_alert(user)
        alert.update(stage=RESOLUTION, user_contact_result=SAFETY_UNCONFIRMED,
                     emergency_contact_result=NOT_AVAILABLE)
        user[QUICK_START_SESSIONS][SESSION]["points"]["late"] = point(1020)
        before = copy.deepcopy(user)
        updated, result = apply_user_reconciliation(
            user, USER, 1200, {"quick_start_timeout_ms": TIMEOUT})
        self.assertEqual(user, before)
        self.assertEqual(result["issues"], [])
        ready = self.single_alert(updated)
        self.assertEqual(ready["stage"], RESOLUTION)
        _, event = convert_alert_to_rescue_event(ready, 1200, True)
        self.assertEqual(event["sample_at_ms"], 1020)
        self.assertEqual(event["abnormal_since_ms"], 1120)

    def test_recovered_conversion_keeps_anomaly_snapshot_and_separate_diagnostics(self):
        user, _ = self.scan(user_record({"a": point(1000)}), 1100)
        alert = self.single_alert(user)
        alert.update(stage=RESOLUTION, user_contact_result=SAFETY_UNCONFIRMED,
                     emergency_contact_result=NOT_AVAILABLE)
        user[QUICK_START_SESSIONS][SESSION]["points"]["fresh"] = point(1190)
        user, _ = self.scan(user, 1200)
        _, event = convert_alert_to_rescue_event(self.single_alert(user), 1201, True)
        self.assertEqual(event["sample_at_ms"], 1000)
        self.assertEqual(event["abnormal_since_ms"], 1100)
        self.assertEqual(event["location_status"], "RECOVERED")
        self.assertEqual(event["location_latest_sample_at_ms"], 1190)
        self.assertEqual(event["location_recovered_at_ms"], 1200)

    def test_transaction_retry_uses_current_callback_time_for_new_sample(self):
        database = Database(user_record({"new": point(1010)}), retry=True)
        callback_times = iter((1000, 1020))
        committed, result = reconcile_user(
            database, USER, 990, {"quick_start_timeout_ms": TIMEOUT},
            clock_ms=lambda: next(callback_times))
        self.assertEqual(result["issues"], [])
        state = self.state(committed)
        self.assertEqual(state["latest_sample_at_ms"], 1010)
        self.assertEqual(state["last_evaluated_at_ms"], 1020)
        self.assertEqual(state["monitoring_status"], "FRESH")
        self.assertEqual(state["rejected_future_samples"], [])
        self.assertEqual(committed["rescue_alerts"], {})


if __name__ == "__main__":
    unittest.main()
