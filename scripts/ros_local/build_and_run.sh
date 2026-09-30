#!/bin/bash
set -euo pipefail
mkdir -p "$HOME"
source /opt/ros/noetic/setup.bash
cd /results/workspace
catkin_make -DCMAKE_BUILD_TYPE=RelWithDebInfo -j4
catkin_make install -j4
source /results/workspace/install/setup.bash
python3 -c 'from rescue_bridge.msg import WaypointCommand, WaypointFeedback, ExecutionControl; from quadrotor_msgs.msg import TakeoffLand; import rospy; print("generated ROS messages and rospy imported")'
dpkg-query -W > /results/installed-packages.txt
python3 /verification/ros_runtime_bench.py --output /results/runtime
