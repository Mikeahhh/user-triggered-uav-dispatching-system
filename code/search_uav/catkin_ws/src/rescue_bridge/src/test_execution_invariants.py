import math
import random
import unittest

from execution_state import ExecutionManager
from test_execution_state import mission


class SeededExecutionInvariantTests(unittest.TestCase):
    def test_no_progress_without_a_complete_continuous_fresh_interval(self):
        for seed in range(2026093000, 2026093100):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                clock = [10.0]
                hover = rng.choice((0.0, 0.7, 1.0, 5.0))
                timeout = rng.choice((0.5, 1.0, 1.25))
                engine = ExecutionManager(clock=lambda: clock[0], hold_feedback_timeout=timeout)
                self.addCleanup(engine.close)
                engine.admit(mission(hover_seconds=hover, return_to_launch=True), (22.0, 114.0))
                total = engine.snapshot()['waypoint_total']
                self.assertEqual(total, 3)
                for target in range(total):
                    self.assertEqual(engine.snapshot()['waypoint_index'], target)
                    engine.feedback('AUDIT/a', 'exec-a', target, 'ACCEPTED')
                    evidence = []
                    max_sequence = -1
                    sequence = 0

                    def observe(delta, status, valid, emitted_sequence, identity_ok=True):
                        nonlocal max_sequence
                        clock[0] += delta
                        engine.feedback('AUDIT/a', 'exec-a' if identity_ok else 'wrong-execution',
                                        target, status, position_valid=valid, feedback_seq=emitted_sequence)
                        if identity_ok and type(emitted_sequence) is int and emitted_sequence > max_sequence:
                            max_sequence = emitted_sequence
                            evidence.append((clock[0], valid is True and status in ('ARRIVED', 'HOLDING')))
                        advanced = engine.tick()
                        state = engine.snapshot()
                        if state['waypoint_index'] != target or state['phase'] == 'LAND_REQUEST_PENDING':
                            self.assertIsNotNone(advanced)
                            self.assertTrue(evidence and evidence[-1][1])
                            latest = evidence[-1][0]
                            self.assertLessEqual(clock[0] - latest, timeout)
                            start = latest
                            for timestamp, inside in reversed(evidence[:-1]):
                                if not inside or start - timestamp > timeout:
                                    break
                                start = timestamp
                            self.assertGreaterEqual(clock[0] - start + 1e-9, hover)
                            self.assertFalse(state['delivery_eligible'])
                            self.assertFalse(state['land_command_requested'])
                            return True
                        self.assertEqual(state['queue_remaining'], total - target)
                        self.assertFalse(state['all_waypoints_completed'])
                        self.assertFalse(state['delivery_eligible'])
                        return False

                    finished = False
                    for _ in range(120):
                        sequence += 1
                        emitted = rng.choice((sequence, sequence, sequence, max_sequence, True, None, 1.2))
                        valid = rng.random() > 0.35
                        status = rng.choice(('ARRIVED', 'HOLDING')) if valid else rng.choice(('OUTSIDE', 'POSITION_STALE'))
                        if observe(rng.choice((0.01, 0.2, 0.8, timeout, timeout + 0.001, 2.0)),
                                   status, valid, emitted, rng.random() > 0.1):
                            finished = True
                            break
                    if not finished:
                        for _ in range(math.ceil(hover / 0.1) + 4):
                            sequence += 1
                            if observe(0.1, 'HOLDING', True, sequence):
                                finished = True
                                break
                    self.assertTrue(finished)
                self.assertTrue(engine.snapshot()['all_waypoints_completed'])
                self.assertEqual(engine.snapshot()['queue_remaining'], 0)
                self.assertFalse(engine.land_result(False)['delivery_eligible'])
                self.assertTrue(engine.land_result(True)['delivery_eligible'])
                self.assertFalse(engine.snapshot()['touchdown_confirmed'])


if __name__ == '__main__':
    unittest.main()
