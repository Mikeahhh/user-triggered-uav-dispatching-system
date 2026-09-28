#!/usr/bin/env python3


import rospy
import math
import uuid
import threading
from nav_msgs.msg import Odometry
from geometry_msgs.msg import PoseStamped
from rescue_bridge.msg import WaypointCommand, WaypointFeedback, ExecutionControl
from target_lifecycle import TargetLifecycle


WGS84_A = 6378137.0
WGS84_E2 = 0.00669437999014


DEFAULT_HOME_LAT = 22.3193
DEFAULT_HOME_LON = 114.1694


def locked_callback(callback):
    def guarded(self, *args, **kwargs):
        with self._lock:
            return callback(self, *args, **kwargs)
    return guarded


class SimGpsAdapter:
    def __init__(self):
        rospy.init_node('sim_gps_adapter')
        self._lock = threading.RLock()

        self.home_lat = rospy.get_param('~home_lat', DEFAULT_HOME_LAT)
        self.home_lon = rospy.get_param('~home_lon', DEFAULT_HOME_LON)

        self.init_x = rospy.get_param('~init_x', -15.0)
        self.init_y = rospy.get_param('~init_y', 0.0)
        self.flight_alt = rospy.get_param('~flight_alt', 1.0)

        self.current_odom = None
        self.has_active_goal = False
        self.goal_x = 0.0
        self.goal_y = 0.0
        self.active_mission_id = ""


        self.arrival_threshold = rospy.get_param("~arrival_threshold", 5.0)
        self.data_timeout = rospy.get_param("~data_timeout", 5.0)
        if not all(math.isfinite(v) and v > 0 for v in (self.flight_alt, self.arrival_threshold, self.data_timeout)):
            raise ValueError("invalid simulation profile")
        self.lifecycle = TargetLifecycle()
        self.receiver_session_id = uuid.uuid4().hex
        self.odom_received_at = None


        rospy.Subscriber('/rescue/waypoint_command', WaypointCommand, self.gps_callback)
        rospy.Subscriber('/rescue/execution_control', ExecutionControl, self.control_callback)
        rospy.Subscriber('/drone_0_visual_slam/odom', Odometry, self.odom_callback)


        self.goal_pub = rospy.Publisher('/move_base_simple/goal', PoseStamped, queue_size=1)
        self.status_pub = rospy.Publisher('/rescue/waypoint_feedback', WaypointFeedback, queue_size=10, latch=True)
        self.publish_status("READY", "RECEIVER_STARTED", {"mission_id": "", "execution_id": "", "waypoint_index": 0})
        self.ready_timer = rospy.Timer(rospy.Duration(1.0), self.announce_ready)

        rospy.loginfo("SimGpsAdapter ready | Home GPS: (%.6f, %.6f) = Sim pos (%.1f, %.1f)",
                      self.home_lat, self.home_lon, self.init_x, self.init_y)

    def announce_ready(self, _event=None):
        self.publish_status("READY", "RECEIVER_AVAILABLE", {"mission_id": "", "execution_id": "", "waypoint_index": 0})

    def gps_to_enu(self, lat1_deg, lon1_deg, lat2_deg, lon2_deg):

        lat1 = math.radians(lat1_deg)
        lat2 = math.radians(lat2_deg)
        dlat = lat2 - lat1
        dlon = math.radians(lon2_deg - lon1_deg)

        sin_lat = math.sin((lat1 + lat2) / 2.0)
        W = math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        M = WGS84_A * (1.0 - WGS84_E2) / (W ** 3)
        N = WGS84_A / W

        north = dlat * M
        east = dlon * N * math.cos((lat1 + lat2) / 2.0)
        return north, east

    @locked_callback
    def odom_callback(self, msg):
        if not all(math.isfinite(v) for v in (msg.pose.pose.position.x, msg.pose.pose.position.y, msg.pose.pose.position.z)):
            self.current_odom = None
            return
        age = rospy.Time.now().to_sec() - msg.header.stamp.to_sec()
        if not 0 <= age <= self.data_timeout:
            self.current_odom = None
            return
        self.current_odom = msg
        self.odom_received_at = rospy.Time.now().to_sec()


        if self.lifecycle.active:
            dx = msg.pose.pose.position.x - self.goal_x
            dy = msg.pose.pose.position.y - self.goal_y
            dist = math.sqrt(dx * dx + dy * dy)

            if self.lifecycle.arrived(dist, self.arrival_threshold):
                rospy.loginfo("ARRIVED at goal (dist=%.2f m)", dist)
                self.publish_status("ARRIVED", "HORIZONTAL_THRESHOLD_REACHED", self.lifecycle.current_target())
                self.has_active_goal = False

    @locked_callback
    def control_callback(self, msg):
        if msg.receiver_session_id != self.receiver_session_id:
            return
        if msg.action not in ("CANCEL", "RELEASE"):
            return
        if self.lifecycle.control(msg.mission_id, msg.execution_id, msg.action == "RELEASE"):
            self.has_active_goal = False
            self.publish_status("RELEASED" if msg.action == "RELEASE" else "CANCELLED",
                                "TARGET_TRACKER_ONLY", {"mission_id": msg.mission_id,
                                "execution_id": msg.execution_id, "waypoint_index": 0})

    @locked_callback
    def gps_callback(self, msg):
        target = {"mission_id": msg.mission_id, "execution_id": msg.execution_id,
                  "waypoint_index": msg.waypoint_index, "latitude": msg.latitude,
                  "longitude": msg.longitude,
                  "altitude": msg.altitude if msg.altitude_specified else self.flight_alt}
        if msg.receiver_session_id != self.receiver_session_id:
            self.publish_status("REJECTED", "RECEIVER_SESSION_MISMATCH", target)
            return
        reason = ""
        if self.current_odom is None or self.odom_received_at is None:
            reason = "ODOMETRY_NOT_READY"
        else:
            now = rospy.Time.now().to_sec()
            source_age = now - self.current_odom.header.stamp.to_sec()
            age = now - self.odom_received_at
            if not (0 <= source_age <= self.data_timeout and 0 <= age <= self.data_timeout):
                reason = "ODOMETRY_STALE"
        if msg.altitude_specified and not (math.isfinite(msg.altitude) and .5 <= msg.altitude <= 120):
            reason = "INVALID_TASK_ALTITUDE"
        status, detail, publish = self.lifecycle.offer(target, not reason, reason)
        if not publish:
            if status == "ARRIVED":
                self.publish_status("ACCEPTED", "DUPLICATE", target)
            self.publish_status(status, detail, target)
            return
        self.active_mission_id = msg.mission_id


        north, east = self.gps_to_enu(
            self.home_lat, self.home_lon,
            msg.latitude, msg.longitude
        )

        rospy.loginfo("Target GPS: (%.6f, %.6f) → ENU offset: N=%.1fm, E=%.1fm",
                      msg.latitude, msg.longitude, north, east)


        goal_x = self.init_x + east
        goal_y = self.init_y + north

        rospy.loginfo("Goal in sim frame: x=%.2f, y=%.2f, z=%.2f", goal_x, goal_y, self.flight_alt)


        goal = PoseStamped()
        goal.header.stamp = rospy.Time.now()
        goal.header.frame_id = "world"
        goal.pose.position.x = goal_x
        goal.pose.position.y = goal_y
        goal.pose.position.z = target["altitude"]


        if self.current_odom:
            dx = goal_x - self.current_odom.pose.pose.position.x
            dy = goal_y - self.current_odom.pose.pose.position.y
            yaw = math.atan2(dy, dx)
        else:
            yaw = 0.0

        goal.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.orientation.w = math.cos(yaw / 2.0)

        try:
            self.goal_pub.publish(goal)
        except Exception:
            self.lifecycle.control(target["mission_id"], target["execution_id"])
            self.publish_status("REJECTED", "LOCAL_GOAL_PUBLICATION_FAILED", target)
            return
        self.goal_x = goal_x
        self.goal_y = goal_y
        self.has_active_goal = True
        self.publish_status("ACCEPTED", "LOCAL_GOAL_PUBLISHED", target)

    def publish_status(self, status, reason, target):
        feedback = WaypointFeedback()
        feedback.mission_id = target["mission_id"]
        feedback.execution_id = target["execution_id"]
        feedback.waypoint_index = target["waypoint_index"]
        feedback.receiver_session_id = self.receiver_session_id
        feedback.effective_altitude = target.get("altitude", 0.0)
        feedback.arrival_threshold = self.arrival_threshold
        feedback.data_timeout = self.data_timeout
        feedback.status = status
        feedback.reason = reason
        self.status_pub.publish(feedback)

    def run(self):
        rospy.spin()


if __name__ == '__main__':
    try:
        adapter = SimGpsAdapter()
        adapter.run()
    except rospy.ROSInterruptException:
        pass
