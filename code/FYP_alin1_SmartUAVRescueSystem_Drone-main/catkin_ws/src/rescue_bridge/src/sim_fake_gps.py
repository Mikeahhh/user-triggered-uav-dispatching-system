#!/usr/bin/env python3


import rospy
import math
from nav_msgs.msg import Odometry
from sensor_msgs.msg import NavSatFix, NavSatStatus

WGS84_A = 6378137.0
WGS84_E2 = 0.00669437999014


class SimFakeGps:
    def __init__(self):
        rospy.init_node('sim_fake_gps')

        self.home_lat = rospy.get_param('~home_lat', 22.3193)
        self.home_lon = rospy.get_param('~home_lon', 114.1694)
        self.init_x = rospy.get_param('~init_x', 0.0)
        self.init_y = rospy.get_param('~init_y', 0.0)

        rospy.Subscriber('/drone_0_visual_slam/odom', Odometry, self.odom_callback)
        self.gps_pub = rospy.Publisher('/sim/gps', NavSatFix, queue_size=1)

        rospy.loginfo("SimFakeGps: Home (%.6f, %.6f) at sim pos (%.1f, %.1f)",
                      self.home_lat, self.home_lon, self.init_x, self.init_y)

    def enu_to_gps(self, east_m, north_m):

        lat_rad = math.radians(self.home_lat)
        sin_lat = math.sin(lat_rad)
        W = math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        M = WGS84_A * (1.0 - WGS84_E2) / (W ** 3)
        N = WGS84_A / W

        dlat = north_m / M
        dlon = east_m / (N * math.cos(lat_rad))

        return self.home_lat + math.degrees(dlat), self.home_lon + math.degrees(dlon)

    def odom_callback(self, msg):

        dx = msg.pose.pose.position.x - self.init_x
        dy = msg.pose.pose.position.y - self.init_y

        lat, lon = self.enu_to_gps(dx, dy)

        fix = NavSatFix()
        fix.header.stamp = rospy.Time.now()
        fix.header.frame_id = 'gps'
        fix.latitude = lat
        fix.longitude = lon
        fix.altitude = msg.pose.pose.position.z
        fix.status.status = NavSatStatus.STATUS_FIX
        fix.status.service = NavSatStatus.SERVICE_GPS

        self.gps_pub.publish(fix)

    def run(self):
        rospy.spin()


if __name__ == '__main__':
    try:
        SimFakeGps().run()
    except rospy.ROSInterruptException:
        pass
