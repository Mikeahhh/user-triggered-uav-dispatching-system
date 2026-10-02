#!/usr/bin/env python3


import rospy
import struct
import os
import numpy as np
from sensor_msgs.msg import PointCloud2, PointField


def load_pcd(filepath):

    points = []
    reading_data = False

    with open(filepath, 'r') as f:
        for line in f:
            if reading_data:
                parts = line.strip().split()
                if len(parts) >= 3:
                    points.append([float(parts[0]), float(parts[1]), float(parts[2])])
            elif line.startswith('DATA'):
                reading_data = True

    return np.array(points, dtype=np.float32)


def create_pointcloud2(points, frame_id='world', stamp=None):

    msg = PointCloud2()
    msg.header.frame_id = frame_id
    msg.header.stamp = stamp or rospy.Time.now()

    msg.height = 1
    msg.width = len(points)
    msg.is_bigendian = False
    msg.is_dense = True
    msg.point_step = 12
    msg.row_step = msg.point_step * msg.width

    msg.fields = [
        PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
        PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
        PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
    ]

    msg.data = points.astype(np.float32).tobytes()
    return msg


class TerrainPublisher:
    def __init__(self):
        rospy.init_node('terrain_publisher')

        pcd_file = rospy.get_param('~pcd_file', '')
        pub_rate = rospy.get_param('~pub_rate', 1.0)

        if not pcd_file or not os.path.exists(pcd_file):
            rospy.logerr("PCD file not found: %s", pcd_file)
            return

        rospy.loginfo("Loading terrain: %s", pcd_file)
        self.points = load_pcd(pcd_file)
        rospy.loginfo("Loaded %d points (X: %.0f~%.0f, Y: %.0f~%.0f, Z: %.0f~%.0f)",
                      len(self.points),
                      self.points[:, 0].min(), self.points[:, 0].max(),
                      self.points[:, 1].min(), self.points[:, 1].max(),
                      self.points[:, 2].min(), self.points[:, 2].max())


        self.cloud_pub = rospy.Publisher(
            '/map_generator/global_cloud', PointCloud2, queue_size=1, latch=True
        )


        self.publish_cloud()


        rospy.Timer(rospy.Duration(1.0 / pub_rate), lambda e: self.publish_cloud())
        rospy.loginfo("Terrain publisher ready (rate=%.1f Hz)", pub_rate)

    def publish_cloud(self):
        msg = create_pointcloud2(self.points)
        self.cloud_pub.publish(msg)

    def run(self):
        rospy.spin()


if __name__ == '__main__':
    try:
        pub = TerrainPublisher()
        pub.run()
    except rospy.ROSInterruptException:
        pass
