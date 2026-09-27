import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GS = ROOT / 'FYP_alin1_SmartUAVRescueSystem_Ground_Station-main'
BRIDGE = ROOT / 'FYP_alin1_SmartUAVRescueSystem_Drone-main/catkin_ws/src/rescue_bridge/src'
sys.path[:0] = [str(GS), str(BRIDGE)]
import ground_station as gs
from mission_execution_protocol import normalized_task, task_fingerprint
from dispatch_journal import DispatchJournal
from rescue_event_manager import detect_pending_sos, record_user_contact, convert_alert_to_rescue_event, SAFETY_UNCONFIRMED
from execution_protocol import normalize_execution_payload, normalized_content
from execution_state import ExecutionError, ExecutionManager

NOW = 2_000_000


class FlightExecutionContractTests(unittest.TestCase):
    def fixture(self, trigger='EVENT_BOOKING_TIMEOUT', count=3):
        types = {'EVENT_BOOKING_TIMEOUT': ('booked_events', 'booking'),
                 'QUICK_START_INACTIVITY': ('QuickStartSessions', 'session'),
                 'QUICK_START_LOCATION_TIMEOUT': ('QuickStartSessions', 'session'),
                 'SOS': ('rescue_requests', 'request')}
        kind, record_id = types[trigger]
        points = [{'latitude': 22.0 + (index % 2) / 1000,
                   'longitude': 114.0 + (index % 2) / 1000} for index in range(count)]
        record = {'waypoints': points, 'expectedEndAtMs': NOW - 1}
        if trigger in {'QUICK_START_INACTIVITY', 'QUICK_START_LOCATION_TIMEOUT'}:
            record = {'status': 'ACTIVE', 'startTime': '1970-01-01T00:00:01Z',
                      'points': {str(i): {**point, 'timestamp': 1_000 + i}
                                 for i, point in enumerate(points)}}
        elif trigger == 'SOS':
            record = {'status': 'PENDING', 'latitude': 22.0, 'longitude': 114.0}
        event = {'event_id': 'synthetic-event', 'user_id': 'SYNTHETIC',
                 'trigger_type': trigger, 'primary_record_type': kind,
                 'primary_record_id': record_id, 'status': 'PENDING',
                 'created_at_ms': NOW - 1, 'abnormal_since_ms': NOW - 1}
        if trigger == 'SOS':
            alert = detect_pending_sos('SYNTHETIC', record_id, record, NOW - 1)
            alert = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
            _, event = convert_alert_to_rescue_event(alert, NOW - 1, True)
        return {kind: {record_id: record}}, event

    def produce(self, trigger='EVENT_BOOKING_TIMEOUT', count=3, **parameters):
        user, event = self.fixture(trigger, count)
        prepared = gs.prepare_mission_for_rescue_event(user, event, NOW)
        payload = gs.build_execution_payload(prepared, 'synthetic-execution', **parameters)
        return prepared, json.loads(json.dumps(payload, allow_nan=False))

    def test_each_trigger_actual_producer_is_accepted_with_identical_content(self):
        for trigger in ('EVENT_BOOKING_TIMEOUT', 'QUICK_START_INACTIVITY', 'QUICK_START_LOCATION_TIMEOUT', 'SOS'):
            with self.subTest(trigger=trigger):
                prepared, wire = self.produce(trigger)
                parsed = normalize_execution_payload(wire)
                self.assertEqual(normalized_task(wire), normalized_content(parsed))
                self.assertEqual(task_fingerprint(wire), parsed['content_fingerprint'])
                self.assertEqual(parsed['waypoints'], prepared['waypoints'])
                manager = ExecutionManager()
                self.addCleanup(manager.close)
                disposition, state = manager.admit(parsed, launch_fix=(22.0, 114.0))
                self.assertEqual(disposition, 'NEW')
                self.assertEqual(state['source_waypoint_total'], len(prepared['waypoints']))
                self.assertEqual(manager.command()['execution_id'], wire['execution_id'])

    def test_maximum_source_length_and_duplicate_points_survive_with_extra_rtl(self):
        prepared, wire = self.produce(count=1000)
        manager = ExecutionManager()
        self.addCleanup(manager.close)
        _, state = manager.admit(normalize_execution_payload(wire), launch_fix=(22.0, 114.0))
        self.assertEqual(state['waypoint_total'], 1001)
        self.assertEqual(manager.queue[:1000], prepared['waypoints'])
        self.assertEqual(manager.queue[-1], (22.0, 114.0))
        with self.assertRaises(ValueError):
            self.produce(count=1001)

    def test_effective_parameter_boundaries_and_nulls_match(self):
        for altitude, hover in [(0.5, 0), (120, 600), (None, None)]:
            _, wire = self.produce(altitude=altitude, hover_seconds=hover)
            parsed = normalize_execution_payload(wire)
            self.assertEqual(task_fingerprint(wire), parsed['content_fingerprint'])
            self.assertEqual(parsed['altitude'], altitude)
            self.assertEqual(parsed['hover_seconds'], hover)

    def test_invalid_wire_is_rejected_by_both_modules(self):
        _, original = self.produce()
        cases = [('schema_version', 2.0), ('schema_version', True),
                 ('execution_id', ''), ('mission_id', '../bad'),
                 ('return_to_launch', 1), ('altitude', 0.49), ('altitude', 121),
                 ('hover_seconds', -1), ('hover_seconds', 601),
                 ('waypoints', [[22, 114]]),
                 ('waypoints', [{'lat': True, 'lon': 0}]),
                 ('waypoints', [{'lat': float('nan'), 'lon': 0}])]
        for field, value in cases:
            with self.subTest(field=field, value=repr(value)):
                wire = {**original, field: value}
                with self.assertRaises((ValueError, TypeError)):
                    task_fingerprint(wire)
                with self.assertRaises((ValueError, TypeError)):
                    normalize_execution_payload(wire)

    def test_retry_and_persistent_restart_never_recreate_a_flight(self):
        _, wire = self.produce()
        mission = normalize_execution_payload(wire)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'uav-executions.json')
            first = ExecutionManager(path)
            first.admit(mission, launch_fix=(22.0, 114.0))
            first.feedback(wire['mission_id'], wire['execution_id'], 0, 'ACCEPTED')
            self.assertEqual(first.admit(mission)[0], 'DUPLICATE')
            first.close()
            recovered = ExecutionManager(path)
            self.addCleanup(recovered.close)
            self.assertEqual(recovered.admit(mission)[0], 'DUPLICATE')
            self.assertEqual(recovered.snapshot()['phase'], 'RECOVERY_REQUIRED')
            self.assertIsNone(recovered.command())
            changed = copy.deepcopy(wire)
            changed['waypoints'][0]['latitude'] += 0.01
            with self.assertRaisesRegex(ExecutionError, 'EXECUTION_CONFLICT'):
                recovered.admit(normalize_execution_payload(changed))

    def test_correlated_feedback_is_required_and_land_request_remains_locked(self):
        _, wire = self.produce(count=1, hover_seconds=0)
        wire['return_to_launch'] = False
        manager = ExecutionManager(clock=lambda: 10.0)
        self.addCleanup(manager.close)
        manager.admit(normalize_execution_payload(wire))
        for mission, execution, index in [(wire['mission_id'], 'old-execution', 0),
                                           ('wrong/request', wire['execution_id'], 0),
                                           (wire['mission_id'], wire['execution_id'], 1)]:
            self.assertIsNone(manager.feedback(mission, execution, index, 'ARRIVED'))
        self.assertIsNone(manager.feedback(wire['mission_id'], wire['execution_id'], 0, 'ARRIVED'))
        manager.feedback(wire['mission_id'], wire['execution_id'], 0, 'ACCEPTED')
        manager.feedback(wire['mission_id'], wire['execution_id'], 0, 'ARRIVED')
        self.assertEqual(manager.tick()['phase'], 'LAND_REQUEST_PENDING')
        state = manager.land_result(True)
        self.assertEqual(state['phase'], 'LAND_REQUESTED')
        self.assertFalse(state['touchdown_confirmed'])
        next_wire = {**wire, 'execution_id': 'new-execution'}
        with self.assertRaisesRegex(ExecutionError, 'MISSION_BUSY'):
            manager.admit(normalize_execution_payload(next_wire))

    def test_actual_gs_publish_bytes_parse_on_uav_after_durable_authorization_hook(self):
        prepared, wire = self.produce()
        order, messages = [], []
        class Info:
            rc = 0
            def wait_for_publish(self, timeout):
                return None
            def is_published(self):
                return True
        class FakeBroker:
            def publish(self, topic, payload, qos):
                order.append('publish')
                messages.append((topic, json.loads(payload), qos))
                return Info()
        def authorized():
            order.append('durable_authorization')
        user, token = wire['mission_id'].split('/')
        with patch.object(gs, 'mqtt_connected', True), patch.object(gs, 'mqtt_client', FakeBroker()), \
             patch.object(gs, 'DEMO_SCREENSHOT_MODE', False), patch.object(gs, '_show_message_safely'):
            result = gs.dispatch_mission(prepared['waypoints'], user, token,
                                         wire['mission_type'], wire['return_to_launch'],
                                         confirm_callback=lambda *_: True, execution=wire,
                                         before_publish=authorized)
        self.assertEqual(result['status'], gs.DISPATCH_PUBLISHED)
        self.assertEqual(order, ['durable_authorization', 'publish'])
        self.assertEqual(messages[0][2], 1)
        self.assertEqual(normalize_execution_payload(messages[0][1])['content_fingerprint'],
                         task_fingerprint(wire))

    def test_lost_broker_confirmation_then_gs_restart_reuses_uav_execution(self):
        user, event = self.fixture()
        user['rescue_events'] = {event['event_id']: event}
        data = {'users': {'SYNTHETIC': user}}
        class Reference:
            def __init__(self, path=()):
                self.path = path
            def child(self, segment):
                return Reference(self.path + tuple(segment.split('/')))
            def get(self):
                current = data
                for segment in self.path:
                    current = current.get(segment)
                return copy.deepcopy(current)
            def transaction(self, transform):
                updated = transform(self.get())
                current = data
                for segment in self.path[:-1]:
                    current = current[segment]
                current[self.path[-1]] = copy.deepcopy(updated)
                return copy.deepcopy(updated)
        manager = ExecutionManager()
        self.addCleanup(manager.close)
        deliveries = []
        class Info:
            rc = 0
            def wait_for_publish(self, timeout):
                if len(deliveries) == 1:
                    raise TimeoutError('synthetic lost broker confirmation after UAV receipt')
            def is_published(self):
                return True
        class Broker:
            def publish(self, topic, payload, qos):
                normalized = normalize_execution_payload(json.loads(payload))
                disposition, state = manager.admit(normalized, launch_fix=(22.0, 114.0))
                deliveries.append((disposition, state['execution_id']))
                return Info()
        with tempfile.TemporaryDirectory() as directory:
            journal_path = Path(directory) / 'dispatch.sqlite3'
            with patch.object(gs, 'rescue_runtime_config', {'ready': True}), \
                 patch.object(gs, 'mqtt_connected', True), patch.object(gs, 'mqtt_client', Broker()), \
                 patch.object(gs, 'DEMO_SCREENSHOT_MODE', False), patch.object(gs, '_show_message_safely'), \
                 patch.object(gs, '_show_workflow_error'), patch.object(gs, 'refresh_data'), \
                 patch.object(gs, '_epoch_now_ms', return_value=NOW):
                first = DispatchJournal(journal_path, 'synthetic-scope')
                selected = gs.select_rescue_event('SYNTHETIC', event['event_id'], Reference(), journal=first)
                self.assertEqual(selected['phase'], 'SELECTED')
                prepared = gs.prepare_active_rescue_event('SYNTHETIC', event['event_id'], Reference(), journal=first)
                self.assertEqual(prepared['phase'], 'PREPARED')
                result = gs.dispatch_rescue_event('SYNTHETIC', event['event_id'], Reference(),
                    journal=first, confirm_callback=lambda *_: True)
                self.assertEqual(result['status'], gs.DISPATCH_UNKNOWN)
                self.assertEqual(first.get(('SYNTHETIC', event['event_id']))['state'], 'UNKNOWN')
                first.close()
                restarted = DispatchJournal(journal_path, 'synthetic-scope')
                try:
                    review = gs.dispatch_rescue_event('SYNTHETIC', event['event_id'], Reference(),
                        journal=restarted, confirm_callback=lambda *_: True)
                    self.assertEqual(review['status'], gs.DISPATCH_UNKNOWN)
                    self.assertEqual(len(deliveries), 1)
                    retry = gs.dispatch_rescue_event('SYNTHETIC', event['event_id'], Reference(),
                        journal=restarted, confirm_callback=lambda *_: True, resume_execution=True)
                    self.assertEqual(retry['status'], gs.DISPATCH_PUBLISHED)
                    self.assertEqual(retry['execution_id'], result['execution_id'])
                    self.assertEqual(restarted.get(('SYNTHETIC', event['event_id']))['state'], 'COMMITTED')
                    self.assertEqual([entry[0] for entry in deliveries], ['NEW', 'DUPLICATE'])
                    self.assertEqual(deliveries[0][1], deliveries[1][1])
                    self.assertEqual(data['users']['SYNTHETIC']['rescue_events'][event['event_id']]['status'],
                                     'DISPATCHED')
                finally:
                    restarted.close()


if __name__ == '__main__':
    unittest.main()
