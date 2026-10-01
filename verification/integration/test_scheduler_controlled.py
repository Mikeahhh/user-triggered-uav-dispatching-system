from __future__ import annotations

import hashlib
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path


CODE_ROOT = (Path(__file__).resolve().parents[2] / "code")
GROUND_STATION = CODE_ROOT / "ground_station/src"
if str(GROUND_STATION) not in sys.path:
    sys.path.insert(0, str(GROUND_STATION))

from priority_scheduler import order_pending_events, schedule_metrics
from run_scheduler_controlled_test import (
    BASE_CREATED_AT_MS,
    BOOKING_EVENT_ID,
    QUICK_EVENT_ID,
    SOS_EVENT_ID,
    build_controlled_artifacts,
    build_synthetic_events,
    write_controlled_artifacts,
)


TEST_ONLY_WAIT_THRESHOLD_MS = 60_000
NOW_BEFORE_THRESHOLD_MS = BASE_CREATED_AT_MS + TEST_ONLY_WAIT_THRESHOLD_MS - 1
NOW_AT_THRESHOLD_MS = BASE_CREATED_AT_MS + TEST_ONLY_WAIT_THRESHOLD_MS


class ControlledSchedulerScenarioTests(unittest.TestCase):
    def setUp(self):
        self.events = build_synthetic_events(TEST_ONLY_WAIT_THRESHOLD_MS)

    def test_three_trigger_types_coexist_one_millisecond_before_upgrade(self):
        ordered = order_pending_events(
            self.events,
            now_ms=NOW_BEFORE_THRESHOLD_MS,
            wait_threshold_ms=TEST_ONLY_WAIT_THRESHOLD_MS,
        )
        self.assertEqual(
            [event["event_id"] for event in ordered],
            [QUICK_EVENT_ID, BOOKING_EVENT_ID, SOS_EVENT_ID],
        )
        metrics = {
            event["event_id"]: schedule_metrics(
                event,
                NOW_BEFORE_THRESHOLD_MS,
                TEST_ONLY_WAIT_THRESHOLD_MS,
            )
            for event in ordered
        }
        self.assertEqual(metrics[SOS_EVENT_ID]["effective_priority"], "NORMAL")
        self.assertEqual(metrics[QUICK_EVENT_ID]["effective_priority"], "NORMAL")
        self.assertEqual(metrics[BOOKING_EVENT_ID]["effective_priority"], "NORMAL")
        self.assertEqual(
            metrics[BOOKING_EVENT_ID]["queue_waiting_time_ms"],
            TEST_ONLY_WAIT_THRESHOLD_MS - 1,
        )
        self.assertGreater(
            metrics[QUICK_EVENT_ID]["abnormal_duration_ms"],
            metrics[BOOKING_EVENT_ID]["abnormal_duration_ms"],
        )

    def test_booking_upgrades_at_equality_and_joins_sos_created_time_order(self):
        ordered = order_pending_events(
            self.events,
            now_ms=NOW_AT_THRESHOLD_MS,
            wait_threshold_ms=TEST_ONLY_WAIT_THRESHOLD_MS,
        )
        self.assertEqual(
            [event["event_id"] for event in ordered],
            [BOOKING_EVENT_ID, QUICK_EVENT_ID, SOS_EVENT_ID],
        )
        self.assertEqual(
            [
                schedule_metrics(
                    event,
                    NOW_AT_THRESHOLD_MS,
                    TEST_ONLY_WAIT_THRESHOLD_MS,
                )["effective_priority"]
                for event in ordered
            ],
            ["HIGH", "NORMAL", "NORMAL"],
        )
        self.assertLess(
            ordered[0]["created_at_ms"],
            ordered[1]["created_at_ms"],
        )

    def test_controlled_result_is_independent_of_input_order(self):
        expected_before = [QUICK_EVENT_ID, BOOKING_EVENT_ID, SOS_EVENT_ID]
        expected_at = [BOOKING_EVENT_ID, QUICK_EVENT_ID, SOS_EVENT_ID]
        for candidate in itertools.permutations(self.events):
            self.assertEqual(
                [
                    event["event_id"]
                    for event in order_pending_events(
                        candidate,
                        NOW_BEFORE_THRESHOLD_MS,
                        TEST_ONLY_WAIT_THRESHOLD_MS,
                    )
                ],
                expected_before,
            )
            self.assertEqual(
                [
                    event["event_id"]
                    for event in order_pending_events(
                        candidate,
                        NOW_AT_THRESHOLD_MS,
                        TEST_ONLY_WAIT_THRESHOLD_MS,
                    )
                ],
                expected_at,
            )


class ControlledSchedulerArtifactTests(unittest.TestCase):
    def test_artifacts_record_stub_dispatch_and_strict_evidence_boundaries(self):
        artifacts = build_controlled_artifacts(
            TEST_ONLY_WAIT_THRESHOLD_MS,
            operator_confirmed=True,
        )
        config = artifacts["test_configuration.json"]
        dispatch = artifacts["dispatch_result.json"]
        summary = artifacts["run_summary.json"]

        self.assertEqual(config["test_only_wait_threshold_ms"], 60_000)
        self.assertEqual(config["D_move"], "NOT_EXERCISED_BY_SCHEDULER_TEST")
        self.assertEqual(config["T_inactive"], "NOT_EXERCISED_BY_SCHEDULER_TEST")
        self.assertEqual(dispatch["operator_confirmation_result"], "CONFIRMED_TEST_STUB")
        self.assertEqual(dispatch["publish_transport"], "IN_MEMORY_STUB")
        self.assertEqual(dispatch["event_status_after"], "DISPATCHED")
        self.assertEqual(dispatch["selected_rescue_event_id"], BOOKING_EVENT_ID)
        self.assertTrue(dispatch["dispatched_mission_id"].startswith("TEST_ONLY/"))
        self.assertEqual(summary["result"], "PASS")
        self.assertFalse(summary["boundaries"]["firebase"])
        self.assertFalse(summary["boundaries"]["mqtt"])
        self.assertFalse(summary["boundaries"]["physical_uav"])
        self.assertFalse(summary["boundaries"]["physical_dispatch"])
        self.assertIn("not a fourth mission simulation", summary["claim_boundary"])

    def test_writer_creates_json_checksums_and_refuses_overwrite(self):
        artifacts = build_controlled_artifacts(
            TEST_ONLY_WAIT_THRESHOLD_MS,
            operator_confirmed=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "scheduler_controlled_result"
            write_controlled_artifacts(output, artifacts)

            expected_files = set(artifacts) | {"SHA256SUMS.txt"}
            self.assertEqual(
                {path.name for path in output.iterdir()}, expected_files
            )
            self.assertEqual(
                json.loads((output / "run_summary.json").read_text(encoding="utf-8"))[
                    "result"
                ],
                "PASS",
            )

            checksum_lines = (output / "SHA256SUMS.txt").read_text(
                encoding="ascii"
            ).splitlines()
            self.assertEqual(len(checksum_lines), len(artifacts))
            for line in checksum_lines:
                digest, filename = line.split("  ", 1)
                self.assertEqual(
                    digest,
                    hashlib.sha256((output / filename).read_bytes()).hexdigest(),
                )

            with self.assertRaises(FileExistsError):
                write_controlled_artifacts(output, artifacts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
