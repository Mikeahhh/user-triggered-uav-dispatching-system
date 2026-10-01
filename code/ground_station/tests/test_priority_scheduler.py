from __future__ import annotations

import itertools
import unittest

from priority_scheduler import (
    HIGH_PRIORITY,
    NORMAL_PRIORITY,
    SchedulerValidationError,
    effective_priority,
    order_pending_events,
    schedule_metrics,
)


NOW = 1_000_000
WAIT = 100_000


def event(
    event_id,
    trigger_type,
    created_at_ms,
    abnormal_since_ms=None,
    status="PENDING",
    user_id="USER_A",
):
    return {
        "event_id": event_id,
        "user_id": user_id,
        "trigger_type": trigger_type,
        "created_at_ms": created_at_ms,
        "abnormal_since_ms": abnormal_since_ms,
        "status": status,
    }


class PrioritySchedulerTests(unittest.TestCase):
    def test_location_timeout_uses_onset_for_normal_order_and_creation_for_promotion(self):
        quick = event("new", "QUICK_START_LOCATION_TIMEOUT", NOW - 10, NOW - 1000)
        booking = event("booking", "EVENT_BOOKING_TIMEOUT", NOW - 20, NOW - 500)
        self.assertEqual(effective_priority(quick, NOW, WAIT), NORMAL_PRIORITY)
        self.assertEqual([item["event_id"] for item in order_pending_events([booking, quick], NOW, WAIT)],
                         ["new", "booking"])
        metrics = schedule_metrics(quick, NOW, WAIT)
        self.assertEqual(metrics["queue_waiting_time_ms"], 10)
        self.assertEqual(metrics["abnormal_duration_ms"], 1000)
        self.assertEqual(effective_priority(quick, NOW + WAIT - 10, WAIT), HIGH_PRIORITY)

    def test_sos_uses_the_same_initial_priority_as_other_modes(self):
        normal = event("normal", "EVENT_BOOKING_TIMEOUT", NOW - 1, NOW - 900_000)
        sos = event("sos", "SOS", NOW - 2)
        self.assertEqual(effective_priority(sos, NOW, WAIT), NORMAL_PRIORITY)
        self.assertEqual(
            [item["event_id"] for item in order_pending_events([normal, sos], NOW, WAIT)],
            ["normal", "sos"],
        )

    def test_booking_and_quick_start_begin_at_same_priority(self):
        booking = event("booking", "EVENT_BOOKING_TIMEOUT", NOW - 1, NOW - 10)
        quick = event("quick", "QUICK_START_INACTIVITY", NOW - 2, NOW - 20)
        self.assertEqual(effective_priority(booking, NOW, WAIT), NORMAL_PRIORITY)
        self.assertEqual(effective_priority(quick, NOW, WAIT), NORMAL_PRIORITY)

    def test_normal_events_use_longest_abnormal_duration_first(self):
        booking = event("booking", "EVENT_BOOKING_TIMEOUT", NOW - 1, NOW - 50)
        quick = event("quick", "QUICK_START_INACTIVITY", NOW - 2, NOW - 100)
        self.assertEqual(
            [item["event_id"] for item in order_pending_events([booking, quick], NOW, WAIT)],
            ["quick", "booking"],
        )

    def test_equal_normal_duration_uses_earlier_event_creation(self):
        later = event("later", "EVENT_BOOKING_TIMEOUT", NOW - 10, NOW - 500)
        earlier = event("earlier", "QUICK_START_INACTIVITY", NOW - 20, NOW - 500)
        self.assertEqual(
            [item["event_id"] for item in order_pending_events([later, earlier], NOW, WAIT)],
            ["earlier", "later"],
        )

    def test_event_upgrades_exactly_at_wait_threshold(self):
        candidate = event(
            "candidate", "EVENT_BOOKING_TIMEOUT", NOW - WAIT, NOW - 300_000
        )
        self.assertEqual(effective_priority(candidate, NOW, WAIT), HIGH_PRIORITY)

    def test_event_does_not_upgrade_one_millisecond_before_threshold(self):
        candidate = event(
            "candidate", "QUICK_START_INACTIVITY", NOW - WAIT + 1, NOW - 300_000
        )
        self.assertEqual(effective_priority(candidate, NOW, WAIT), NORMAL_PRIORITY)

    def test_upgraded_event_and_sos_share_created_at_order(self):
        upgraded = event(
            "upgraded", "EVENT_BOOKING_TIMEOUT", NOW - WAIT - 1, NOW - 500_000
        )
        sos = event("sos", "SOS", NOW - WAIT)
        self.assertEqual(
            [item["event_id"] for item in order_pending_events([sos, upgraded], NOW, WAIT)],
            ["upgraded", "sos"],
        )

    def test_non_pending_events_are_ignored(self):
        closed = event(
            "closed", "EVENT_BOOKING_TIMEOUT", NOW - 1, NOW - 10, status="CLOSED"
        )
        dispatched = event("dispatched", "SOS", NOW - 2, status="DISPATCHED")
        pending = event("pending", "SOS", NOW - 3)
        self.assertEqual(order_pending_events([closed, dispatched, pending], NOW, WAIT), [pending])

    def test_input_order_does_not_change_output(self):
        events = [
            event("sos", "SOS", NOW - 10),
            event("booking", "EVENT_BOOKING_TIMEOUT", NOW - 5, NOW - 500),
            event("quick", "QUICK_START_INACTIVITY", NOW - 6, NOW - 600),
        ]
        expected = None
        for candidate in itertools.permutations(events):
            result = [
                item["event_id"] for item in order_pending_events(candidate, NOW, WAIT)
            ]
            if expected is None:
                expected = result
            self.assertEqual(result, expected)

    def test_cross_user_duplicate_event_ids_have_deterministic_final_tie_break(self):
        user_b = event("same", "SOS", NOW - 10, user_id="USER_B")
        user_a = event("same", "SOS", NOW - 10, user_id="USER_A")
        ordered = order_pending_events([user_b, user_a], NOW, WAIT)
        self.assertEqual([item["user_id"] for item in ordered], ["USER_A", "USER_B"])

    def test_schedule_metrics_are_transient_and_do_not_mutate_event(self):
        candidate = event(
            "candidate", "EVENT_BOOKING_TIMEOUT", NOW - 25, NOW - 75
        )
        before = dict(candidate)
        self.assertEqual(
            schedule_metrics(candidate, NOW, WAIT),
            {
                "effective_priority": NORMAL_PRIORITY,
                "queue_waiting_time_ms": 25,
                "abnormal_duration_ms": 75,
            },
        )
        self.assertEqual(candidate, before)

    def test_normal_event_requires_abnormal_start_not_after_creation(self):
        candidate = event(
            "candidate",
            "EVENT_BOOKING_TIMEOUT",
            NOW - 100,
            NOW - 99,
        )
        with self.assertRaisesRegex(
            SchedulerValidationError,
            "abnormal_since_ms cannot be later than created_at_ms",
        ):
            order_pending_events([candidate], NOW, WAIT)

    def test_normal_event_requires_creation_not_after_now(self):
        candidate = event(
            "candidate",
            "QUICK_START_INACTIVITY",
            NOW + 1,
            NOW,
        )
        with self.assertRaisesRegex(
            SchedulerValidationError,
            "created_at_ms cannot be later than now_ms",
        ):
            order_pending_events([candidate], NOW, WAIT)

    def test_normal_event_allows_equal_timestamp_boundaries(self):
        candidate = event(
            "candidate",
            "EVENT_BOOKING_TIMEOUT",
            NOW,
            NOW,
        )
        self.assertEqual(
            schedule_metrics(candidate, NOW, WAIT),
            {
                "effective_priority": NORMAL_PRIORITY,
                "queue_waiting_time_ms": 0,
                "abnormal_duration_ms": 0,
            },
        )

    def test_boolean_epoch_values_are_rejected(self):
        candidate = event(
            "candidate",
            "EVENT_BOOKING_TIMEOUT",
            True,
            0,
        )
        with self.assertRaises(SchedulerValidationError):
            order_pending_events([candidate], NOW, WAIT)

    def test_sos_accepts_rtdb_omitted_null_fields(self):
        candidate = event("sos", "SOS", NOW - 1)
        candidate.pop("abnormal_since_ms")
        self.assertEqual(order_pending_events([candidate], NOW, WAIT), [candidate])
        self.assertIsNone(
            schedule_metrics(candidate, NOW, WAIT)["abnormal_duration_ms"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
