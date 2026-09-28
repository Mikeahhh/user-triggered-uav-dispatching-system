#!/usr/bin/env python3


import os
import sys


for p in (
    os.path.dirname(os.path.abspath(__file__)),
    "/home/mike/catkin_ws/src/rescue_bridge/src",
    "/home/mike/Fast-Drone-250/devel/lib/python3/dist-packages",
    "/home/mike/catkin_ws/devel/lib/python3/dist-packages",
    "/opt/ros/noetic/lib/python3/dist-packages",
):
    if p not in sys.path:
        sys.path.insert(0, p)

import json
import math
import threading
import time

import rospy
import yaml
from sensor_msgs.msg import NavSatFix, NavSatStatus
from std_msgs.msg import String
from quadrotor_msgs.msg import TakeoffLand
from rescue_bridge.msg import WaypointCommand, WaypointFeedback, ExecutionControl
from execution_protocol import normalize_execution_payload
from execution_state import ExecutionManager, ExecutionError

import paho.mqtt.client as mqtt

from mission_protocol import MissionValidationError, UAV_SOFTWARE_VERSION, SYSTEM_RELEASE_ID


try:
    from paho.mqtt.client import CallbackAPIVersion
    _PAHO_V2 = True
except ImportError:
    _PAHO_V2 = False


class State:
    IDLE = "IDLE"
    NAVIGATING = "NAVIGATING"
    HOVERING = "HOVERING"
    LAND_REQUESTED = "LAND_REQUESTED"
    ABORTED = "ABORTED"


DEFAULT_CONFIG = {
    "hover_seconds": 5.0, "mqtt_broker": "localhost", "mqtt_port": 1883,
    "topic_target_legacy": "alin1/mission/target_gps",
    "topic_target_multi": "alin1/mission/multi_waypoint",
    "topic_status": "alin1/mission/status", "topic_abort": "alin1/mission/abort",
    "topic_operator_reset": "alin1/mission/operator_reset",
    "execution_journal_path": "/home/mike/drone_system/data/mission_state/executions.json",

    "gps_timeout_seconds": 5.0,

    "target_acceptance_timeout_seconds": 5.0, "target_retry_limit": 3,
}
CONFIG_PATH = "/home/mike/drone_system/config/mission_settings.yaml"


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = rospy.get_param("~config_file", CONFIG_PATH)
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
            if not isinstance(loaded, dict):
                raise ValueError("mission config must be a mapping")
            cfg.update(loaded)
            if "execution_journal_path" not in loaded and loaded.get("mission_state_path"):
                cfg["execution_journal_path"] = os.path.join(os.path.dirname(loaded["mission_state_path"]), "executions.json")
            if "gps_timeout_seconds" not in loaded and "gps_fix_timeout_seconds" in loaded:
                cfg["gps_timeout_seconds"] = loaded["gps_fix_timeout_seconds"]
    except FileNotFoundError:
        rospy.logwarn("mission configuration missing; using documented defaults")
    for key in ("gps_timeout_seconds", "target_acceptance_timeout_seconds"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid positive finite configuration: " + key)
    if type(cfg["target_retry_limit"]) is not int or cfg["target_retry_limit"] < 1:
        raise ValueError("target_retry_limit must be a positive integer")
    hover = cfg["hover_seconds"]
    if isinstance(hover, bool) or not isinstance(hover, (int, float)) or not math.isfinite(hover) or not 0 <= hover <= 600:
        raise ValueError("invalid hover_seconds")
    return cfg


def storage_guard(callback):
    def guarded(self, *args, **kwargs):
        try:
            return callback(self, *args, **kwargs)
        except (OSError, ExecutionError) as exc:
            self._target_deadline = None
            current = self.engine.snapshot()
            self._emit(dict(current, status="UNKNOWN", phase="PERSISTENCE_FAILURE",
                            reason=str(exc), touchdown_confirmed=False))
            rospy.logerr("execution persistence unavailable; operator recovery required: %s", exc)
    return guarded


class MqttBridge:
    def __init__(self):
        rospy.init_node("mqtt_bridge")
        self.cfg = load_config()
        self._lock = threading.RLock()
        self.latest_drone_gps = None
        self._gps_received_at = None
        self._target_key = None
        self._target_attempts = 0
        self._target_deadline = None
        self._pending_reset = None
        self._latest_receiver_session = ""
        self._receiver_session_id = ""
        path = self.cfg.get("execution_journal_path")
        if not isinstance(path, str) or not path.strip():
            raise ExecutionError("persistent execution journal is required")
        self.engine = ExecutionManager(path)
        self.target_pub = rospy.Publisher("/rescue/waypoint_command", WaypointCommand, queue_size=4)
        self.control_pub = rospy.Publisher("/rescue/execution_control", ExecutionControl, queue_size=4)
        self.takeoff_land_pub = rospy.Publisher("/px4ctrl/takeoff_land", TakeoffLand, queue_size=2)
        rospy.Subscriber("/rescue/waypoint_feedback", WaypointFeedback, self._on_feedback)
        rospy.Subscriber("/mavros/global_position/global", NavSatFix, self._on_drone_gps)

        rospy.Subscriber("/rescue/mission_status", String, self._on_ros_status)
        kwargs = {"client_id": "rescue_drone_bridge"}
        if _PAHO_V2:
            kwargs["callback_api_version"] = CallbackAPIVersion.VERSION1
        self.client = mqtt.Client(**kwargs)
        self.client.on_connect = self._on_mqtt_connect
        self.client.on_disconnect = self._on_mqtt_disconnect
        self.client.on_message = self._on_mqtt_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        broker = rospy.get_param("~mqtt_broker", self.cfg["mqtt_broker"])
        port = int(rospy.get_param("~mqtt_port", self.cfg["mqtt_port"]))
        self.client.connect(broker, port, keepalive=60)
        self.client.loop_start()
        self._timer = rospy.Timer(rospy.Duration(0.1), self._tick)

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc != 0:
            rospy.logerr("MQTT connection rejected rc=%s", rc)
            return
        for key in ("topic_target_legacy", "topic_target_multi", "topic_abort", "topic_operator_reset"):
            client.subscribe(self.cfg[key], qos=1)
        self._emit({"schema_version": 2, "status": "ONLINE", "phase": "ONLINE",
                    "mission_id": "", "execution_id": "", "touchdown_confirmed": False})
        if self.engine.active_id:
            self._publish_snapshot(self.engine.snapshot())

    def _on_mqtt_disconnect(self, client, userdata, rc):
        if rc:
            rospy.logwarn("MQTT disconnected; execution identity remains locked")

    def _on_mqtt_message(self, client, userdata, msg):
        if msg.topic == self.cfg["topic_target_multi"]:
            self._handle_multi(msg.payload)
        elif msg.topic == self.cfg["topic_target_legacy"]:
            self._handle_legacy(msg.payload)
        elif msg.topic == self.cfg["topic_abort"]:
            self._handle_abort(msg.payload)
        elif msg.topic == self.cfg["topic_operator_reset"]:
            self._handle_reset(msg.payload)

    @staticmethod
    def _decode(payload):
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise MissionValidationError("payload must be an object")
        return data

    def _handle_multi(self, payload):
        return self._handle_mission(payload, False)

    def _handle_legacy(self, payload):
        return self._handle_mission(payload, True)

    def _handle_mission(self, payload, legacy_topic):
        data = {}
        with self._lock:
            try:
                data = self._decode(payload)
                mission = normalize_execution_payload(data, legacy_topic=legacy_topic)
                result, snapshot = self.engine.admit(
                    mission, self._valid_launch_fix(), hover_seconds=float(self.cfg["hover_seconds"]))
                self._publish_snapshot(snapshot, disposition=result)
                if result == "NEW":
                    self._receiver_session_id = ""
                    self._send_current()
                return result
            except (ValueError, TypeError, OSError) as exc:
                self._reject(data, str(exc))
                return "REJECTED"

    def _reject(self, data, reason):
        self._emit({"schema_version": 2, "status": "REJECTED", "phase": "MISSION_REJECTED",
                    "mission_id": data.get("mission_id", "") if isinstance(data.get("mission_id", ""), str) else "",
                    "execution_id": data.get("execution_id", "") if isinstance(data.get("execution_id", ""), str) else "",
                    "active_execution_id": self.engine.active_id, "reason": reason,
                    "message": reason, "waypoint_index": None, "touchdown_confirmed": False})

    def _on_drone_gps(self, msg):
        with self._lock:
            self.latest_drone_gps = msg
            self._gps_received_at = time.monotonic()

    def _valid_launch_fix(self):
        msg = self.latest_drone_gps
        if msg is None or self._gps_received_at is None:
            return None
        try:
            limit = float(self.cfg["gps_timeout_seconds"])
            age = time.monotonic() - self._gps_received_at
            source_age = rospy.Time.now().to_sec() - msg.header.stamp.to_sec()
            if msg.status.status < NavSatStatus.STATUS_FIX or not (0 <= age <= limit and 0 <= source_age <= limit):
                return None
            if isinstance(msg.latitude, bool) or isinstance(msg.longitude, bool):
                return None
            lat, lon = float(msg.latitude), float(msg.longitude)
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                return None
            return lat, lon
        except (AttributeError, TypeError, ValueError):
            return None

    def _send_current(self):
        command = self.engine.command(include_navigating=True)
        if command is None:
            return
        key = (command["execution_id"], command["waypoint_index"])
        if self._target_key != key:
            self._target_key, self._target_attempts = key, 0
        self._target_attempts += 1
        self._target_deadline = time.monotonic() + float(self.cfg["target_acceptance_timeout_seconds"])
        if not self._receiver_session_id:
            self._receiver_session_id = self._latest_receiver_session
        if not self._receiver_session_id:
            return
        msg = WaypointCommand()
        msg.receiver_session_id = self._receiver_session_id
        for field in ("mission_id", "execution_id", "waypoint_index", "latitude", "longitude"):
            setattr(msg, field, command[field])
        msg.altitude_specified = command["altitude"] is not None
        msg.altitude = command["altitude"] if msg.altitude_specified else 0.0
        try:
            self.target_pub.publish(msg)
        except Exception as exc:
            rospy.logwarn("target publication failed; waiting for bounded retry: %s", exc)

    @storage_guard
    def _on_feedback(self, msg):
        with self._lock:
            if msg.status == "READY":
                if msg.receiver_session_id:
                    if (self.engine.active_id and self._receiver_session_id
                            and msg.receiver_session_id != self._receiver_session_id):
                        self.engine.invalidate_collection_controller()
                    self._latest_receiver_session = msg.receiver_session_id
                return
            if msg.status == "RELEASED":
                pending = self._pending_reset
                if (pending and msg.receiver_session_id == pending["receiver_session_id"] and msg.execution_id == self.engine.active_id == pending["execution_id"]
                        and msg.mission_id == self.engine.snapshot().get("mission_id")):
                    status = self.engine.operator_reset(msg.execution_id, confirmed=True, reason=pending["reason"])
                    self._pending_reset = None
                    self._publish_snapshot(status)
                return
            if msg.receiver_session_id != self._receiver_session_id:
                return
            current = self.engine.snapshot()
            if (msg.status == "ACCEPTED" and current.get("phase") == "NAVIGATING"
                    and msg.execution_id == current.get("execution_id")
                    and msg.mission_id == current.get("mission_id")
                    and type(msg.waypoint_index) is int and msg.waypoint_index == current.get("waypoint_index")):
                self._target_attempts = 0
                self._target_deadline = time.monotonic() + self.cfg["target_acceptance_timeout_seconds"]
                return
            profile = None
            feedback_status, feedback_reason = msg.status, msg.reason
            if msg.status == "ACCEPTED":
                profile = {key: getattr(msg, key, None) for key in
                           ("effective_altitude", "arrival_threshold", "data_timeout")}
                if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) and v > 0 for v in profile.values()):
                    feedback_status, feedback_reason = "REJECTED", "INVALID_RECEIVER_PROFILE"
                    profile = None
                else:
                    profile.update(receiver_session_id=msg.receiver_session_id, arrival_basis="HORIZONTAL_XY")
            status = self.engine.feedback(msg.mission_id, msg.execution_id, msg.waypoint_index,
                                          feedback_status, feedback_reason, runtime_profile=profile)
            if status is None:
                return
            self._target_deadline = None
            if feedback_status == "ACCEPTED":
                self._target_attempts = 0
                self._target_deadline = time.monotonic() + self.cfg["target_acceptance_timeout_seconds"]
            if msg.status == "ARRIVED":
                self._publish_snapshot(status, status_override="ARRIVED", phase_override="ARRIVED")
            self._publish_snapshot(status)

    def _on_ros_status(self, msg):


        rospy.logwarn_throttle(10.0, "legacy uncorrelated mission status ignored by execution controller")

    @storage_guard
    def _tick(self, _event=None):
        with self._lock:
            status = self.engine.tick()
            if status:
                if status["phase"] == "WAITING_TARGET_ACCEPTANCE":
                    self._publish_snapshot(status)
                    self._send_current()
                elif status["phase"] == "LAND_REQUEST_PENDING":
                    self._publish_snapshot(self.engine.land_result(self._send_land_command()))
            if self._target_deadline is not None and time.monotonic() >= self._target_deadline:
                current = self.engine.snapshot()
                key = (current.get("execution_id"), current.get("waypoint_index"))
                if current.get("phase") not in ("WAITING_TARGET_ACCEPTANCE", "NAVIGATING") or key != self._target_key:
                    self._target_deadline = None
                elif self._target_attempts < int(self.cfg["target_retry_limit"]):
                    self._send_current()
                else:
                    status = (self.engine.acceptance_timeout(*key) if current["phase"] == "WAITING_TARGET_ACCEPTANCE"
                              else self.engine.navigation_feedback_timeout(*key))
                    self._target_deadline = None
                    if status:
                        self._publish_snapshot(status)

    def _send_control(self, action, snapshot):
        msg = ExecutionControl()
        msg.mission_id = snapshot["mission_id"]
        msg.execution_id = snapshot["execution_id"]
        msg.action = action
        msg.receiver_session_id = self._latest_receiver_session
        self.control_pub.publish(msg)

    def _handle_abort(self, payload):
        with self._lock:
            data = {}
            try:
                data = self._decode(payload)
                current = self.engine.snapshot()
                if not current:
                    raise ExecutionError("no active execution")
                if data.get("execution_id") != current["execution_id"] and not (
                        current.get("legacy") and not data.get("execution_id")):
                    raise ExecutionError("abort execution_id mismatch")
                reason = data.get("reason", "operator abort")
                if not isinstance(reason, str) or not reason.strip():
                    raise ExecutionError("abort reason required")
                try:
                    self._send_control("CANCEL", current)
                except Exception as exc:
                    rospy.logwarn("commander cancellation not confirmed: %s", exc)
                self._target_deadline = None
                self._publish_snapshot(self.engine.land_result(self._send_land_command(), aborted=True,
                                                                reason=reason.strip()[:256]))
            except (ValueError, TypeError, OSError) as exc:
                self._reject(data, str(exc))

    def _handle_reset(self, payload):
        with self._lock:
            data = {}
            try:
                data = self._decode(payload)
                current = self.engine.snapshot()
                if (not current or data.get("execution_id") != current["execution_id"]
                        or data.get("operator_confirmed") is not True
                        or not isinstance(data.get("reason"), str) or not data["reason"].strip()):
                    raise ExecutionError("matching execution, explicit operator confirmation and reason required")
                if current["phase"] in ("NAVIGATING", "HOVERING", "WAITING_TARGET_ACCEPTANCE", "LAND_REQUEST_PENDING"):
                    raise ExecutionError("abort or reconcile execution before reset")
                self._pending_reset = {"execution_id": current["execution_id"], "reason": data["reason"].strip()[:256],
                                       "receiver_session_id": self._latest_receiver_session}
                status = self.engine.prepare_operator_reset(current["execution_id"], self._pending_reset["reason"])
                self._publish_snapshot(status)
                try:
                    self._send_control("RELEASE", status)
                except Exception as exc:
                    rospy.logwarn("release publication failed; explicit same-execution reset may retry: %s", exc)
            except (ValueError, TypeError, OSError) as exc:
                self._reject(data, str(exc))

    def _send_land_command(self):
        msg = TakeoffLand()
        msg.takeoff_land_cmd = TakeoffLand.LAND
        published = False
        for _ in range(3):
            try:
                self.takeoff_land_pub.publish(msg)
                published = True
            except Exception as exc:
                rospy.logwarn("LAND request publication failed: %s", exc)
            time.sleep(0.05)
        return published

    def _publish_snapshot(self, snapshot, *, disposition="", status_override="", phase_override=""):
        phase = snapshot.get("phase", "IDLE")
        status_map = {"WAITING_TARGET_ACCEPTANCE": "ACCEPTED", "TARGET_REJECTED": "REJECTED",
                      "TARGET_ACCEPTANCE_TIMEOUT": "UNKNOWN", "NAVIGATION_FEEDBACK_TIMEOUT": "UNKNOWN", "RECOVERY_REQUIRED": "UNKNOWN",
                      "LAND_REQUESTED": "LANDING", "ABORT_LAND_REQUESTED": "ABORTED",
                      "LAND_REQUEST_FAILED": "REJECTED", "OPERATOR_RELEASED": "RELEASED"}
        payload = dict(snapshot)
        payload.update(active_execution_id=self.engine.active_id, status=status_override or status_map.get(phase, phase),
                       phase=phase_override or phase, message=snapshot.get("reason", ""),
                       disposition=disposition, touchdown_confirmed=False)
        self._emit(payload)

    def _emit(self, payload):
        payload = dict(payload, uav_software_version=UAV_SOFTWARE_VERSION, system_release_id=SYSTEM_RELEASE_ID)
        try:
            self.client.publish(self.cfg["topic_status"], json.dumps(payload, sort_keys=True, allow_nan=False), qos=1)
        except Exception as exc:
            rospy.logwarn("status publication failed; execution journal retained: %s", exc)

    def run(self):
        rospy.on_shutdown(self._shutdown)
        rospy.spin()

    def _shutdown(self):
        self._timer.shutdown()
        self._emit({"schema_version": 2, "status": "OFFLINE", "phase": "OFFLINE",
                    "mission_id": "", "execution_id": "", "touchdown_confirmed": False})
        self.client.loop_stop()
        self.client.disconnect()
        self.engine.close()


def main():
    try:
        MqttBridge().run()
    except rospy.ROSInterruptException:
        pass


if __name__ == "__main__":
    main()
