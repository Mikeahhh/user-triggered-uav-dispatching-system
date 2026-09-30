import tempfile
import unittest
from pathlib import Path

from execution_state import ExecutionManager, ExecutionError
from test_execution_state import mission


class ContinuousHoldTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.manager = ExecutionManager(clock=lambda: self.now)
        self.manager.admit(mission(waypoints=[{'lat': 0, 'lon': 0}]))
        self.manager.feedback('AUDIT/a', 'exec-a', 0, 'ACCEPTED')
        self.seq = 0

    def observe(self, valid=True, status='HOLDING'):
        self.seq += 1
        return self.manager.feedback('AUDIT/a', 'exec-a', 0, status,
                                     position_valid=valid, feedback_seq=self.seq)

    def hold(self, seconds):
        for _ in range(round(seconds * 5)):
            self.now = round(self.now + .2, 6)
            self.observe()

    def test_height_or_stale_position_cannot_start_hold(self):
        self.observe(False, 'OUTSIDE')
        self.assertEqual(self.manager.snapshot()['phase'], 'NAVIGATING')
        self.now += 10
        self.assertIsNone(self.manager.tick())
        self.assertFalse(self.manager.snapshot()['all_waypoints_completed'])

    def test_continuous_hold_and_land_publication_gate(self):
        self.observe(True, 'ARRIVED')
        self.assertEqual(self.manager.snapshot()['queue_remaining'], 1)
        self.hold(4.8)
        self.now = 14.999
        self.assertIsNone(self.manager.tick())
        self.now = 15.
        self.observe()
        self.assertEqual(self.manager.tick()['phase'], 'LAND_REQUEST_PENDING')
        self.assertFalse(self.manager.land_result(False)['delivery_eligible'])
        result = self.manager.land_result(True)
        self.assertTrue(result['delivery_eligible'])
        self.assertFalse(result['touchdown_confirmed'])

    def test_one_arrival_then_silence_does_not_finish(self):
        self.observe(True, 'ARRIVED')
        self.now += 5
        self.assertEqual(self.manager.tick()['reason'], 'HOLD_RESET_FEEDBACK_STALE')
        self.assertFalse(self.manager.snapshot()['all_waypoints_completed'])

    def test_drift_and_gap_each_require_a_new_full_hold(self):
        for mode in ('outside', 'gap'):
            with self.subTest(mode=mode):
                self.observe()
                self.hold(4.)
                if mode == 'outside': self.observe(False, 'OUTSIDE')
                else: self.now += 1.001
                self.observe()
                self.hold(1.)
                self.assertIsNone(self.manager.tick())
                self.observe(False, 'POSITION_STALE')

    def test_old_sequence_and_abort_never_authorize_delivery(self):
        self.observe()
        self.now += 5
        self.assertIsNone(self.manager.feedback('AUDIT/a', 'exec-a', 0, 'HOLDING',
                                                position_valid=True, feedback_seq=self.seq))
        self.manager.tick()
        self.assertFalse(self.manager.land_result(True, aborted=True)['delivery_eligible'])

    def test_completed_delivery_evidence_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'execution.json')
            engine = ExecutionManager(path)
            engine.admit(mission(waypoints=[{'lat': 0, 'lon': 0}], hover_seconds=0))
            engine.feedback('AUDIT/a', 'exec-a', 0, 'ACCEPTED')
            engine.feedback('AUDIT/a', 'exec-a', 0, 'ARRIVED', position_valid=True, feedback_seq=1)
            engine.tick()
            engine.land_result(True)
            engine.close()
            recovered = ExecutionManager(path)
            self.addCleanup(recovered.close)
            self.assertEqual(recovered.snapshot()['phase'], 'RECOVERY_REQUIRED')
            self.assertTrue(recovered.snapshot()['delivery_eligible'])
            self.assertIsNone(recovered.command())

    def test_explicit_retry_keeps_same_target_and_cannot_replay_running(self):
        with self.assertRaises(ExecutionError): self.manager.retry_rejected_target('exec-a')
        engine = ExecutionManager()
        engine.admit(mission())
        target = engine.command()
        engine.feedback('AUDIT/a', 'exec-a', 0, 'REJECTED')
        engine.retry_rejected_target('exec-a')
        self.assertEqual(engine.command(), target)

    def test_coordinate_frame_change_stops_navigation_and_cannot_retry_old_target(self):
        for phase in ('WAITING_TARGET_ACCEPTANCE', 'NAVIGATING', 'HOVERING'):
            with self.subTest(phase=phase):
                engine = ExecutionManager(clock=lambda: self.now)
                engine.admit(mission())
                if phase != 'WAITING_TARGET_ACCEPTANCE':
                    engine.feedback('AUDIT/a', 'exec-a', 0, 'ACCEPTED')
                if phase == 'HOVERING':
                    engine.feedback('AUDIT/a', 'exec-a', 0, 'ARRIVED', position_valid=True, feedback_seq=1)
                result = engine.feedback('AUDIT/a', 'exec-a', 0, 'REJECTED', 'COORDINATE_FRAME_CHANGED')
                self.assertEqual(result['phase'], 'RECOVERY_REQUIRED')
                self.assertFalse(result['collection_controller_valid'])
                self.assertFalse(result['delivery_eligible'])
                self.assertIsNone(engine.hover_deadline)
                self.assertIsNone(engine.last_hold_feedback)
                self.now += 10
                self.assertIsNone(engine.feedback('AUDIT/a', 'exec-a', 0, 'HOLDING',
                                                position_valid=True, feedback_seq=2))
                self.assertIsNone(engine.tick())
                self.assertIsNone(engine.command(include_navigating=True))
                with self.assertRaises(ExecutionError):
                    engine.retry_rejected_target('exec-a')
                self.assertEqual(engine.prepare_operator_reset('exec-a', 'frame recovery')['phase'],
                                 'OPERATOR_RESET_PENDING')

    def test_frame_change_report_requires_matching_current_target(self):
        for identity in (('OTHER/a', 'exec-a', 0), ('AUDIT/a', 'other-exec', 0), ('AUDIT/a', 'exec-a', 1)):
            self.assertIsNone(self.manager.feedback(*identity, 'REJECTED', 'COORDINATE_FRAME_CHANGED'))
        self.assertEqual(self.manager.snapshot()['phase'], 'NAVIGATING')


if __name__ == '__main__':
    unittest.main()
