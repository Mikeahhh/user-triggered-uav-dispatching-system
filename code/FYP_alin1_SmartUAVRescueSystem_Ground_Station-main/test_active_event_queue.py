import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

import test_ground_station_rescue_flow as flow
from dispatch_journal import DispatchJournal
from priority_scheduler import schedule_metrics
from test_execution_recovery import AtomicReference
import active_event_queue as queue
from quick_start_freshness import sample_identity

gs = flow.gs
KEY = ("USER_A", "sos__request_1")


class ActiveQueueTests(unittest.TestCase):
    setUp = flow.RescueEventDispatchWrapperTests.setUp
    tearDown = flow.RescueEventDispatchWrapperTests.tearDown

    def user(self): return self.root.data["users"][KEY[0]]
    def entry(self): return self.user()["active_events"][KEY[1]]
    def event(self): return self.user()["rescue_events"][KEY[1]]
    def select(self): return gs.select_rescue_event(*KEY, self.root, journal=self.journal)
    def prepare(self): return gs.prepare_active_rescue_event(*KEY, self.root, journal=self.journal)
    def dispatch(self, confirm=True, **options):
        return gs.dispatch_rescue_event(*KEY, self.root, journal=self.journal,
            confirm_callback=confirm if callable(confirm) else lambda *_: confirm, **options)
    def cancel(self): return gs.cancel_active_rescue_event(*KEY, self.root, journal=self.journal)
    def recover(self): return gs.recover_active_confirmation(*KEY, self.root, journal=self.journal)

    def test_direct_dispatch_requires_selection_then_separate_preparation(self):
        self.assertEqual(self.dispatch()["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.select()
        self.assertEqual(self.entry()["phase"], queue.SELECTED)
        self.assertEqual(self.dispatch()["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.journal.get(KEY))
        self.prepare()
        self.assertEqual(self.entry()["phase"], queue.PREPARED)
        self.assertEqual(self.client.calls, [])

    def test_confirmed_dispatch_atomically_exits_active_and_marks_dispatched(self):
        self.select(); self.prepare()
        self.assertEqual(self.dispatch()["status"], gs.DISPATCH_PUBLISHED)
        self.assertNotIn(KEY[1], self.user()["active_events"])
        self.assertEqual(self.event()["status"], "DISPATCHED")
        commits = [item for item in self.root.history if item[0] == "transaction"
                   and item[2]["rescue_events"][KEY[1]]["status"] == "DISPATCHED"]
        self.assertTrue(commits)
        self.assertNotIn(KEY[1], commits[-1][2]["active_events"])

    def test_cancelling_selection_returns_same_event_without_resetting_wait_time(self):
        original = copy.deepcopy(self.event())
        metrics = schedule_metrics(original, flow.NOW_MS, 1000)
        self.select(); self.cancel()
        self.assertEqual(self.event(), original)
        self.assertNotIn(KEY[1], self.user()["active_events"])
        self.assertEqual(schedule_metrics(self.event(), flow.NOW_MS, 1000), metrics)
        self.assertEqual([item[:2] for item in gs._collect_pending_event_entries(self.root.data["users"])[0]], [KEY])

    def test_confirmation_cancellation_returns_pending_without_execution(self):
        self.select(); self.prepare()
        self.assertEqual(self.dispatch(False)["status"], gs.DISPATCH_CANCELLED)
        self.assertNotIn(KEY[1], self.user()["active_events"])
        self.assertEqual(self.event()["status"], "PENDING")
        self.assertIsNone(self.journal.get(KEY))
        self.assertEqual(self.client.calls, [])

    def test_preparation_error_keeps_selected_entry_available_for_return(self):
        self.select()
        del self.user()["rescue_requests"]
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.entry()["phase"], queue.SELECTED)
        self.cancel()
        self.assertNotIn(KEY[1], self.user()["active_events"])

    def test_primary_change_requires_a_new_prepared_review(self):
        self.select(); self.prepare()
        self.user()["rescue_requests"]["request_1"]["latitude"] = 23.0
        confirmation = Mock(return_value=True)
        self.assertEqual(self.dispatch(confirmation)["status"], gs.DISPATCH_INVALID_PARAMETERS)
        confirmation.assert_not_called()
        self.assertEqual(self.entry()["phase"], queue.PREPARED)
        self.prepare()
        self.assertEqual(self.dispatch()["status"], gs.DISPATCH_PUBLISHED)

    def test_event_trigger_changed_during_confirmation_cannot_authorize_old_route(self):
        self.select(); self.prepare()
        def change_trigger(*_):
            self.event()["trigger_type"] = "EVENT_BOOKING_TIMEOUT"
            return True
        self.assertEqual(self.dispatch(change_trigger)["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.journal.get(KEY))
        self.assertNotIn("execution", self.event())

    def test_booking_episode_semantics_change_hash_but_priority_does_not(self):
        event = flow._event("booking-event", trigger_type="EVENT_BOOKING_TIMEOUT",
            primary_record_type="booked_events", primary_record_id="booking-1", abnormal_since_ms=100)
        user = {"booked_events": {"booking-1": {"expectedEndAtMs": 100, "waypoints": [{"latitude": 1, "longitude": 2}]}}}
        original = queue.source_fingerprint(user, event)
        event["effective_priority"] = "HIGH"
        self.assertEqual(queue.source_fingerprint(user, event), original)
        event["primary_expected_end_at_ms"] = 200
        self.assertNotEqual(queue.source_fingerprint(user, event), original)

    def test_destroyed_dialog_is_explicitly_recoverable_after_sqlite_reopen(self):
        self.select(); self.prepare()
        def destroyed(*_): raise RuntimeError("synthetic destroyed dialog")
        self.assertEqual(self.dispatch(destroyed)["status"], gs.DISPATCH_UNKNOWN)
        before = self.entry()["version"]
        self.assertEqual(self.entry()["phase"], queue.CONFIRMING)
        owner = self.journal.workstation_id()
        self.journal.close()
        self.journal = DispatchJournal(self.journal_path, "https://synthetic.firebaseio.com")
        self.assertEqual(self.journal.workstation_id(), owner)
        self.recover()
        self.assertEqual(self.entry()["phase"], queue.PREPARED)
        self.assertGreater(self.entry()["version"], before)
        self.cancel()
        self.assertNotIn(KEY[1], self.user()["active_events"])

    def test_old_dialog_callback_rejected_after_explicit_confirmation_recovery(self):
        self.select(); self.prepare()
        def stale_dialog(*_):
            self.recover()
            return True
        result = self.dispatch(stale_dialog)
        self.assertEqual(result["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.entry()["phase"], queue.PREPARED)
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.journal.get(KEY))

    def test_old_preparation_version_cannot_replace_current_review(self):
        selected = self.select()
        prepared = self.prepare()
        with self.assertRaises(ValueError):
            queue.save_preparation(self.root, *KEY, self.journal.workstation_id(), selected["selection_id"],
                selected["version"], prepared["prepared"], prepared["source_hash"], flow.NOW_MS)
        self.assertEqual(self.entry()["version"], prepared["version"])

    def test_confirming_entry_cannot_be_cancelled_by_another_window(self):
        self.select(); self.prepare()
        def confirm(*_):
            with self.assertRaises(ValueError): self.cancel()
            return False
        self.assertEqual(self.dispatch(confirm)["status"], gs.DISPATCH_CANCELLED)
        self.assertNotIn(KEY[1], self.user()["active_events"])

    def test_local_prepared_execution_intent_prevents_confirmation_reset(self):
        self.select(); self.prepare()
        with patch.object(gs.messagebox, "askyesno", side_effect=RuntimeError("interrupted")):
            gs.dispatch_rescue_event(*KEY, self.root, journal=self.journal)
        prepared = self.entry()["prepared"]
        payload = gs.build_execution_payload(prepared, "existing-intent")
        self.journal.prepare(KEY, payload)
        with self.assertRaises(ValueError): self.recover()
        self.assertEqual(self.entry()["phase"], queue.CONFIRMING)
        self.assertEqual(self.journal.get(KEY)["state"], "PREPARED")

    def test_cloud_authorization_prevents_release_when_local_storage_fails(self):
        self.select(); self.prepare()
        with patch.object(self.journal, "prepare", side_effect=OSError("synthetic disk full")):
            self.assertEqual(self.dispatch()["status"], gs.DISPATCH_UNKNOWN)
        self.assertEqual(self.entry()["phase"], queue.AUTHORIZED)
        execution_id = self.event()["execution"]["execution_id"]
        with self.assertRaises(ValueError): self.cancel()
        with self.assertRaises(ValueError): self.recover()
        self.assertEqual(self.client.calls, [])
        recovered = self.dispatch(resume_execution=True)
        self.assertEqual(recovered["execution_id"], execution_id)
        self.assertEqual(recovered["status"], gs.DISPATCH_PUBLISHED)

    def test_unknown_execution_stays_active_and_cannot_return_to_pending(self):
        self.select(); self.prepare()
        self.client.info = flow._PublishInfo(wait_result=False)
        self.assertEqual(self.dispatch()["status"], gs.DISPATCH_UNKNOWN)
        with self.assertRaises(ValueError): self.cancel()
        with self.assertRaises(ValueError): self.recover()
        self.assertEqual(self.entry()["phase"], queue.AUTHORIZED)
        self.assertEqual(self.event()["status"], "PENDING")
        self.assertFalse(gs._collect_pending_event_entries(self.root.data["users"])[0])

    def test_other_workstation_cannot_prepare_cancel_or_confirm_this_selection(self):
        self.select(); self.prepare()
        other = DispatchJournal(Path(self.directory.name) / "other.sqlite3", self.journal.scope)
        try:
            self.assertNotEqual(other.workstation_id(), self.journal.workstation_id())
            for function in (gs.select_rescue_event, gs.prepare_active_rescue_event, gs.cancel_active_rescue_event):
                with self.assertRaises(ValueError): function(*KEY, self.root, journal=other)
            result = gs.dispatch_rescue_event(*KEY, self.root, journal=other, confirm_callback=lambda *_: True)
            self.assertEqual(result["status"], gs.DISPATCH_INVALID_PARAMETERS)
            self.assertEqual(self.client.calls, [])
        finally:
            other.close()

    def test_same_workstation_second_process_cannot_confirm_an_open_dialog(self):
        self.root = AtomicReference(self.root.data)
        self.select(); self.prepare()
        other = DispatchJournal(self.journal_path, self.journal.scope)
        self.assertEqual(other.workstation_id(), self.journal.workstation_id())
        entered = threading.Event(); release = threading.Event(); results = []
        def confirm(*_):
            entered.set()
            return release.wait(3)
        worker = threading.Thread(target=lambda: results.append(self.dispatch(confirm)))
        try:
            worker.start()
            self.assertTrue(entered.wait(3))
            blocked = gs.dispatch_rescue_event(*KEY, self.root, journal=other, confirm_callback=lambda *_: True)
            self.assertEqual(blocked["status"], gs.DISPATCH_INVALID_PARAMETERS)
            self.assertEqual(self.client.calls, [])
            release.set(); worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[0]["status"], gs.DISPATCH_PUBLISHED)
            self.assertEqual(len(self.client.calls), 1)
        finally:
            release.set(); worker.join(3); other.close()

    def test_legacy_unqueued_unknown_execution_migrates_but_never_republishes(self):
        self.select(); self.prepare()
        self.client.info = flow._PublishInfo(wait_result=False)
        first = self.dispatch()
        del self.user()["active_events"][KEY[1]]
        second = self.dispatch(resume_execution=True)
        self.assertEqual(second["status"], gs.DISPATCH_UNKNOWN)
        self.assertEqual(self.entry()["phase"], queue.RECOVERY_ONLY)
        self.assertEqual(second["execution_id"], first["execution_id"])
        self.assertEqual(len(self.client.calls), 1)

    def test_legacy_broker_confirmed_recovery_only_commits_and_exits_active(self):
        self.select(); self.prepare()
        with patch.object(gs, "_commit_execution", side_effect=OSError("synthetic database write failure")):
            self.dispatch()
        del self.user()["active_events"][KEY[1]]
        self.assertEqual(self.dispatch()["firebase_status"], "DISPATCHED")
        self.assertNotIn(KEY[1], self.user()["active_events"])
        self.assertEqual(len(self.client.calls), 1)

    def test_real_ui_callbacks_enforce_select_prepare_confirm_in_separate_views(self):
        buttons, headings = [], []
        widget = SimpleNamespace(pack=lambda *_a, **_k: None, bind=lambda *_a, **_k: None)
        def render():
            buttons.clear(); headings.clear()
            gs._render_pending_events(self.root.data["users"], flow.NOW_MS)
            gs._render_active_events(self.root.data["users"], flow.NOW_MS)
        def click(label):
            choice = next(item for item in buttons if item[0] == label)
            self.assertEqual(choice[2].get("state", "normal"), "normal")
            choice[1]()
        with patch.object(gs, "get_dispatch_journal", side_effect=lambda: DispatchJournal(self.journal_path, self.journal.scope)), \
             patch.object(gs, "_load_live_user", side_effect=lambda _uid, _root=None: (self.root, copy.deepcopy(self.user()))), \
             patch.object(gs, "rescue_runtime_config", {"ready": True, "wait_threshold_ms": 1000}), \
             patch.object(gs, "_section_heading", side_effect=lambda title, *_: headings.append(title)), \
             patch.object(gs, "create_mission_card", return_value=widget), patch.object(gs.ctk, "CTkLabel", return_value=widget), \
             patch.object(gs, "_add_card_button", side_effect=lambda _card, text, callback, **kw: buttons.append((text, callback, kw))), \
             patch.object(gs.messagebox, "askyesno", return_value=True):
            render()
            self.assertFalse(any(label == "Confirm Dispatch" for label, _, _ in buttons))
            click("Select → Active Queue")
            render()
            self.assertNotIn("Select → Active Queue", [item[0] for item in buttons])
            click("Prepare Mission")
            self.assertEqual(self.client.calls, [])
            render()
            self.assertIn("Review Prepared Route", [item[0] for item in buttons])
            self.assertIn("Review Associated Records", [item[0] for item in buttons])
            click("Confirm Dispatch")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.event()["status"], "DISPATCHED")
        self.assertNotIn(KEY[1], self.user()["active_events"])

    def test_capture_v2_ui_callback_does_not_require_ambiguous_top_level_mission_id(self):
        record = {"schema_version": 2, "capture_id": "synthetic-capture", "request_id": "request-1",
                  "carrier_mission_id": "USER_A/event-1", "carrier_execution_id": "exec-1", "envelope_sha256": "a" * 64}
        def process(_payload, **kwargs):
            kwargs["on_state"](gs.RECEIVED_STORED, record)
            kwargs["on_state"](gs.ACK_PUBLISHED, record)
        with patch.object(gs, "process_onboard_envelope", side_effect=process), \
             patch.object(gs, "_get_ground_rescue_store"), patch.object(gs, "format_record_summary", return_value="capture summary") as formatter, \
             patch.object(gs, "_schedule_ui"), patch("builtins.print") as printed:
            gs._process_onboard_record_message(Mock(), b"synthetic")
        self.assertEqual(formatter.call_count, 2)
        self.assertTrue(any("carrier_execution=exec-1" in call.args[0] for call in printed.call_args_list))


class QuickStartQuarantineAuthorizationTests(unittest.TestCase):
    setUp = flow.RescueEventDispatchWrapperTests.setUp
    tearDown = flow.RescueEventDispatchWrapperTests.tearDown
    user = ActiveQueueTests.user
    entry = ActiveQueueTests.entry
    event = ActiveQueueTests.event
    select = ActiveQueueTests.select
    prepare = ActiveQueueTests.prepare
    dispatch = ActiveQueueTests.dispatch

    def configure_quick_start(self, trigger="QUICK_START_LOCATION_TIMEOUT"):
        self.event().update(trigger_type=trigger, primary_record_type="QuickStartSessions",
                            primary_record_id="session_A", abnormal_since_ms=300)
        self.user()["QuickStartSessions"] = {"session_A": {
            "status": "ACTIVE", "startTime": 100,
            "points": [{"timestamp": 200, "latitude": 1, "longitude": 2}]}}

    def quarantine(self, session_id="session_A"):
        identity = sample_identity({"timestamp_ms": 200, "latitude": 1.0, "longitude": 2.0})
        self.user().setdefault("quick_start_monitoring", {})[session_id] = {
            "rejected_future_samples": [identity]}

    def assert_rejected_before_confirmation(self, trigger):
        self.configure_quick_start(trigger)
        self.select(); self.prepare()
        original_source_hash = self.entry()["source_hash"]
        self.quarantine()
        self.assertEqual(queue.source_fingerprint(self.user(), self.event()), original_source_hash)
        confirmation = Mock(return_value=True)
        self.assertEqual(self.dispatch(confirmation)["status"], gs.DISPATCH_INVALID_PARAMETERS)
        confirmation.assert_not_called()
        self.assertEqual(self.entry()["phase"], queue.PREPARED)
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.journal.get(KEY))
        self.assertNotIn("execution", self.event())

    def test_new_timeout_prepared_route_rechecks_current_quarantine(self):
        self.assert_rejected_before_confirmation("QUICK_START_LOCATION_TIMEOUT")

    def test_legacy_inactivity_prepared_route_rechecks_current_quarantine(self):
        self.assert_rejected_before_confirmation("QUICK_START_INACTIVITY")

    def test_quarantine_during_confirmation_blocks_new_authorization(self):
        self.configure_quick_start()
        self.select(); self.prepare()
        def confirm(*_):
            self.quarantine()
            return True
        self.assertEqual(self.dispatch(confirm)["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.entry()["phase"], queue.CONFIRMING)
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.journal.get(KEY))
        self.assertNotIn("execution", self.event())

    def test_other_session_quarantine_does_not_block_primary_authorization(self):
        self.configure_quick_start()
        self.select(); self.prepare()
        self.quarantine("unrelated_session")
        self.assertEqual(self.dispatch()["status"], gs.DISPATCH_PUBLISHED)
        self.assertEqual(len(self.client.calls), 1)

    def test_malformed_quarantine_cannot_silently_authorize(self):
        self.configure_quick_start()
        self.select(); self.prepare()
        self.user()["quick_start_monitoring"] = {"session_A": {"rejected_future_samples": "invalid"}}
        confirmation = Mock(return_value=True)
        self.assertEqual(self.dispatch(confirmation)["status"], gs.DISPATCH_INVALID_PARAMETERS)
        confirmation.assert_not_called()
        self.assertEqual(self.entry()["phase"], queue.PREPARED)
        self.assertEqual(self.client.calls, [])

    def test_later_quarantine_does_not_rewrite_authorized_same_execution_retry(self):
        self.configure_quick_start()
        self.select(); self.prepare()
        self.client.info = flow._PublishInfo(wait_result=False)
        first = self.dispatch()
        self.assertEqual(first["status"], gs.DISPATCH_UNKNOWN)
        payload = copy.deepcopy(self.event()["execution"]["payload"])
        self.quarantine()
        self.client.info = flow._PublishInfo()
        retried = self.dispatch(resume_execution=True)
        self.assertEqual(retried["status"], gs.DISPATCH_PUBLISHED)
        self.assertEqual(retried["execution_id"], first["execution_id"])
        self.assertEqual(self.event()["execution"]["payload"], payload)

    def test_later_quarantine_does_not_block_commit_only_recovery(self):
        self.configure_quick_start()
        self.select(); self.prepare()
        with patch.object(gs, "_commit_execution", side_effect=OSError("synthetic commit failure")):
            first = self.dispatch()
        payload = copy.deepcopy(self.event()["execution"]["payload"])
        self.quarantine()
        recovered = self.dispatch()
        self.assertEqual(recovered["firebase_status"], "DISPATCHED")
        self.assertEqual(recovered["execution_id"], first["execution_id"])
        self.assertEqual(self.event()["execution"]["payload"], payload)
        self.assertEqual(len(self.client.calls), 1)


if __name__ == "__main__":
    unittest.main()
