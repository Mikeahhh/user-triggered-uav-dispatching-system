#!/bin/bash

echo "[stop_stack] stopping rescue_bridge nodes..."
pkill -f "mqtt_bridge.py" 2>/dev/null
pkill -f "mission_commander" 2>/dev/null
pkill -f "phone_sos_receiver.py" 2>/dev/null
rm -f /home/mike/drone_system/rescue_receiver.pid

echo "[stop_stack] stopping ROS launches..."
pkill -f "rescue_bridge.launch" 2>/dev/null
pkill -f "fast_drone_250.launch" 2>/dev/null
pkill -f "px4.launch" 2>/dev/null
pkill -f "rs_camera.launch" 2>/dev/null
pkill -f "roslaunch" 2>/dev/null

sleep 3

echo "[stop_stack] stopping vins / mavros / camera nodes..."
pkill -f "vins_node" 2>/dev/null
pkill -f "loop_fusion" 2>/dev/null
pkill -f "mavros_node" 2>/dev/null
pkill -f "realsense2_camera" 2>/dev/null
pkill -f "ego_planner" 2>/dev/null
pkill -f "px4ctrl_node" 2>/dev/null

sleep 2

echo "[stop_stack] stopping rosmaster + rosout..."
pkill -f "rosmaster" 2>/dev/null
pkill -f "rosout" 2>/dev/null

sleep 1


LEFT=$(pgrep -af "mqtt_bridge\.py|phone_sos_receiver\.py|mission_commander|fast_drone_250|px4\.launch|rs_camera\.launch|rosmaster|rosout|vins_node|mavros_node|realsense2|ego_planner|px4ctrl" | head -20)
if [ -n "$LEFT" ]; then
    echo "[stop_stack] still running, sending SIGKILL:"
    echo "$LEFT"
    pkill -9 -f "mqtt_bridge\.py|phone_sos_receiver\.py|mission_commander|fast_drone_250|px4\.launch|rs_camera\.launch|rosmaster|rosout|vins_node|mavros_node|realsense2|ego_planner|px4ctrl" 2>/dev/null
fi

echo "[stop_stack] DONE"
