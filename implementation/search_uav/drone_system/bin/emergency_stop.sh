#!/bin/bash


source /opt/ros/noetic/setup.bash
source /home/mike/Fast-Drone-250/devel/setup.bash 2>/dev/null || true

echo "[estop] $(date) sending LAND command via ROS..."
rostopic pub -1 /px4ctrl/takeoff_land quadrotor_msgs/TakeoffLand "takeoff_land_cmd: 2" 2>&1 || \
    echo "[estop] ROS publish failed (rosmaster down?)"

echo "[estop] sending abort via MQTT..."
mosquitto_pub -h localhost -t alin1/mission/abort \
    -m '{"reason":"emergency_stop.sh CLI"}' 2>&1 || \
    echo "[estop] MQTT publish failed (broker down?)"

echo "[estop] DONE"
