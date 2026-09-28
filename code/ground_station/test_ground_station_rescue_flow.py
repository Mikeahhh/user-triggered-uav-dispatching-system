from __future__ import annotations

import copy
import inspect
import json
import sys
import types
import unittest
import tempfile
from pathlib import Path
from dispatch_journal import DispatchJournal
from unittest.mock import Mock, patch


def _install_import_stubs_if_needed():


    try:
        import tkinter
    except ImportError:
        module = types.ModuleType("tkinter")
        module.TclError = type("TclError", (Exception,), {})
        module.END = "end"
        module.Listbox = type("Listbox", (), {})
        module.messagebox = types.SimpleNamespace(
            askyesno=Mock(return_value=False),
            showerror=Mock(),
            showinfo=Mock(),
        )
        sys.modules["tkinter"] = module

    try:
        import customtkinter
    except ImportError:
        module = types.ModuleType("customtkinter")
        module.set_appearance_mode = lambda _mode: None
        for name in (
            "CTk",
            "CTkButton",
            "CTkEntry",
            "CTkFrame",
            "CTkLabel",
            "CTkOptionMenu",
            "CTkScrollableFrame",
            "StringVar",
        ):
            setattr(module, name, type(name, (), {}))
        sys.modules["customtkinter"] = module

    try:
        import firebase_admin
    except ImportError:
        module = types.ModuleType("firebase_admin")
        module.get_app = Mock(side_effect=ValueError("not initialized"))
        module.initialize_app = Mock()
        module.credentials = types.SimpleNamespace(Certificate=Mock())
        module.db = types.SimpleNamespace(reference=Mock())
        sys.modules["firebase_admin"] = module

    try:
        import tkintermapview
    except ImportError:
        module = types.ModuleType("tkintermapview")
        module.TkinterMapView = type("TkinterMapView", (), {})
        sys.modules["tkintermapview"] = module

    try:
        import paho.mqtt.client
    except ImportError:
        paho = types.ModuleType("paho")
        mqtt = types.ModuleType("paho.mqtt")
        client = types.ModuleType("paho.mqtt.client")
        client.MQTT_ERR_SUCCESS = 0
        client.Client = type("Client", (), {})
        paho.mqtt = mqtt
        mqtt.client = client
        sys.modules["paho"] = paho
        sys.modules["paho.mqtt"] = mqtt
        sys.modules["paho.mqtt.client"] = client


_install_import_stubs_if_needed()

import ground_station as gs


NOW_MS = 2_000_000


def _event(event_id, user_id="USER_A", **overrides):
    value = {
        "event_id": event_id,
        "user_id": user_id,
        "trigger_type": "SOS",
        "primary_record_type": "rescue_requests",
        "primary_record_id": "request_1",
        "created_at_ms": NOW_MS - 10,
        "abnormal_since_ms": None,
        "status": "PENDING",
        "source_alert_id": "sos_review__request_1",
        "search_confirmed_at_ms": NOW_MS - 10,
        "user_contact_result": "SAFETY_UNCONFIRMED",
        "emergency_contact_result": "NOT_AVAILABLE",
    }
    value.update(overrides)
    return value


class _FirebaseReference:
    def __init__(self, data, path=(), history=None, fail_updates=None):
        self.data = data
        self.path = tuple(path)
        self.history = history if history is not None else []
        self.fail_updates = fail_updates if fail_updates is not None else {"count": 0}

    def child(self, segment):
        parts = tuple(part for part in str(segment).split("/") if part)
        return _FirebaseReference(
            self.data,
            self.path + parts,
            self.history,
            self.fail_updates,
        )

    def _read(self):
        value = self.data
        for part in self.path:
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value

    def _write_path(self, path, value):
        current = self.data
        for part in path[:-1]:
            current = current.setdefault(part, {})
        current[path[-1]] = copy.deepcopy(value)

    def get(self):
        return copy.deepcopy(self._read())

    def set(self, value):
        self.history.append(("set", self.path, copy.deepcopy(value)))
        self._write_path(self.path, value)

    def transaction(self, transform):
        value = transform(self.get())
        if self.fail_updates["count"]:
            self.fail_updates["count"] -= 1
            raise RuntimeError("synthetic Firebase transaction failure")
        self.history.append(("transaction", self.path, copy.deepcopy(value)))
        self._write_path(self.path, value)
        return copy.deepcopy(value)

    def update(self, values):
        self.history.append(("update", self.path, copy.deepcopy(values)))
        if self.fail_updates["count"]:
            self.fail_updates["count"] -= 1
            raise RuntimeError("synthetic Firebase write failure")
        for key, value in values.items():
            suffix = tuple(part for part in str(key).split("/") if part)
            self._write_path(self.path + suffix, value)


class _PublishInfo:
    def __init__(self, rc=0, wait_result=None, published=True):
        self.rc = rc
        self.wait_result = wait_result
        self.published = published
        self.wait_timeout = None

    def wait_for_publish(self, timeout=None):
        self.wait_timeout = timeout
        return self.wait_result

    def is_published(self):
        return self.published


class _MqttClient:
    def __init__(self, info):
        self.info = info
        self.calls = []

    def publish(self, topic, payload, qos=0):
        self.calls.append((topic, payload, qos))
        return self.info


class RescueConfigurationAndDetectionTests(unittest.TestCase):
    def test_thresholds_have_no_business_defaults(self):
        config = gs.load_rescue_runtime_config({})
        self.assertFalse(config["ready"])
        self.assertIsNone(config["quick_start_timeout_ms"])
        self.assertIsNone(config["wait_threshold_ms"])
        self.assertEqual(len(config["errors"]), 2)

    def test_explicit_thresholds_are_converted_to_milliseconds(self):
        config = gs.load_rescue_runtime_config(
            {
                "GS_T_LOCATION_UPDATE_SECONDS": "12",
                "GS_T_WAIT_SECONDS": "30.25",
                "GS_EVENT_TIMEZONE": "Asia/Shanghai",
            }
        )
        self.assertTrue(config["ready"])
        self.assertEqual(config["quick_start_timeout_ms"], 12_000)
        self.assertEqual(config["wait_threshold_ms"], 30_250)
        self.assertEqual(config["event_timezone"], "Asia/Shanghai")

    def test_unrepresentable_duration_fails_closed_without_crashing(self):
        for duration in ("0.0001", "1e308"):
            with self.subTest(duration=duration):
                config = gs.load_rescue_runtime_config(
                    {
                        "GS_T_LOCATION_UPDATE_SECONDS": duration,
                        "GS_T_WAIT_SECONDS": "1",
                    }
                )
                self.assertFalse(config["ready"])
                self.assertIsNone(config["quick_start_timeout_ms"])

    def test_missing_thresholds_still_reconcile_timestamped_booking_and_sos(self):
        users = {
            "USER_A": {
                "booked_events": {
                    "booking_1": {
                        "expectedEndAtMs": NOW_MS - 1,
                        "waypoints": [{"latitude": 1, "longitude": 2}],
                    }
                },
                "QuickStartSessions": {
                    "session_1": {
                        "status": "ACTIVE",
                        "startTime": "1970-01-01T00:00:00.000Z",
                        "points": {},
                    }
                },
                "rescue_requests": {
                    "request_1": {
                        "status": "PENDING",
                        "latitude": 1,
                        "longitude": 2,
                    }
                },
            }
        }
        updates, additions, issues = gs.detect_rescue_updates(
            users, NOW_MS, gs.load_rescue_runtime_config({})
        )
        self.assertIn(
            f"users/USER_A/rescue_alerts/event_timeout__booking_1__{NOW_MS - 1}", updates
        )
        self.assertIn("users/USER_A/rescue_alerts/sos_review__request_1", updates)
        self.assertFalse(
            any("quick_location_timeout__" in path for path in updates),
            "Quick Start priority rules must not be guessed without thresholds",
        )
        self.assertIn("sos_review__request_1", additions["USER_A"]["rescue_alerts"])
        self.assertFalse(additions["USER_A"]["rescue_events"])
        self.assertTrue(
            any(issue["record_type"] == "QuickStartSessions" for issue in issues)
        )

    def test_configured_detection_waits_for_first_valid_quick_start_point(self):
        session = {
            "status": "ACTIVE",
            "startTime": "1970-01-01T00:00:00.000Z",
            "points": {"bad": {"latitude": "bad"}},
        }
        users = {"USER_A": {"QuickStartSessions": {"session_1": session}}}

        config = gs.load_rescue_runtime_config(
            {
                "GS_T_LOCATION_UPDATE_SECONDS": "100",
                "GS_T_WAIT_SECONDS": "60",
            }
        )
        updates, additions, issues = gs.detect_rescue_updates(users, NOW_MS, config)
        self.assertFalse(additions["USER_A"]["rescue_alerts"])
        self.assertEqual(additions["USER_A"]["quick_start_monitoring"]["session_1"]["monitoring_status"], "NOT_READY")
        self.assertEqual(issues, [])

        session["points"]["point_1"] = {
            "latitude": 22.3, "longitude": 114.1, "timestamp": NOW_MS,
        }
        _, additions, issues = gs.detect_rescue_updates(users, NOW_MS + 99_999, config)
        self.assertFalse(additions["USER_A"]["rescue_alerts"])
        self.assertEqual(issues, [])
        updates, additions, issues = gs.detect_rescue_updates(users, NOW_MS + 100_000, config)
        alert_id = f"quick_location_timeout__session_1__{NOW_MS + 100_000}"
        self.assertIn(f"users/USER_A/rescue_alerts/{alert_id}", updates)
        self.assertEqual(additions["USER_A"]["rescue_alerts"][alert_id]["abnormal_since_ms"],
                         NOW_MS + 100_000)
        self.assertEqual(issues, [])

    def test_legacy_settings_do_not_silently_enable_new_freshness_policy(self):
        config = gs.load_rescue_runtime_config({"GS_D_MOVE_METERS": "8.5",
            "GS_T_INACTIVE_SECONDS": "12", "GS_T_WAIT_SECONDS": "30"})
        self.assertFalse(config["ready"])
        self.assertIsNone(config["quick_start_timeout_ms"])
        self.assertEqual(len(config["ignored_legacy_settings"]), 2)


class MissionPreparationTests(unittest.TestCase):
    def test_booking_route_keeps_original_order(self):
        event = _event(
            "rescue__event_timeout__booking_1",
            trigger_type="EVENT_BOOKING_TIMEOUT",
            primary_record_type="booked_events",
            primary_record_id="booking_1",
            abnormal_since_ms=NOW_MS - 100,
        )
        user = {
            "booked_events": {
                "booking_1": {
                    "expectedEndAtMs": NOW_MS - 100,
                    "waypoints": [
                        {"latitude": 22.2, "longitude": 114.2},
                        {"latitude": 22.1, "longitude": 114.1},
                    ]
                }
            }
        }
        prepared = gs.prepare_mission_for_rescue_event(user, event)
        self.assertEqual(
            prepared["waypoints"], [(22.2, 114.2), (22.1, 114.1)]
        )
        self.assertEqual(prepared["mqtt_mission_token"], event["event_id"])

    def test_quick_route_uses_timestamp_order_not_firebase_key_order(self):
        event = _event(
            "rescue__quick_inactivity__session_1__100",
            trigger_type="QUICK_START_INACTIVITY",
            primary_record_type="QuickStartSessions",
            primary_record_id="session_1",
            abnormal_since_ms=100,
        )
        user = {
            "QuickStartSessions": {
                "session_1": {
                    "points": {
                        "point_10": {
                            "latitude": 2,
                            "longitude": 2,
                            "timestamp": 20,
                        },
                        "point_2": {
                            "latitude": 1,
                            "longitude": 1,
                            "timestamp": 10,
                        },
                    }
                }
            }
        }
        prepared = gs.prepare_mission_for_rescue_event(user, event)
        self.assertEqual(prepared["waypoints"], [(1.0, 1.0), (2.0, 2.0)])
        self.assertEqual(prepared["mqtt_mission_token"], event["event_id"])

    def test_sos_reuses_request_id_for_phone_uav_join(self):
        event = _event("sos__request_1")
        user = {
            "rescue_requests": {
                "request_1": {"latitude": 22.3, "longitude": 114.2}
            }
        }
        prepared = gs.prepare_mission_for_rescue_event(user, event)
        self.assertEqual(prepared["mqtt_mission_token"], "request_1")
        self.assertEqual(prepared["mqtt_mission_id"], "USER_A/request_1")
        self.assertEqual(len(prepared["waypoints"]), 19)

    def test_missing_empty_invalid_and_oversized_routes_are_blocked(self):
        booking_event = _event(
            "rescue__event_timeout__booking_1",
            trigger_type="EVENT_BOOKING_TIMEOUT",
            primary_record_type="booked_events",
            primary_record_id="booking_1",
            abnormal_since_ms=100,
        )
        with self.assertRaisesRegex(ValueError, "primary record group is missing"):
            gs.prepare_mission_for_rescue_event({}, booking_event)
        with self.assertRaisesRegex(ValueError, "no waypoint list"):
            gs.prepare_mission_for_rescue_event(
                {"booked_events": {"booking_1": {"expectedEndAtMs": 100, "waypoints": []}}},
                booking_event,
            )
        with self.assertRaisesRegex(ValueError, "has no coordinate"):
            gs.prepare_mission_for_rescue_event(
                {
                    "booked_events": {
                        "booking_1": {"expectedEndAtMs": 100, "waypoints": [{"latitude": 1}]}
                    }
                },
                booking_event,
            )
        with self.assertRaisesRegex(ValueError, "1000-waypoint limit"):
            gs.prepare_mission_for_rescue_event(
                {
                    "booked_events": {
                        "booking_1": {
                            "expectedEndAtMs": 100,
                            "waypoints": [
                                {"latitude": 1, "longitude": 2}
                                for _ in range(1001)
                            ]
                        }
                    }
                },
                booking_event,
            )

    def test_supplementary_records_do_not_change_primary_route(self):
        event = _event(
            "rescue__event_timeout__booking_1",
            trigger_type="EVENT_BOOKING_TIMEOUT",
            primary_record_type="booked_events",
            primary_record_id="booking_1",
            abnormal_since_ms=100,
        )
        user = {
            "booked_events": {
                "booking_1": {
                    "expectedEndAtMs": 100,
                    "waypoints": [{"latitude": 1, "longitude": 2}]
                }
            },
            "QuickStartSessions": {
                "supplement": {
                    "points": {
                        "point_1": {
                            "latitude": 70,
                            "longitude": 80,
                            "timestamp": 1,
                        }
                    }
                }
            },
            "rescue_requests": {
                "supplement": {"latitude": 40, "longitude": 50}
            },
        }
        prepared = gs.prepare_mission_for_rescue_event(user, event)
        self.assertEqual(prepared["waypoints"], [(1.0, 2.0)])
        self.assertIn("Quick Start: 1", gs.supplementary_record_summary(user, event))
        self.assertIn("SOS: 1", gs.supplementary_record_summary(user, event))


class GenericDispatchTests(unittest.TestCase):
    def setUp(self):
        self.original_demo = gs.DEMO_SCREENSHOT_MODE
        self.original_connected = gs.mqtt_connected
        self.original_client = gs.mqtt_client
        gs.DEMO_SCREENSHOT_MODE = False

    def tearDown(self):
        gs.DEMO_SCREENSHOT_MODE = self.original_demo
        gs.mqtt_connected = self.original_connected
        gs.mqtt_client = self.original_client

    def _dispatch_with_info(self, info, confirmation=True):
        client = _MqttClient(info)
        gs.mqtt_connected = True
        gs.mqtt_client = client
        with patch.object(gs.messagebox, "showerror"), patch.object(
            gs.messagebox, "showinfo"
        ):
            result = gs.dispatch_mission(
                [(22.3, 114.2)],
                "USER_A",
                "event_1",
                confirm_callback=lambda *_args: confirmation,
                execution={"schema_version": 2, "execution_id": "exec-test", "mission_id": "USER_A/event_1",
                           "mission_type": "event", "waypoints": [{"latitude": 22.3, "longitude": 114.2}],
                           "return_to_launch": True, "altitude": 5.0, "hover_seconds": 5.0},
                before_publish=lambda: None,
            )
        return result, client

    def test_invalid_parameters_return_structured_status_without_publish(self):
        gs.mqtt_connected = True
        gs.mqtt_client = _MqttClient(_PublishInfo())
        with patch.object(gs.messagebox, "showerror"):
            result = gs.dispatch_mission([], "USER_A", "event_1")
        self.assertEqual(result["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(gs.mqtt_client.calls, [])

    def test_mqtt_unavailable_is_explicit(self):
        gs.mqtt_connected = False
        gs.mqtt_client = None
        with patch.object(gs.messagebox, "showerror"):
            result = gs.dispatch_mission([(1, 2)], "USER_A", "event_1")
        self.assertEqual(result["status"], gs.DISPATCH_MQTT_UNAVAILABLE)

    def test_operator_cancellation_does_not_publish(self):
        result, client = self._dispatch_with_info(_PublishInfo(), confirmation=False)
        self.assertEqual(result["status"], gs.DISPATCH_CANCELLED)
        self.assertEqual(client.calls, [])

    def test_qos_one_rc_wait_and_publish_confirmation_are_enforced(self):
        for info in (
            _PublishInfo(rc=7),
            _PublishInfo(wait_result=False),
            _PublishInfo(published=False),
        ):
            with self.subTest(info=vars(info)):
                result, _client = self._dispatch_with_info(info)
                self.assertEqual(result["status"], gs.DISPATCH_UNKNOWN)

        info = _PublishInfo()
        result, client = self._dispatch_with_info(info)
        self.assertEqual(result["status"], gs.DISPATCH_PUBLISHED)
        self.assertEqual(client.calls[0][0], gs.MQTT_TOPIC_MULTI)
        self.assertEqual(client.calls[0][2], 1)
        payload = json.loads(client.calls[0][1])
        self.assertEqual(payload["mission_id"], "USER_A/event_1")
        self.assertTrue(info.is_published())


class RescueEventDispatchWrapperTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal_path = Path(self.directory.name) / "execution.sqlite3"
        self.journal = DispatchJournal(self.journal_path, "https://synthetic.firebaseio.com")
        self.root = _FirebaseReference({"users": {"USER_A": {
            "rescue_requests": {"request_1": {"latitude": 22.3, "longitude": 114.2}},
            "rescue_events": {"sos__request_1": _event("sos__request_1")}}}})
        self.client = _MqttClient(_PublishInfo())
        self.patches = [patch.object(gs, "rescue_runtime_config", {"ready": True}),
                        patch.object(gs, "mqtt_connected", True), patch.object(gs, "mqtt_client", self.client),
                        patch.object(gs, "DEMO_SCREENSHOT_MODE", False), patch.object(gs, "refresh_data"),
                        patch.object(gs, "_show_workflow_error"), patch.object(gs, "_show_message_safely")]
        for patcher in self.patches: patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches): patcher.stop()
        self.journal.close()
        self.directory.cleanup()
        gs.published_uncommitted_events.clear()

    def call(self, confirm=True, resume=False):
        confirmer = confirm if callable(confirm) else lambda *_args: confirm
        user = self.root.data["users"]["USER_A"]
        event = user["rescue_events"]["sos__request_1"]
        if (event.get("status") == "PENDING" and "sos__request_1" not in user.get("active_events", {})
                and not event.get("execution") and self.journal.get(("USER_A", "sos__request_1")) is None):
            gs.select_rescue_event("USER_A", "sos__request_1", self.root, journal=self.journal)
            gs.prepare_active_rescue_event("USER_A", "sos__request_1", self.root, journal=self.journal)
        return gs.dispatch_rescue_event("USER_A", "sos__request_1", self.root,
                                       journal=self.journal, confirm_callback=confirmer,
                                       resume_execution=resume)

    def test_cancel_does_not_create_intent_or_publish(self):
        result = self.call(False)
        self.assertEqual(result["status"], gs.DISPATCH_CANCELLED)
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.journal.get(("USER_A", "sos__request_1")))

    def test_published_event_is_conditionally_marked_dispatched(self):
        result = self.call()
        self.assertEqual(result["firebase_status"], "DISPATCHED")
        self.assertEqual(len(self.client.calls), 1)
        event = self.root.data["users"]["USER_A"]["rescue_events"]["sos__request_1"]
        self.assertEqual(event["status"], "DISPATCHED")
        self.assertEqual(event["execution"]["execution_id"], result["execution_id"])
        self.assertEqual(self.journal.get(("USER_A", "sos__request_1"))["state"], "COMMITTED")

    def test_refresh_failure_cannot_reverse_committed_dispatch(self):
        with patch.object(gs, "refresh_data", side_effect=RuntimeError("synthetic render error")):
            result = self.call()
        self.assertEqual(result["status"], gs.DISPATCH_PUBLISHED)
        self.assertEqual(result["refresh_status"], "FAILED")
        self.assertEqual(len(self.client.calls), 1)

    def test_firebase_failure_then_sqlite_reopen_only_commits_without_republish(self):
        with patch.object(gs, "_commit_execution", side_effect=RuntimeError("synthetic commit failure")):
            first = self.call()
        self.assertEqual(first["firebase_status"], "PENDING_WRITE_RETRY")
        self.journal.close()
        self.journal = DispatchJournal(self.journal_path, "https://synthetic.firebaseio.com")
        gs.published_uncommitted_events.clear()
        second = self.call()
        self.assertEqual(second["firebase_status"], "DISPATCHED")
        self.assertEqual(second["execution_id"], first["execution_id"])
        self.assertEqual(len(self.client.calls), 1)

    def test_changed_event_during_confirmation_cannot_publish(self):
        def confirm(*args):
            self.root.data["users"]["USER_A"]["rescue_events"]["sos__request_1"]["status"] = "DISPATCHED"
            return True
        result = self.call(confirm)
        self.assertEqual(result["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.client.calls, [])

    def test_changed_primary_during_confirmation_requires_review(self):
        def confirm(*args):
            self.root.data["users"]["USER_A"]["rescue_requests"]["request_1"]["latitude"] = 23.0
            return True
        result = self.call(confirm)
        self.assertEqual(result["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.client.calls, [])

    def test_unknown_requires_explicit_same_execution_retry(self):
        self.client.info = _PublishInfo(wait_result=False)
        first = self.call()
        self.assertEqual(first["status"], gs.DISPATCH_UNKNOWN)
        second = self.call()
        self.assertEqual(second["status"], gs.DISPATCH_UNKNOWN)
        self.assertEqual(len(self.client.calls), 1)
        self.client.info = _PublishInfo()
        third = self.call(resume=True)
        self.assertEqual(third["status"], gs.DISPATCH_PUBLISHED)
        sent = [json.loads(call[1]) for call in self.client.calls]
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(third["execution_id"], first["execution_id"])


class ContactAndPresentationIntegrationTests(unittest.TestCase):
    def test_police_confirmation_uses_one_conditional_user_transaction(self):
        alert = {
            "alert_id": "event_timeout__booking_1",
            "user_id": "USER_A",
            "trigger_type": "EVENT_BOOKING_TIMEOUT",
            "primary_record_type": "booked_events",
            "primary_record_id": "booking_1",
            "abnormal_since_ms": NOW_MS - 100,
            "created_at_ms": NOW_MS - 50,
            "stage": "RESOLUTION",
            "user_contact_result": "SAFETY_UNCONFIRMED",
            "emergency_contact_result": "NOT_AVAILABLE",
            "status": "PENDING",
        }
        user = {"rescue_alerts": {alert["alert_id"]: alert}, "rescue_events": {},
                "booked_events": {"booking_1": {"expectedEndAtMs": NOW_MS - 100}}}
        root = _FirebaseReference({"users": {"USER_A": copy.deepcopy(user)}})
        with patch.object(gs.messagebox, "askyesno", return_value=True), patch.object(
            gs, "_epoch_now_ms", return_value=NOW_MS
        ):
            converted = gs._convert_alert_atomically(
                root, "USER_A", alert["alert_id"], user, alert
            )
        self.assertTrue(converted)
        root_updates = [item for item in root.history if item[0] == "transaction"]
        self.assertEqual(len(root_updates), 1)
        self.assertEqual(root_updates[0][1], ("users", "USER_A"))
        values = root_updates[0][2]
        self.assertEqual(
            values["rescue_alerts"]["event_timeout__booking_1"][
                "status"
            ],
            "CONVERTED",
        )
        self.assertEqual(
            values["rescue_events"]["rescue__event_timeout__booking_1"]["status"],
            "PENDING",
        )

    def test_alert_identity_mismatch_is_rejected_before_prompt_or_write(self):
        alert = {
            "alert_id": "event_timeout__booking_1",
            "user_id": "USER_B",
            "trigger_type": "EVENT_BOOKING_TIMEOUT",
            "primary_record_type": "booked_events",
            "primary_record_id": "booking_1",
            "abnormal_since_ms": NOW_MS - 100,
            "created_at_ms": NOW_MS - 50,
            "stage": "RESOLUTION",
            "user_contact_result": "SAFETY_UNCONFIRMED",
            "emergency_contact_result": "NOT_AVAILABLE",
            "status": "PENDING",
        }
        user = {"rescue_alerts": {alert["alert_id"]: alert}, "rescue_events": {},
                "booked_events": {"booking_1": {"expectedEndAtMs": NOW_MS - 100}}}
        root = _FirebaseReference({"users": {"USER_A": copy.deepcopy(user)}})
        with patch.object(gs.messagebox, "askyesno") as prompt:
            with self.assertRaisesRegex(ValueError, "does not match"):
                gs._convert_alert_atomically(
                    root, "USER_A", alert["alert_id"], user, alert
                )
        prompt.assert_not_called()
        self.assertEqual(root.history, [])

        with patch.object(
            gs, "_load_live_user", return_value=(root, user)
        ), patch.object(gs, "_show_workflow_error") as workflow_error, patch.object(
            gs, "refresh_data"
        ):
            gs._handle_user_contact(
                "USER_A", alert["alert_id"], gs.SAFETY_UNCONFIRMED
            )
        workflow_error.assert_called_once()
        self.assertEqual(root.history, [])

    def test_removed_emergency_contact_cannot_create_a_contact_result(self):
        alert = {
            "alert_id": "event_timeout__booking_1",
            "user_id": "USER_A",
            "trigger_type": "EVENT_BOOKING_TIMEOUT",
            "primary_record_type": "booked_events",
            "primary_record_id": "booking_1",
            "abnormal_since_ms": NOW_MS - 100,
            "created_at_ms": NOW_MS - 50,
            "stage": "CONTACT_EMERGENCY",
            "user_contact_result": "SAFETY_UNCONFIRMED",
            "emergency_contact_result": None,
            "status": "PENDING",
        }
        user = {
            "profile": {"phone": "SYNTHETIC", "emergency_contacts": []},
            "rescue_alerts": {alert["alert_id"]: alert},
        }
        root = _FirebaseReference({"users": {"USER_A": copy.deepcopy(user)}})
        with patch.object(
            gs, "_load_live_user", return_value=(root, user)
        ), patch.object(gs, "_show_workflow_error") as workflow_error, patch.object(
            gs, "refresh_data"
        ):
            gs._handle_emergency_contact(
                "USER_A", alert["alert_id"], gs.SAFE_CONFIRMED
            )
            self.assertEqual(root.history, [])
            workflow_error.assert_called_once()
            gs._handle_emergency_contact(
                "USER_A", alert["alert_id"], gs.NOT_AVAILABLE
            )
        writes = [item for item in root.history if item[0] == "transaction"]
        self.assertEqual(len(writes), 1)
        saved = writes[0][2]["rescue_alerts"][alert["alert_id"]]
        self.assertEqual(saved["emergency_contact_result"], "NOT_AVAILABLE")
        self.assertEqual(saved["stage"], "RESOLUTION")
        self.assertEqual(saved["status"], "PENDING")

    def test_contact_emergency_ui_offers_only_not_available_when_removed(self):
        alert = {
            "alert_id": "event_timeout__booking_1",
            "user_id": "USER_A",
            "trigger_type": "EVENT_BOOKING_TIMEOUT",
            "primary_record_type": "booked_events",
            "primary_record_id": "booking_1",
            "abnormal_since_ms": NOW_MS - 100,
            "created_at_ms": NOW_MS - 50,
            "stage": "CONTACT_EMERGENCY",
            "user_contact_result": "SAFETY_UNCONFIRMED",
            "emergency_contact_result": None,
            "status": "PENDING",
        }
        users = {
            "USER_A": {
                "profile": {"phone": "SYNTHETIC", "emergency_contacts": []},
                "booked_events": {"booking_1": {"expectedEndAtMs": NOW_MS - 100}},
                "rescue_alerts": {alert["alert_id"]: alert},
            }
        }
        button_texts = []
        card = Mock()
        with patch.object(gs, "_section_heading"), patch.object(
            gs, "create_mission_card", return_value=card
        ), patch.object(
            gs,
            "_add_card_button",
            side_effect=lambda _card, text, _command, **_kwargs: button_texts.append(text),
        ):
            gs._render_pending_alerts(users, NOW_MS)
        self.assertEqual(
            button_texts,
            [gs.LANGUAGES[gs.current_lang]["emergency_not_available_btn"]],
        )

    def test_raw_records_and_manual_preview_have_no_direct_dispatch_call(self):
        raw_source = inspect.getsource(gs._render_raw_records_read_only)
        self.assertNotIn("dispatch_mission", raw_source)
        self.assertNotIn("_add_dispatch_button", raw_source)
        self.assertNotIn("_add_rescue_spiral_button", raw_source)
        main_source = inspect.getsource(gs.show_main_window)
        preview_source = main_source.split("def _run_pattern", 1)[1].split(
            "sos_btn_frame", 1
        )[0]
        self.assertNotIn("dispatch_mission", preview_source)
        self.assertIn("_show_waypoint_route", preview_source)

    def test_pending_event_renderer_uses_global_scheduler_order(self):
        users = {
            "USER_B": {
                "rescue_events": {
                    "normal": _event(
                        "normal",
                        "USER_B",
                        trigger_type="EVENT_BOOKING_TIMEOUT",
                        primary_record_type="booked_events",
                        primary_record_id="booking",
                        created_at_ms=NOW_MS - 5,
                        abnormal_since_ms=NOW_MS - 500,
                    ),
                    "closed": _event("closed", "USER_B", status="DISPATCHED"),
                }
            },
            "USER_A": {
                "rescue_events": {
                    "sos": _event("sos", "USER_A", created_at_ms=NOW_MS - 10)
                }
            },
        }
        cards = []

        class Card:
            def bind(self, *_args, **_kwargs):
                return None

        def create_card(_parent, title, **_kwargs):
            cards.append(title)
            return Card()

        prepared = {
            "waypoints": [(1.0, 2.0)],
            "mqtt_mission_token": "x",
            "mission_type": "event",
            "return_to_launch": True,
        }
        with patch.object(
            gs,
            "rescue_runtime_config",
            {"ready": True, "wait_threshold_ms": 1_000},
        ), patch.object(gs, "_section_heading"), patch.object(
            gs, "create_mission_card", side_effect=create_card
        ), patch.object(gs, "_add_card_button"), patch.object(
            gs, "prepare_mission_for_rescue_event", return_value=prepared
        ):
            gs._render_pending_events(users, NOW_MS)
        self.assertEqual(cards, ["USER_B/normal", "USER_A/sos"])
        self.assertEqual(gs.pending_rescue_event_card_ids, ["USER_B/normal", "USER_A/sos"])

    def test_pending_identity_conflict_keeps_both_cards_disabled(self):
        shared = _event("shared", "USER_A")
        users = {
            "USER_A": {"rescue_events": {"shared": shared}},
            "USER_B": {
                "rescue_events": {"other": copy.deepcopy(shared)}
            },
        }
        entries, invalid = gs._collect_pending_event_entries(users)
        self.assertEqual(len(entries), 2)
        self.assertEqual(set(invalid), {("USER_A", "shared"), ("USER_B", "other")})

        cards = []
        states = []

        class Card:
            def bind(self, *_args, **_kwargs):
                return None

        def create_card(_parent, title, **kwargs):
            cards.append((title, kwargs["extra_info"]))
            return Card()

        def add_button(_card, _text, _command, **kwargs):
            states.append(kwargs["state"])

        with patch.object(
            gs,
            "rescue_runtime_config",
            {"ready": True, "wait_threshold_ms": 1_000},
        ), patch.object(gs, "_section_heading"), patch.object(
            gs, "create_mission_card", side_effect=create_card
        ), patch.object(
            gs, "_add_card_button", side_effect=add_button
        ), patch.object(
            gs, "prepare_mission_for_rescue_event"
        ) as prepare:
            gs._render_pending_events(users, NOW_MS)
        self.assertEqual(
            [item[0] for item in cards], ["USER_A/shared", "USER_B/other"]
        )
        self.assertTrue(all("INVALID EVENT" in item[1] for item in cards))
        self.assertEqual(states, ["disabled", "disabled"])
        self.assertEqual(
            gs.pending_rescue_event_card_ids,
            ["USER_A/shared", "USER_B/other"],
        )
        prepare.assert_not_called()

    def test_malformed_primary_identity_disables_one_card_without_crashing(self):
        malformed = _event(
            "malformed",
            "USER_A",
            primary_record_type=["rescue_requests"],
            primary_record_id={"request": "one"},
        )
        users = {"USER_A": {"rescue_events": {"malformed": malformed}}}
        cards = []
        states = []

        class Card:
            def bind(self, *_args, **_kwargs):
                return None

        def create_card(_parent, title, **kwargs):
            cards.append((title, kwargs["extra_info"]))
            return Card()

        def add_button(_card, _text, _command, **kwargs):
            states.append(kwargs["state"])

        with patch.object(
            gs,
            "rescue_runtime_config",
            {"ready": True, "wait_threshold_ms": 1_000},
        ), patch.object(gs, "_section_heading"), patch.object(
            gs, "create_mission_card", side_effect=create_card
        ), patch.object(gs, "_add_card_button", side_effect=add_button):
            gs._render_pending_events(users, NOW_MS)
        self.assertEqual([item[0] for item in cards], ["USER_A/malformed"])
        self.assertIn("primary_record_type", cards[0][1])
        self.assertEqual(states, ["disabled"])


class PeriodicRefreshTests(unittest.TestCase):
    def test_failed_cancellation_does_not_register_a_second_callback(self):
        class Root:
            def __init__(self):
                self.after_calls = 0

            def winfo_exists(self):
                return True

            def after_cancel(self, _callback_id):
                raise RuntimeError("synthetic cancellation failure")

            def after(self, _delay, _callback):
                self.after_calls += 1
                return "unexpected-new-callback"

        fake_root = Root()
        original_root = gs.root
        original_after_id = gs.periodic_refresh_after_id
        try:
            gs.root = fake_root
            gs.periodic_refresh_after_id = "existing-callback"
            gs._schedule_periodic_refresh()
            self.assertEqual(gs.periodic_refresh_after_id, "existing-callback")
            self.assertEqual(fake_root.after_calls, 0)
        finally:
            gs.root = None
            gs.periodic_refresh_after_id = None
            gs.root = original_root
            gs.periodic_refresh_after_id = original_after_id

    def test_scheduler_keeps_only_one_after_callback(self):
        class Root:
            def __init__(self):
                self.next_id = 0
                self.cancelled = []

            def winfo_exists(self):
                return True

            def after(self, _delay, _callback):
                self.next_id += 1
                return f"after-{self.next_id}"

            def after_cancel(self, callback_id):
                self.cancelled.append(callback_id)

        fake_root = Root()
        original_root = gs.root
        original_after_id = gs.periodic_refresh_after_id
        try:
            gs.root = fake_root
            gs.periodic_refresh_after_id = None
            gs._schedule_periodic_refresh()
            first = gs.periodic_refresh_after_id
            gs._schedule_periodic_refresh()
            self.assertEqual(fake_root.cancelled, [first])
            self.assertEqual(gs.periodic_refresh_after_id, "after-2")
        finally:
            gs._cancel_periodic_refresh()
            gs.root = original_root
            gs.periodic_refresh_after_id = original_after_id

    def test_refresh_exception_rearms_exactly_one_periodic_callback(self):
        class Root:
            def __init__(self):
                self.next_id = 0
                self.active = set()

            def winfo_exists(self):
                return True

            def after(self, _delay, _callback):
                self.next_id += 1
                callback_id = f"after-{self.next_id}"
                self.active.add(callback_id)
                return callback_id

            def after_cancel(self, callback_id):
                self.active.discard(callback_id)

        fake_root = Root()
        original_root = gs.root
        original_after_id = gs.periodic_refresh_after_id

        def failing_refresh_that_also_schedules():
            gs._schedule_periodic_refresh()
            raise RuntimeError("synthetic refresh error")

        try:
            gs.root = fake_root
            gs.periodic_refresh_after_id = None
            with patch.object(
                gs, "refresh_data", side_effect=failing_refresh_that_also_schedules
            ):
                gs._periodic_refresh_tick()
            self.assertEqual(len(fake_root.active), 1)
            self.assertEqual(fake_root.active, {gs.periodic_refresh_after_id})
            self.assertEqual(gs.periodic_refresh_after_id, "after-2")
        finally:
            gs._cancel_periodic_refresh()
            gs.root = original_root
            gs.periodic_refresh_after_id = original_after_id


if __name__ == "__main__":
    unittest.main(verbosity=2)
