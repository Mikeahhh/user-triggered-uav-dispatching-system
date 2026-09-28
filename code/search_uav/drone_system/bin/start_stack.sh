#!/bin/bash


set -e

source /opt/ros/noetic/setup.bash
source /home/mike/Fast-Drone-250/devel/setup.bash 2>/dev/null || true
source /home/mike/catkin_ws/devel/setup.bash 2>/dev/null || true

LOG_DIR=/home/mike/drone_system/logs
mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)

echo "[start_stack] $TS  starting ROS stack..."

if ! systemctl is-active --quiet mosquitto; then
    echo "[start_stack] WARN: mosquitto inactive, attempting start..."
    sudo -n systemctl start mosquitto 2>/dev/null || \
        echo "[start_stack] WARN: could not start mosquitto (need sudoers)"
fi


if [ -f /home/mike/drone_system/config/rescue_receiver.env ]; then
    /home/mike/drone_system/bin/start_rescue_receiver.sh || \
        echo "[start_stack] WARN: phone rescue receiver failed to start"
else
    echo "[start_stack] WARN: phone rescue receiver not configured"
fi


sudo -n chmod 777 /dev/ttyACM0 2>/dev/null || echo "[start_stack] WARN: could not chmod /dev/ttyACM0 (need sudoers)"


echo "[start_stack] starting realsense..."
roslaunch realsense2_camera rs_camera.launch \
    > "$LOG_DIR/realsense_$TS.log" 2>&1 &
sleep 8


echo "[start_stack] starting mavros..."
roslaunch mavros px4.launch \
    > "$LOG_DIR/mavros_$TS.log" 2>&1 &
sleep 8


echo "[start_stack] starting VINS..."
roslaunch vins fast_drone_250.launch \
    > "$LOG_DIR/vins_$TS.log" 2>&1 &
sleep 5


echo "[start_stack] starting rescue_bridge..."
roslaunch rescue_bridge rescue_bridge.launch flight_alt:=5.0 \
    > "$LOG_DIR/rescue_bridge_$TS.log" 2>&1 &
sleep 3

echo "[start_stack] all started. Active rosnodes:"
rosnode list 2>/dev/null || echo "(rosnode not yet ready)"
echo
echo "[start_stack] logs in $LOG_DIR/"
echo "[start_stack] DONE"
