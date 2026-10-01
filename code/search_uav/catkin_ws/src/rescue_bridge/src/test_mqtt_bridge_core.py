import json
import sys
import threading
import tempfile
import types
import unittest
from unittest.mock import patch


def _install_import_stubs():
    rospy = types.ModuleType('rospy')
    for name in ('logwarn', 'loginfo', 'logerr', 'logwarn_throttle'):
        setattr(rospy, name, lambda *a, **k: None)
    rospy.Time = types.SimpleNamespace(now=lambda: types.SimpleNamespace(to_sec=lambda: 100.0))
    rospy.Duration = lambda v: v
    rospy.ROSInterruptException = RuntimeError
    sys.modules['rospy'] = rospy
    for package, classes in {
        'sensor_msgs': ('NavSatFix', 'NavSatStatus'), 'std_msgs': ('String',),
        'quadrotor_msgs': ('TakeoffLand',),
        'rescue_bridge': ('WaypointCommand', 'WaypointFeedback', 'ExecutionControl'),
    }.items():
        sys.modules[package] = types.ModuleType(package)
        module = types.ModuleType(package + '.msg')
        for name in classes:
            setattr(module, name, type(name, (), {}))
        sys.modules[package + '.msg'] = module
    sys.modules['sensor_msgs.msg'].NavSatStatus.STATUS_FIX = 0
    sys.modules['quadrotor_msgs.msg'].TakeoffLand.LAND = 2
    for name in ('paho', 'paho.mqtt', 'paho.mqtt.client'):
        sys.modules[name] = types.ModuleType(name)
    sys.modules['paho.mqtt.client'].Client = object


_install_import_stubs()
from mqtt_bridge import MqttBridge, DEFAULT_CONFIG, load_config
from execution_state import ExecutionManager
from mission_transfer_store import MissionTransferStore


class MqttBridgeCoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.clock = patch('mqtt_bridge.time.monotonic', lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.feedback_counter = 0
        self.ros_time = patch('mqtt_bridge.rospy.Time.now', lambda: types.SimpleNamespace(to_sec=lambda: self.now))
        self.ros_time.start(); self.addCleanup(self.ros_time.stop)
        self.transfer_temp = tempfile.TemporaryDirectory(); self.addCleanup(self.transfer_temp.cleanup)
        bridge = MqttBridge.__new__(MqttBridge)
        bridge.transfer_store = MissionTransferStore(self.transfer_temp.name)
        bridge._lock = threading.RLock()
        bridge.cfg = dict(DEFAULT_CONFIG)
        bridge.engine = ExecutionManager(clock=lambda: self.now)
        bridge._target_key = None
        bridge._target_attempts = 0
        bridge._target_deadline = None
        bridge._pending_reset = None
        bridge._latest_receiver_session = 'receiver-a'
        bridge._receiver_session_id = ''
        bridge.latest_drone_gps = types.SimpleNamespace(latitude=22., longitude=114.,
            header=types.SimpleNamespace(stamp=types.SimpleNamespace(to_sec=lambda: 100.)),
            status=types.SimpleNamespace(status=0))
        bridge._gps_received_at = 100.
        self.targets, self.controls, self.statuses, self.lands = [], [], [], []
        bridge.target_pub = types.SimpleNamespace(publish=self.targets.append)
        bridge.control_pub = types.SimpleNamespace(publish=self.controls.append)
        bridge.takeoff_land_pub = types.SimpleNamespace(publish=self.lands.append)
        bridge.client = types.SimpleNamespace(publish=lambda topic, payload, **kw: self.statuses.append(json.loads(payload)))
        bridge._send_land_command = lambda: self.lands.append('LAND') or True
        self.bridge = bridge

    def start(self, eid='exec-a', **overrides):
        data = dict(schema_version=2, execution_id=eid, mission_id='user/event',
                    mission_type='event', waypoints=[{'latitude':22.1,'longitude':114.1}],
                    return_to_launch=False, hover_seconds=0)
        data.update(overrides)
        return self.bridge._handle_multi(json.dumps(data).encode())

    def feedback(self, status, mid='user/event', eid='exec-a', index=0, session='receiver-a'):
        self.feedback_counter += 1
        self.bridge._on_feedback(types.SimpleNamespace(status=status, mission_id=mid,
            execution_id=eid, waypoint_index=index, reason='test', receiver_session_id=session, effective_altitude=5., arrival_threshold=1., vertical_threshold=.5, data_timeout=5., position_stamp=self.now, feedback_seq=self.feedback_counter, within_tolerance=status in ('ARRIVED','HOLDING')))

    def test_admission_and_target_rejection_are_distinct_and_same_target_can_retry(self):
        self.start(); self.feedback('REJECTED')
        admission = [value for value in self.statuses if value.get('message_type') == 'ADMISSION'][-1]
        self.assertTrue(admission['accepted'])
        self.assertEqual(self.statuses[-1]['message_type'], 'EXECUTION')
        self.assertEqual(self.statuses[-1]['phase'], 'TARGET_REJECTED')
        self.bridge._handle_retry(json.dumps(dict(mission_id='user/event', execution_id='exec-a',
            operator_confirmed=True, reason='fixture retry')).encode())
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'WAITING_TARGET_ACCEPTANCE')
        self.assertEqual(len(self.targets), 2)
        self.assertEqual({value.execution_id for value in self.targets}, {'exec-a'})
        self.feedback('ACCEPTED')
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'NAVIGATING')

    def test_coordinate_frame_change_during_navigation_requires_recovery_without_replay(self):
        self.start(hover_seconds=5)
        self.feedback('ACCEPTED')
        before = len(self.statuses)
        targets = len(self.targets)
        fault = dict(status='REJECTED', mission_id='user/event', execution_id='exec-a',
                     waypoint_index=0, reason='COORDINATE_FRAME_CHANGED', receiver_session_id='receiver-a')
        self.bridge._on_feedback(types.SimpleNamespace(**dict(fault, execution_id='another-execution')))
        self.assertEqual(len(self.statuses), before)
        self.bridge._on_feedback(types.SimpleNamespace(**fault))
        report = self.statuses[-1]
        self.assertEqual(report['phase'], 'RECOVERY_REQUIRED')
        self.assertEqual(report['message_type'], 'EXECUTION')
        self.assertTrue(report['accepted'])
        self.assertFalse(report['collection_controller_valid'])
        self.assertFalse(report['delivery_eligible'])
        self.assertIsNone(self.bridge._target_deadline)
        self.now += 60
        self.bridge._tick()
        self.feedback('ARRIVED')
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'RECOVERY_REQUIRED')
        self.assertEqual(len(self.targets), targets)
        self.assertEqual(self.lands, [])
        self.bridge._handle_retry(json.dumps(dict(mission_id='user/event', execution_id='exec-a',
            operator_confirmed=True, reason='must not replay stale coordinates')).encode())
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'RECOVERY_REQUIRED')
        self.assertEqual(len(self.targets), targets)
        self.assertEqual(self.lands, [])

    def test_hover_frame_fault_is_execution_recovery_not_ground_admission_rejection(self):
        from pathlib import Path
        ground = Path(__file__).resolve().parents[5] / 'ground_station'
        with patch.object(sys, 'path', [str(ground / 'src'), str(ground / 'tests')] + sys.path):
            import test_ground_station_rescue_flow as flow
        fixture = flow.RescueEventDispatchWrapperTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.call()
        intent = fixture.journal.get(('USER_A', 'sos__request_1'))
        task = intent['payload']
        self.assertEqual(self.bridge._handle_multi(json.dumps(task).encode()), 'NEW')
        admission = next(item for item in reversed(self.statuses) if item.get('message_type') == 'ADMISSION')
        with patch.object(flow.gs, '_schedule_ui'):
            flow.gs.process_admission_report(admission, fixture.root, fixture.journal)
        eid, mid = task['execution_id'], task['mission_id']
        self.feedback('ACCEPTED', mid=mid, eid=eid)
        self.feedback('ARRIVED', mid=mid, eid=eid)
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'HOVERING')
        targets, publications = len(self.targets), len(fixture.client.calls)
        self.bridge._on_feedback(types.SimpleNamespace(status='REJECTED', mission_id=mid, execution_id=eid,
            waypoint_index=0, reason='COORDINATE_FRAME_CHANGED', receiver_session_id='receiver-a'))
        report = self.statuses[-1]
        self.assertEqual(report['message_type'], 'EXECUTION')
        self.assertEqual(report['phase'], 'RECOVERY_REQUIRED')
        with patch.object(flow.gs, '_schedule_ui'):
            flow.gs.process_execution_report(report, fixture.root, fixture.journal)
        event = fixture.root.data['users']['USER_A']['rescue_events']['sos__request_1']
        self.assertEqual(event['status'], 'DISPATCHED')
        self.assertEqual(event['execution']['state'], 'ACCEPTED')
        self.assertEqual(event['execution']['last_report']['phase'], 'RECOVERY_REQUIRED')
        self.assertEqual(fixture.journal.get(('USER_A', 'sos__request_1'))['state'], 'ACCEPTED')
        self.now += 60
        self.bridge._tick()
        recovered = fixture.call(resume=True)
        self.assertEqual(recovered['firebase_status'], 'DISPATCHED')
        self.assertEqual(len(fixture.client.calls), publications)
        self.assertEqual(len(self.targets), targets)
        self.assertEqual(self.lands, [])

    def test_stale_hold_feedback_publishes_one_reset_without_new_goal_or_landing(self):
        self.start(hover_seconds=5)
        self.feedback('ACCEPTED'); self.feedback('ARRIVED')
        status_count = len(self.statuses)
        target_count = len(self.targets)
        self.now += 1.01
        self.bridge._tick()
        self.assertEqual(len(self.statuses), status_count + 1)
        self.assertEqual(self.statuses[-1]['phase'], 'HOVERING')
        self.assertEqual(self.statuses[-1]['reason'], 'HOLD_RESET_FEEDBACK_STALE')
        self.assertEqual(self.statuses[-1]['message_type'], 'EXECUTION')
        self.assertEqual(len(self.targets), target_count)
        self.assertEqual(self.lands, [])
        self.now += 10
        self.bridge._tick()
        self.assertEqual(len(self.statuses), status_count + 1)
        self.assertEqual(len(self.targets), target_count)
        self.assertEqual(self.lands, [])

    def test_stale_arrival_never_publishes_arrived_or_disables_timeout(self):
        self.start(); self.feedback('ACCEPTED')
        deadline = self.bridge._target_deadline
        self.bridge._on_feedback(types.SimpleNamespace(status='ARRIVED', mission_id='user/event', execution_id='exec-a',
            waypoint_index=0, reason='', receiver_session_id='receiver-a', position_stamp=90., feedback_seq=1,
            within_tolerance=True))
        self.assertNotIn('ARRIVED', [item['status'] for item in self.statuses])
        self.assertEqual(self.bridge._target_deadline, deadline)
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'NAVIGATING')

    def test_active_mission_cannot_be_overwritten(self):
        self.assertEqual(self.start(), 'NEW')
        self.feedback('ACCEPTED')
        queue = list(self.bridge.engine.queue)
        self.assertEqual(self.start('exec-b'), 'REJECTED')
        self.assertEqual(self.statuses[-1]['reason'], 'MISSION_BUSY')
        self.assertEqual(self.bridge.engine.active_id, 'exec-a')
        self.assertEqual(self.bridge.engine.queue, queue)
        self.assertEqual(len(self.targets), 1)

    def test_rtl_is_appended_and_counted_at_mission_start(self):
        self.assertEqual(self.start(return_to_launch=True), 'NEW')
        snap = self.bridge.engine.snapshot()
        self.assertEqual(snap['source_waypoint_total'], 1)
        self.assertEqual(snap['waypoint_total'], 2)
        self.assertEqual(self.bridge.engine.queue[-1], (22.,114.))

    def test_queue_complete_requests_land_and_keeps_admission_lock(self):
        self.start(); self.feedback('ACCEPTED'); self.feedback('ARRIVED'); self.bridge._tick()
        self.assertEqual(self.statuses[-1]['phase'], 'LAND_REQUESTED')
        self.assertEqual(self.statuses[-1]['status'], 'LANDING')
        self.assertTrue(self.statuses[-1]['land_command_requested'])
        self.assertFalse(self.statuses[-1]['touchdown_confirmed'])
        self.assertEqual(self.start('exec-b'), 'REJECTED')

    def test_stale_arrival_and_unassociated_legacy_status_cannot_advance(self):
        self.start(); self.feedback('ACCEPTED')
        for kwargs in ({'mid':'user/old'}, {'eid':'old'}, {'index':1}, {'session':'receiver-old'}):
            self.feedback('ARRIVED', **kwargs)
        self.bridge._on_ros_status(types.SimpleNamespace(data='{"status":"ARRIVED"}'))
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'NAVIGATING')
        self.assertEqual(len(self.targets), 1)

    def test_duplicate_admission_and_feedback_do_not_replay(self):
        self.start(); self.assertEqual(self.start(), 'DUPLICATE')
        self.feedback('ARRIVED')
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'WAITING_TARGET_ACCEPTANCE')
        self.feedback('ACCEPTED'); self.feedback('ARRIVED'); self.feedback('ARRIVED')
        self.assertEqual(len(self.targets), 1)
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'HOVERING')

    def test_effective_receiver_profile_is_journaled_and_invalid_profile_rejected(self):
        self.start(); self.feedback('ACCEPTED')
        profile = self.bridge.engine.snapshot()['runtime_profile']
        self.assertEqual(profile['effective_altitude'], 5.)
        self.assertEqual(profile['arrival_basis'], 'HORIZONTAL_XY_AND_VERTICAL_Z')
        self.assertEqual(profile['receiver_session_id'], 'receiver-a')
        self.setUp()
        self.start()
        self.bridge._on_feedback(types.SimpleNamespace(status='ACCEPTED', mission_id='user/event',
            execution_id='exec-a', waypoint_index=0, reason='', receiver_session_id='receiver-a',
            effective_altitude=float('nan'), arrival_threshold=1., data_timeout=5.))
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'TARGET_REJECTED')
        self.assertEqual(self.bridge.engine.snapshot()['reason'], 'INVALID_RECEIVER_PROFILE')

    def test_rejected_target_never_reports_navigation(self):
        self.start(); self.feedback('REJECTED')
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'TARGET_REJECTED')
        self.assertNotIn('NAVIGATING', [s['status'] for s in self.statuses])

    def test_target_acceptance_retry_is_bounded_and_correlated(self):
        self.start()
        for _ in range(3):
            self.now += 5; self.bridge._tick()
        self.assertEqual(len(self.targets), 3)
        self.assertEqual({(m.execution_id,m.waypoint_index,m.receiver_session_id) for m in self.targets},
                         {('exec-a',0,'receiver-a')})
        self.assertEqual(self.statuses[-1]['phase'], 'TARGET_ACCEPTANCE_TIMEOUT')

    def test_lost_arrival_is_recovered_by_same_target_probe(self):
        self.start(); self.feedback('ACCEPTED')
        self.now += 5; self.bridge._tick()
        self.assertEqual(len(self.targets), 2)

        self.feedback('ACCEPTED'); self.feedback('ARRIVED'); self.bridge._tick()
        self.assertEqual(self.statuses[-1]['phase'], 'LAND_REQUESTED')

    def test_unavailable_navigation_feedback_becomes_unknown_not_new_flight(self):
        self.start(); self.feedback('ACCEPTED')
        for _ in range(4):
            self.now += 5; self.bridge._tick()
        self.assertEqual(self.statuses[-1]['phase'], 'NAVIGATION_FEEDBACK_TIMEOUT')
        self.assertEqual(self.statuses[-1]['status'], 'UNKNOWN')
        self.assertEqual(self.bridge.engine.active_id, 'exec-a')

    def test_receiver_restart_does_not_rebind_active_execution(self):
        self.start(); self.feedback('ACCEPTED')
        self.feedback('READY', session='receiver-b')
        self.now += 5; self.bridge._tick()
        self.assertEqual(self.targets[-1].receiver_session_id, 'receiver-a')
        self.feedback('ARRIVED', session='receiver-b')
        self.assertEqual(self.bridge.engine.snapshot()['phase'], 'NAVIGATING')

    def test_missing_or_stale_rtl_fix_rejects_before_command(self):
        self.bridge.latest_drone_gps.status.status = -1
        self.assertEqual(self.start(return_to_launch=True), 'REJECTED')
        self.assertEqual(self.targets, [])
        self.bridge.latest_drone_gps.status.status = 0
        self.bridge._gps_received_at = 1.
        self.assertEqual(self.start(return_to_launch=True), 'REJECTED')

    def test_abort_requires_matching_execution_and_reset_waits_for_ack(self):
        self.start(); self.feedback('ACCEPTED')
        self.bridge._handle_abort(b'{}')
        self.assertEqual(self.lands, [])
        self.bridge._handle_abort(b'{"execution_id":"exec-a"}')
        self.assertEqual(len(self.lands), 1)
        request = b'{"execution_id":"exec-a","operator_confirmed":true,"reason":"operator checked"}'
        self.bridge._handle_reset(request)
        self.assertEqual(self.bridge.engine.active_id, 'exec-a')
        self.bridge._handle_reset(request)
        self.assertEqual([c.action for c in self.controls], ['CANCEL','RELEASE','RELEASE'])
        self.feedback('RELEASED', session='receiver-old')
        self.assertEqual(self.bridge.engine.active_id, 'exec-a')
        self.feedback('RELEASED')
        self.assertEqual(self.bridge.engine.active_id, '')
        self.assertEqual(self.start('exec-b'), 'NEW')

    def test_hold_feedback_timeout_configuration_and_ros_override(self):
        import rospy
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'cfg.yaml';path.write_text('hold_feedback_timeout_seconds: 0.75\n')
            with patch.object(rospy,'get_param',side_effect=lambda name,default:str(path) if name=='~config_file' else default,create=True):
                self.assertEqual(load_config()['hold_feedback_timeout_seconds'],.75)
            with patch.object(rospy,'get_param',side_effect=lambda name,default:str(path) if name=='~config_file' else .5,create=True):
                self.assertEqual(load_config()['hold_feedback_timeout_seconds'],.5)

    def test_configuration_rejects_nonfinite_or_nonpositive_transport_values(self):
        import tempfile
        from pathlib import Path
        import rospy
        for body in ('target_acceptance_timeout_seconds: .nan', 'gps_timeout_seconds: 0',
                     'target_retry_limit: 2.0', 'target_retry_limit: 0', 'hover_seconds: .inf',
                     'hold_feedback_timeout_seconds: 0', 'hold_feedback_timeout_seconds: .nan',
                     'hold_feedback_timeout_seconds: 6'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                p = Path(directory)/'cfg.yaml'; p.write_text(body)
                with patch.object(rospy, 'get_param', side_effect=lambda name, default: str(p) if name == '~config_file' else default, create=True):
                    with self.assertRaises(ValueError): load_config()


if __name__ == '__main__':
    unittest.main()
