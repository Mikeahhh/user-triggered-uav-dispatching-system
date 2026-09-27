#!/bin/bash

source /opt/ros/noetic/setup.bash
source /home/mike/Fast-Drone-250/devel/setup.bash 2>/dev/null || true

if [ -x /home/mike/Fast-Drone-250/shfiles/takeoff.sh ]; then
    bash /home/mike/Fast-Drone-250/shfiles/takeoff.sh
else
    rostopic pub -1 /px4ctrl/takeoff_land quadrotor_msgs/TakeoffLand "takeoff_land_cmd: 1"
fi
echo "[takeoff] command sent"
