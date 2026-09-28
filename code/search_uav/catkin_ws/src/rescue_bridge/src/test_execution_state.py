import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution_protocol import normalize_execution_payload, normalized_content
from execution_state import ExecutionError, ExecutionManager
from mission_protocol import MissionValidationError


def mission(eid="exec-a", **changes):
    data = {"schema_version": 2, "mission_id": "AUDIT/a", "execution_id": eid,
            "mission_type": "event", "waypoints": [{"lat": 22, "lon": 114}, {"lat": 22.1, "lon": 114.1}],
            "return_to_launch": False, "hover_seconds": 5}
    data.update(changes)
    return normalize_execution_payload(data)


class ExecutionTests(unittest.TestCase):
    def test_shared_fixtures(self):
        fixtures = Path(__file__).resolve().parents[1] / "test/task_v2_fingerprints.json"
        for case in json.loads(fixtures.read_text())["cases"]:
            with self.subTest(case=case["name"]):
                result = normalize_execution_payload(case["input"])
                self.assertEqual(normalized_content(result), case["normalized"])
                self.assertEqual(result["content_fingerprint"], case["sha256"])

    def test_required_identity_and_source_bound(self):
        with self.assertRaises(MissionValidationError):
            mission(execution_id="")
        with self.assertRaises(MissionValidationError):
            mission(waypoints=[{"lat": 0, "lon": 0}] * 1001)
        result = mission(waypoints=[{"lat": 0, "lon": 0}] * 1000, return_to_launch=True)
        manager = ExecutionManager()
        _, status = manager.admit(result, (0.0, 0.0))
        self.assertEqual(status["waypoint_total"], 1001)

    def test_legacy_execution_key_is_stable(self):
        data = {"mission_id": "AUDIT/a", "waypoints": [{"lat": 0, "lon": 0}]}
        self.assertEqual(normalize_execution_payload(data)["execution_id"], normalize_execution_payload(data)["execution_id"])

    def test_duplicate_never_restarts_and_conflict_rejected(self):
        manager = ExecutionManager()
        manager.admit(mission())
        manager.feedback("AUDIT/a", "exec-a", 0, "ACCEPTED")
        disposition, saved = manager.admit(mission())
        self.assertEqual(disposition, "DUPLICATE")
        self.assertEqual(saved["phase"], "NAVIGATING")
        with self.assertRaisesRegex(ExecutionError, "EXECUTION_CONFLICT"):
            manager.admit(mission(hover_seconds=6))
        with self.assertRaisesRegex(ExecutionError, "MISSION_BUSY"):
            manager.admit(mission("exec-b"))

    def test_arrival_requires_matching_acceptance_and_identity(self):
        now = [0.0]
        manager = ExecutionManager(clock=lambda: now[0]); manager.admit(mission())
        self.assertIsNone(manager.feedback("AUDIT/a", "exec-a", 0, "ARRIVED"))
        manager.feedback("AUDIT/a", "exec-a", 0, "ACCEPTED")
        for mid, eid, index in (("AUDIT/old", "exec-a", 0), ("AUDIT/a", "", 0),
                                ("AUDIT/a", "old", 0), ("AUDIT/a", "exec-a", 1)):
            self.assertIsNone(manager.feedback(mid, eid, index, "ARRIVED"))
        manager.feedback("AUDIT/a", "exec-a", 0, "ARRIVED")
        now[0] = 4.999; self.assertIsNone(manager.tick())
        now[0] = 5; self.assertEqual(manager.tick()["waypoint_index"], 1)
        self.assertIsNone(manager.feedback("AUDIT/a", "exec-a", 0, "ARRIVED"))
        self.assertEqual(manager.snapshot()["phase"], "WAITING_TARGET_ACCEPTANCE")

    def test_rejection_and_timeout_are_explicit_and_keep_target(self):
        manager = ExecutionManager(); manager.admit(mission())
        status = manager.feedback("AUDIT/a", "exec-a", 0, "REJECTED", "GPS_NOT_READY")
        self.assertEqual(status["phase"], "TARGET_REJECTED")
        self.assertEqual(len(manager.queue), 2)
        manager = ExecutionManager(); manager.admit(mission())
        self.assertIsNone(manager.acceptance_timeout("old", 0))
        self.assertEqual(manager.acceptance_timeout("exec-a", 0)["phase"], "TARGET_ACCEPTANCE_TIMEOUT")

    def test_landing_stays_locked_and_manual_release_does_not_claim_touchdown(self):
        manager = ExecutionManager(); manager.admit(mission())
        manager.land_result(True)
        with self.assertRaisesRegex(ExecutionError, "MISSION_BUSY"):
            manager.admit(mission("exec-b"))
        with self.assertRaises(ExecutionError):
            manager.operator_reset("exec-a", reason="checked")
        with self.assertRaises(ExecutionError):
            manager.operator_reset("old", confirmed=True, reason="checked")
        released = manager.operator_reset("exec-a", confirmed=True, reason="operator checked")
        self.assertFalse(released["touchdown_confirmed"])
        self.assertEqual(manager.admit(mission())[0], "DUPLICATE")
        self.assertEqual(manager.admit(mission("exec-b"))[0], "NEW")

    def test_missing_return_fix_does_not_start(self):
        manager = ExecutionManager()
        with self.assertRaisesRegex(ExecutionError, "RTL_FIX_UNAVAILABLE"):
            manager.admit(mission(return_to_launch=True))
        self.assertFalse(manager.active_id)

    def test_restart_requires_recovery_and_cannot_replay_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "execution.json")
            original = ExecutionManager(path); original.admit(mission())
            with self.assertRaisesRegex(ExecutionError, "another bridge"):
                ExecutionManager(path)
            original.close()
            restarted = ExecutionManager(path)
            self.assertEqual(restarted.snapshot()["phase"], "RECOVERY_REQUIRED")
            self.assertIsNone(restarted.command())
            self.assertEqual(restarted.admit(mission())[0], "DUPLICATE")
            with self.assertRaises(ExecutionError):
                restarted.admit(mission("new"))
            restarted.close()

    def test_persistence_failure_does_not_admit(self):
        manager = ExecutionManager()
        with patch.object(manager, "_write", side_effect=OSError("injected write failure")):
            with self.assertRaises(OSError):
                manager.admit(mission())
        self.assertEqual(manager.records, {})
        self.assertIsNone(manager.command())

    def test_schema_and_feedback_indexes_are_strict_integers(self):
        for version in (2.0, 1.0, True, "2"):
            with self.subTest(version=version), self.assertRaises(MissionValidationError):
                mission(schema_version=version)
        manager = ExecutionManager(); manager.admit(mission())
        for index in (0.0, False, "0"):
            self.assertIsNone(manager.feedback("AUDIT/a", "exec-a", index, "ACCEPTED"))

    def test_tempfile_failure_and_directory_flush_failure_disable_commands(self):
        for failure in ("tempfile", "directory_flush"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                manager = ExecutionManager(str(Path(directory)/"execution.json"))
                if failure == "tempfile":
                    injection = patch("execution_state.tempfile.mkstemp", side_effect=OSError("disk unavailable"))
                else:
                    injection = patch("execution_state.os.fsync", side_effect=[None, OSError("directory flush failed")])
                with injection, self.assertRaises(OSError): manager.admit(mission())
                self.assertFalse(manager.active_id)
                self.assertIsNone(manager.command(include_navigating=True))
                with self.assertRaises(ExecutionError): manager.admit(mission("exec-b"))
                manager.close()

    def test_closed_journal_owner_cannot_dispatch(self):
        manager=ExecutionManager(); manager.admit(mission()); manager.close()
        self.assertIsNone(manager.command())
        with self.assertRaises(ExecutionError): manager.admit(mission("exec-b"))


if __name__ == "__main__":
    unittest.main()
