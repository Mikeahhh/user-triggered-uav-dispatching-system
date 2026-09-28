#!/bin/bash


echo "[start_sim] stopping any existing ROS stack first..."
/home/mike/drone_system/bin/stop_stack.sh 2>&1 | grep -v "^\$"
sleep 2


source /opt/ros/noetic/setup.bash
source /home/mike/catkin_ws/devel/setup.bash
source /home/mike/Fast-Drone-250/devel/setup.bash --extend


if ! rospack find ego_planner >/dev/null 2>&1; then
    echo "[start_sim] FATAL: ego_planner not in ROS_PACKAGE_PATH"
    echo "[start_sim] ROS_PACKAGE_PATH=$ROS_PACKAGE_PATH"
    exit 1
fi
if ! rospack find rescue_bridge >/dev/null 2>&1; then
    echo "[start_sim] FATAL: rescue_bridge not in ROS_PACKAGE_PATH"
    echo "[start_sim] ROS_PACKAGE_PATH=$ROS_PACKAGE_PATH"
    exit 1
fi

LOG_DIR=/home/mike/drone_system/logs
mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)

echo "[start_sim] $TS  starting Lion Rock SIM stack..."
echo "[start_sim] ego_planner   -> $(rospack find ego_planner)"
echo "[start_sim] rescue_bridge -> $(rospack find rescue_bridge)"


if ! systemctl is-active --quiet mosquitto; then
    echo "[start_sim] WARN: mosquitto inactive, attempting start..."
    sudo -n systemctl start mosquitto 2>/dev/null || \
        echo "[start_sim] WARN: could not start mosquitto (need sudoers)"
fi


echo "[start_sim] launching lion_rock_sim.launch..."
setsid roslaunch ego_planner lion_rock_sim.launch \
    > "$LOG_DIR/lion_rock_sim_$TS.log" 2>&1 < /dev/null &
disown
sleep 6


echo "[start_sim] launching rescue_bridge_lion_rock.launch..."
setsid roslaunch rescue_bridge rescue_bridge_lion_rock.launch \
    > "$LOG_DIR/rescue_bridge_sim_$TS.log" 2>&1 < /dev/null &
disown
sleep 4

echo "[start_sim] active rosnodes:"
rosnode list 2>/dev/null || echo "(rosmaster not yet ready)"
echo
echo "[start_sim] logs:"
echo "  $LOG_DIR/lion_rock_sim_$TS.log"
echo "  $LOG_DIR/rescue_bridge_sim_$TS.log"
echo "[start_sim] DONE — RViz should be up. Dispatch from ground station to fly."
