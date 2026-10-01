import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import xmlrpc.client

import paho.mqtt.client as mqtt
import rospy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import NavSatFix, NavSatStatus
from rescue_bridge.msg import WaypointCommand, WaypointFeedback
from quadrotor_msgs.msg import TakeoffLand


INSTALLED = Path('/results/workspace/install/lib/rescue_bridge')
sys.path.insert(0, str(INSTALLED))
from execution_protocol import normalize_execution_payload
from mission_transfer_protocol import messages as transfer_messages


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def wait_for(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Timed out: ' + description)


class Scenario:
    def __init__(self, root, name):
        self.path = root / name
        self.path.mkdir()
        self.name = name
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.processes = []
        self.handles = []
        self.events = []
        self.feedback = []
        self.status = []
        self.goals = []
        self.commands = []
        self.land = []
        self.checks = {}
        self.pose = [0., 0., 1.]
        self.manual_target = list(self.pose)
        self.follow_goal = True
        self.goal = None
        self.frame = 'vins-original'
        self.origin = [4., -3.]
        self.angle = math.pi / 3
        self.invalid_odom = False
        self.bad_quaternion = False
        self.no_fix = False
        self.gps_delay = 0.
        self.pause_odom = False
        self.vertical_offset = 0.
        self.xy_offset = 0.
        self.task = None
        self.gps_pub = rospy.Publisher('/mavros/global_position/global', NavSatFix, queue_size=10)
        self.odom_pub = rospy.Publisher('/vins_fusion/odometry', Odometry, queue_size=10)
        self.subscriptions = [rospy.Subscriber('/move_base_simple/goal', PoseStamped, self.on_goal),
                              rospy.Subscriber('/rescue/waypoint_feedback', WaypointFeedback, self.on_feedback),
                              rospy.Subscriber('/rescue/waypoint_command', WaypointCommand, self.on_command),
                              rospy.Subscriber('/px4ctrl/takeoff_land', TakeoffLand, self.on_land)]
        self.client = mqtt.Client(client_id='synthetic-ros-' + name)
        self.connected = threading.Event()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect('127.0.0.1', 1883, 20)
        self.client.loop_start()
        if not self.connected.wait(5):
            raise RuntimeError('local MQTT observer did not connect')

    def event(self, kind, body):
        with self.lock:
            self.events.append(dict(elapsed=time.monotonic(), kind=kind, data=body))

    def on_connect(self, client, _userdata, _flags, rc):
        if rc == 0:
            client.subscribe('alin1/mission/status', qos=1)
            self.connected.set()

    def on_message(self, _client, _userdata, msg):
        body = json.loads(msg.payload.decode())
        with self.lock:
            self.status.append(body)
        self.event('mqtt_status', body)

    def on_feedback(self, msg):
        body = {name: getattr(msg, name) for name in msg.__slots__}
        with self.lock:
            self.feedback.append(body)
        self.event('ros_feedback', body)

    def on_command(self, msg):
        body = {name: getattr(msg, name) for name in msg.__slots__}
        with self.lock:
            self.commands.append(body)
        self.event('ros_command', body)

    def on_land(self, msg):
        with self.lock:
            self.land.append(msg.takeoff_land_cmd)
        self.event('ros_land_request', {'command': msg.takeoff_land_cmd})

    def on_goal(self, msg):
        point = msg.pose.position
        body = dict(x=point.x, y=point.y, z=point.z, frame=msg.header.frame_id)
        with self.lock:
            self.goal = body
            self.goals.append(body)
        self.event('ros_goal', body)

    def start_process(self, arguments, log_name):
        handle = (self.path / log_name).open('w')
        self.handles.append(handle)
        process = subprocess.Popen(arguments, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes.append(process)
        return process

    def start(self, altitude=1.):
        self.pose[2] = altitude
        self.manual_target[2] = altitude
        save(self.path / 'bridge-config.json', dict(mqtt_broker='127.0.0.1', mqtt_port=1883,
             execution_journal_path=str(self.path / 'execution.json'), target_acceptance_timeout_seconds=3,
             target_retry_limit=3, hold_feedback_timeout_seconds=1., gps_timeout_seconds=5.))
        self.start_process([str(INSTALLED / 'mission_commander'), '__name:=mission_commander',
                            '_flight_alt:=5.0', '_arrival_threshold:=1.0', '_vertical_threshold:=1.0',
                            '_data_timeout:=5.0', '_hold_feedback_timeout:=1.0', '_position_feedback_hz:=5.0'],
                           'mission_commander.log')
        self.start_process(['python3', str(INSTALLED / 'mqtt_bridge.py'), '__name:=mqtt_bridge',
                            '_config_file:=' + str(self.path / 'bridge-config.json')], 'mqtt_bridge.log')
        self.sensors = threading.Thread(target=self.sensor_loop, daemon=True)
        self.sensors.start()
        wait_for(lambda: any(message.get('status') == 'READY' for message in self.feedback), 10, 'commander READY')
        wait_for(lambda: any(message.get('status') == 'ONLINE' for message in self.status), 10, 'bridge ONLINE')
        time.sleep(.3)
        with self.lock:
            self.manual_target = [3., 0., altitude]
        wait_for(lambda: abs(self.pose[0] - 3.) < .01, 3, 'two-metre calibration baseline')
        time.sleep(.4)

    @staticmethod
    def gps(east, north):
        latitude, longitude = 22., 114.
        w = math.sqrt(1. - .00669437999014 * math.sin(math.radians(latitude)) ** 2)
        meridian = 6378137. * (1. - .00669437999014) / w ** 3
        normal = 6378137. / w
        return latitude + math.degrees(north / meridian), longitude + math.degrees(east / (normal * math.cos(math.radians(latitude))))

    def sensor_loop(self):
        last = time.monotonic()
        while not self.stop.is_set():
            current = time.monotonic()
            step = min(.1, current - last)
            last = current
            with self.lock:
                target = list(self.manual_target)
                if self.follow_goal and self.goal is not None:
                    dx = self.goal['x'] - self.origin[0]
                    dy = self.goal['y'] - self.origin[1]
                    target = [math.cos(self.angle) * dx + math.sin(self.angle) * dy + self.xy_offset,
                              -math.sin(self.angle) * dx + math.cos(self.angle) * dy,
                              self.goal['z'] + self.vertical_offset]
                delta = [target[index] - self.pose[index] for index in range(3)]
                horizontal = math.hypot(delta[0], delta[1])
                ratio = min(1., 4. * step / horizontal) if horizontal else 0.
                self.pose[0] += delta[0] * ratio
                self.pose[1] += delta[1] * ratio
                self.pose[2] += max(-2. * step, min(2. * step, delta[2]))
                east, north, height = self.pose
                latitude, longitude = self.gps(east, north)
                now = rospy.Time.now()
                gps = NavSatFix()
                gps.header.stamp = now - rospy.Duration(self.gps_delay)
                gps.header.frame_id = 'gps'
                gps.status.status = NavSatStatus.STATUS_NO_FIX if self.no_fix else NavSatStatus.STATUS_FIX
                gps.latitude, gps.longitude, gps.altitude = latitude, longitude, height
                odom = Odometry()
                odom.header.stamp = now
                odom.header.frame_id = self.frame
                odom.pose.pose.position.x = float('nan') if self.invalid_odom else math.cos(self.angle) * east - math.sin(self.angle) * north + self.origin[0]
                odom.pose.pose.position.y = math.sin(self.angle) * east + math.cos(self.angle) * north + self.origin[1]
                odom.pose.pose.position.z = height
                odom.pose.pose.orientation.w = 0. if self.bad_quaternion else 1.
                self.event('synthetic_sensor_input', dict(east=east, north=north, height=height, frame=self.frame,
                           gps_delay=self.gps_delay, pause_odom=self.pause_odom, invalid_odom=self.invalid_odom,
                           bad_quaternion=self.bad_quaternion, no_fix=self.no_fix, stamp=now.to_sec()))
                self.gps_pub.publish(gps)
                if not self.pause_odom:
                    self.odom_pub.publish(odom)
            self.stop.wait(.05)

    def send(self, topic, body):
        info = self.client.publish(topic, json.dumps(body, allow_nan=False), qos=1, retain=False)
        wait_for(info.is_published, 3, 'local broker publish acknowledgement')
        self.event('mqtt_input', dict(topic=topic, payload=body))

    def dispatch(self, points=((8., 0.),), hover=1.5, rtl=False, suffix=''):
        self.task = dict(schema_version=2, mission_id='ROS_AUDIT/' + self.name,
                         execution_id='ros-' + self.name + suffix, mission_type='event',
                         waypoints=[dict(zip(('lat', 'lon'), self.gps(*point))) for point in points],
                         altitude=5., hover_seconds=hover, return_to_launch=rtl)
        normalized = normalize_execution_payload(self.task)
        save(self.path / ('task' + suffix + '.json'), self.task)
        for message in transfer_messages(self.task, normalized['content_fingerprint']):
            self.send('alin1/mission/transfer', message)
        wait_for(lambda: any(message.get('message_type') == 'ADMISSION' and message.get('accepted') is True
                             and message.get('execution_id') == self.task['execution_id'] for message in self.status),
                 6, 'durable ADMISSION acceptance')

    def snapshot(self):
        try:
            journal = json.loads((self.path / 'execution.json').read_text())
            return journal['executions'].get(self.task['execution_id'], {}) if self.task else {}
        except (OSError, ValueError):
            return {}

    def phase(self, phase, timeout=12):
        def matching_phase():
            record = self.snapshot()
            return record if record.get('phase') == phase else None
        return wait_for(matching_phase, timeout, phase)

    def complete(self, timeout=15):
        result = self.phase('LAND_REQUESTED', timeout)
        wait_for(lambda: self.land, 3, 'actual ROS LAND command')
        wait_for(lambda: any(message.get('phase') == 'LAND_REQUESTED'
                             and message.get('execution_id') == self.task['execution_id'] for message in self.status),
                 3, 'MQTT completion report')
        self.checks['completed_all_points'] = result.get('all_waypoints_completed') is True
        self.checks['land_published'] = result.get('land_command_requested') is True and all(value == TakeoffLand.LAND for value in self.land)
        self.checks['delivery_eligible'] = result.get('delivery_eligible') is True
        self.checks['no_touchdown_claim'] = result.get('touchdown_confirmed') is False
        return result

    def retry(self):
        self.send('alin1/mission/operator_retry', dict(mission_id=self.task['mission_id'], execution_id=self.task['execution_id'],
                  operator_confirmed=True, reason='Synthetic input repaired; same target retry'))

    def close(self):
        self.stop.set()
        if hasattr(self, 'sensors'):
            self.sensors.join(2)
        for process in reversed(self.processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        self.client.loop_stop()
        self.client.disconnect()
        for subscription in self.subscriptions:
            subscription.unregister()
        self.gps_pub.unregister()
        self.odom_pub.unregister()
        for handle in self.handles:
            handle.close()
        with (self.path / 'events.jsonl').open('w') as handle:
            for event in self.events:
                handle.write(json.dumps(event, allow_nan=False) + '\n')


def normal(s):
    s.start()
    s.dispatch(((9., 0.), (9., 5.)), hover=5., rtl=True)
    result = s.complete(timeout=38)
    s.checks['two_source_points_plus_rtl'] = len(s.goals) == 3 and result['waypoint_total'] == 3
    s.checks['all_goal_altitudes_match'] = all(abs(goal['z'] - 5.) < 1e-9 for goal in s.goals)
    s.checks['waypoint_order'] = [command['waypoint_index'] for command in s.commands] == [0, 1, 2]
    hover_start = {}
    intervals = []
    for event in s.events:
        body = event['data']
        if event['kind'] != 'mqtt_status' or body.get('execution_id') != s.task['execution_id']:
            continue
        if body.get('phase') == 'HOVERING':
            hover_start.setdefault(body['waypoint_index'], event['elapsed'])
        if body.get('phase') == 'WAITING_TARGET_ACCEPTANCE' and body.get('waypoint_index', 0) > 0:
            previous = body['waypoint_index'] - 1
            if previous in hover_start:
                intervals.append(event['elapsed'] - hover_start[previous])
        if body.get('phase') == 'LAND_REQUESTED' and body['waypoint_index'] in hover_start:
            intervals.append(event['elapsed'] - hover_start[body['waypoint_index']])
    s.checks['three_continuous_five_second_hovers'] = len(intervals) == 3 and min(intervals) >= 4.8
    save(s.path / 'hover-durations.json', intervals)


def vertical(s):
    s.start(altitude=7.)
    s.vertical_offset = 2.
    s.dispatch()
    s.phase('NAVIGATING')
    time.sleep(3.)
    s.checks['xy_alone_cannot_arrive'] = s.snapshot()['phase'] == 'NAVIGATING' and not s.land
    s.checks['outside_feedback_seen'] = any(value['status'] == 'OUTSIDE' for value in s.feedback)
    s.vertical_offset = 0.
    s.complete()


def stale(s):
    s.start()
    s.dispatch(hover=2.)
    s.phase('HOVERING')
    s.pause_odom = True
    time.sleep(1.5)
    s.checks['stale_feedback_cannot_complete'] = not s.land and s.snapshot().get('all_waypoints_completed') is False
    s.checks['stale_feedback_reported'] = any(value['status'] == 'POSITION_STALE' for value in s.feedback)
    s.pause_odom = False
    resumed = time.monotonic()
    s.complete()
    s.checks['fresh_feedback_restarts_full_hover'] = time.monotonic() - resumed >= 1.9


def drift(s):
    s.start()
    s.dispatch(hover=2.)
    s.phase('HOVERING')
    s.xy_offset = 3.
    time.sleep(1.2)
    s.checks['drift_cannot_complete'] = not s.land and s.snapshot().get('all_waypoints_completed') is False
    s.checks['drift_resets_hold'] = s.snapshot().get('reason') == 'HOLD_RESET_POSITION_UNCONFIRMED'
    s.xy_offset = 0.
    resumed = time.monotonic()
    s.complete()
    s.checks['drift_requires_full_hover_again'] = time.monotonic() - resumed >= 1.9


def unmatched(s):
    s.start()
    s.gps_delay = .5
    s.invalid_odom = True
    time.sleep(.2)
    s.invalid_odom = False
    time.sleep(.3)
    s.dispatch()
    result = s.phase('TARGET_REJECTED')
    s.checks['unmatched_snapshot_rejected'] = result['reason'] == 'SYNCHRONIZED_POSITION_UNAVAILABLE'
    s.checks['no_goal_from_unmatched_snapshot'] = not s.goals and not s.land
    s.gps_delay = 0.
    time.sleep(.4)
    s.retry()
    s.complete()
    s.checks['same_execution_retry'] = all(command['execution_id'] == s.task['execution_id'] for command in s.commands)


def invalid_source(s, attribute, reason):
    s.start()
    setattr(s, attribute, True)
    time.sleep(.3)
    s.dispatch()
    result = s.phase('TARGET_REJECTED')
    s.checks['invalid_observation_rejected'] = result['reason'] == reason
    s.checks['no_goal_from_invalid_observation'] = not s.goals and not s.land
    setattr(s, attribute, False)
    time.sleep(.4)
    s.retry()
    s.complete()


def frame_change(s):
    s.start()
    s.dispatch(hover=2.)
    s.phase('HOVERING')
    s.invalid_odom = True
    time.sleep(.2)
    with s.lock:
        s.frame = 'vins-restarted'
        s.origin = [24., 7.]
        s.invalid_odom = False
        s.follow_goal = False
        s.manual_target = [s.pose[0] + 3., s.pose[1], s.pose[2]]
    result = s.phase('RECOVERY_REQUIRED')
    s.checks['invalid_then_frame_change_detected'] = result['reason'] == 'COORDINATE_FRAME_CHANGED'
    s.checks['collection_disabled'] = result['collection_controller_valid'] is False
    time.sleep(2.)
    s.checks['recalibration_does_not_resume_old_goal'] = s.snapshot()['phase'] == 'RECOVERY_REQUIRED' and len(s.goals) == 1 and not s.land
    s.checks['no_delivery_after_frame_fault'] = s.snapshot()['delivery_eligible'] is False
    old_execution = s.task['execution_id']
    s.send('alin1/mission/abort', dict(execution_id=old_execution, reason='Synthetic recovery abort'))
    s.phase('ABORT_LAND_REQUESTED')
    s.send('alin1/mission/operator_reset', dict(execution_id=old_execution, operator_confirmed=True,
           reason='Synthetic aircraft reset after aborted scenario; no physical aircraft'))
    s.phase('OPERATOR_RELEASED')
    s.checks['operator_release_received'] = bool(wait_for(lambda: any(value['status'] == 'RELEASED' for value in s.feedback),
                                                        3, 'correlated release feedback'))
    s.follow_goal = True
    s.dispatch(points=((s.pose[0] + 3., s.pose[1]),), suffix='-recovered')
    s.complete()
    s.checks['new_execution_after_release'] = s.task['execution_id'] != old_execution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(exist_ok=False)
    interfaces = [name for _, name in socket.if_nameindex()]
    report = dict(all_passed=False, interfaces=interfaces, master=os.environ.get('ROS_MASTER_URI'), scenarios={})
    addresses = json.loads(subprocess.check_output(['ip', '-j', 'address'], text=True))
    routes = json.loads(subprocess.check_output(['ip', '-j', 'route', 'show', 'table', 'all'], text=True))
    routes_v6 = json.loads(subprocess.check_output(['ip', '-j', '-6', 'route', 'show', 'table', 'all'], text=True))
    active = [value['ifname'] for value in addresses if 'UP' in value['flags']]
    isolated = (active == ['lo'] and all(not value['addr_info'] for value in addresses if value['ifname'] != 'lo')
                and all(value.get('dev') == 'lo' for value in routes + routes_v6))
    save(output / 'network-isolation.json', dict(interfaces=addresses, active_interfaces=active,
                                                ipv4_routes=routes, ipv6_routes=routes_v6,
                                                published_ports=[], physical_devices=False, isolated=isolated))
    if not isolated or report['master'] != 'http://127.0.0.1:11311':
        raise RuntimeError('ROS runtime must have only an isolated loopback interface')
    (output / 'mosquitto.conf').write_text('listener 1883 127.0.0.1\nallow_anonymous true\npersistence false\n')
    processes, handles = [], []
    try:
        for name, command in [('roscore', ['roscore']), ('mosquitto', ['mosquitto', '-c', str(output / 'mosquitto.conf')])]:
            handle = (output / (name + '.log')).open('w')
            handles.append(handle)
            processes.append(subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True))
        master = xmlrpc.client.ServerProxy('http://127.0.0.1:11311')

        def master_ready():
            try:
                return master.getPid('/local_verifier')[0] == 1
            except OSError:
                return False

        wait_for(master_ready, 15, 'real roscore')
        rospy.init_node('local_ros_verification', disable_signals=True)
        scenarios = [('normal_route_rtl', normal), ('vertical_arrival', vertical), ('stale_feedback', stale),
                     ('horizontal_drift', drift), ('unmatched_positions', unmatched),
                     ('invalid_quaternion', lambda s: invalid_source(s, 'bad_quaternion', 'ODOMETRY_NOT_READY')),
                     ('gps_no_fix', lambda s: invalid_source(s, 'no_fix', 'GPS_NOT_READY')),
                     ('frame_change_recovery', frame_change)]
        for name, operation in scenarios:
            scenario = Scenario(output, name)
            error = None
            try:
                operation(scenario)
            except Exception as exc:
                error = str(exc)
                (scenario.path / 'failure.log').write_text(traceback.format_exc())
            finally:
                scenario.close()
            result = dict(all_passed=error is None and bool(scenario.checks) and all(scenario.checks.values()),
                          checks=scenario.checks, error=error, goal_count=len(scenario.goals),
                          command_count=len(scenario.commands), land_message_count=len(scenario.land),
                          feedback_count=len(scenario.feedback))
            save(scenario.path / 'result.json', result)
            report['scenarios'][name] = result
            save(output / 'result.json', report)
            print(json.dumps({'scenario': name, 'all_passed': result['all_passed'], 'error': error}), flush=True)
        report['all_passed'] = all(value['all_passed'] for value in report['scenarios'].values())
    finally:
        rospy.signal_shutdown('synthetic local verification finished')
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        for handle in handles:
            handle.close()
        report['artifacts_sha256'] = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in sorted(output.rglob('*')) if path.is_file() and path != output / 'result.json'}
        save(output / 'result.json', report)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
