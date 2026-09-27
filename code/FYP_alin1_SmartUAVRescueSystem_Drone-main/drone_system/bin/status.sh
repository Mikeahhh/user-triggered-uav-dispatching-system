#!/bin/bash


source /opt/ros/noetic/setup.bash
source /home/mike/Fast-Drone-250/devel/setup.bash 2>/dev/null || true
source /home/mike/catkin_ws/devel/setup.bash 2>/dev/null || true

echo "=== Mosquitto MQTT broker ==="
systemctl is-active mosquitto || true

echo
echo "=== ROS master ==="
if pgrep -f rosmaster > /dev/null; then
    echo "  rosmaster RUNNING (pid $(pgrep -f rosmaster | head -1))"
else
    echo "  rosmaster NOT RUNNING"
fi

echo
echo "=== ROS nodes ==="
rosnode list 2>/dev/null || echo "  (no rosmaster)"

echo
echo "=== Key topics ==="
for t in /mavros/global_position/global /vins_fusion/odometry /rescue/incoming_gps /rescue/mission_status /move_base_simple/goal /px4ctrl/takeoff_land; do
    if rostopic list 2>/dev/null | grep -q "^${t}\$"; then
        rate=$(timeout 2 rostopic hz $t 2>/dev/null | grep "average rate" | tail -1 | awk '{print $3}')
        echo "  $t  rate=${rate:-N/A}"
    else
        echo "  $t  ABSENT"
    fi
done

echo
echo "=== Drone GPS (1 sample) ==="
timeout 2 rostopic echo -n 1 /mavros/global_position/global 2>/dev/null | head -10 || echo "  (no fix or topic absent)"

echo
echo "=== Mission status (last) ==="
timeout 2 rostopic echo -n 1 /rescue/mission_status 2>/dev/null || echo "  (no recent status)"
