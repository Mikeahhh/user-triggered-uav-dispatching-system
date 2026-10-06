#!/bin/bash

source /opt/ros/noetic/setup.bash || echo "[estop] ROS environment could not be loaded" >&2
source /home/mike/Fast-Drone-250/devel/setup.bash 2>/dev/null || true

RESULT=0
# Ubuntu coreutils timeout bounds each independent publication attempt.
echo "[estop] $(date) sending LAND command via ROS..."
if ! timeout --kill-after=1s 5s rostopic pub -1 /px4ctrl/takeoff_land quadrotor_msgs/TakeoffLand "takeoff_land_cmd: 2"; then
    echo "[estop] LAND publication failed or timed out; attempting the mission abort independently" >&2
    RESULT=1
fi

CONFIG="${UAV_MISSION_CONFIG:-/home/mike/drone_system/config/mission_settings.yaml}"
if ! ABORT_DETAILS=$(python3 - "$CONFIG" <<'PY'
import json
import os
import re
import sys

try:
    import yaml
    with open(sys.argv[1], encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ValueError("mission configuration must be an object")
    journal_path = config.get("execution_journal_path")
    if not journal_path and config.get("mission_state_path"):
        journal_path = os.path.join(os.path.dirname(config["mission_state_path"]), "executions.json")
    if journal_path is None:
        journal_path = "/home/mike/drone_system/data/mission_state/executions.json"
    if not isinstance(journal_path, str) or not journal_path.strip():
        raise ValueError("execution journal path is invalid")
    with open(os.path.expanduser(journal_path), encoding="utf-8") as handle:
        journal = json.load(handle)
    if (not isinstance(journal, dict) or type(journal.get("journal_version")) is not int
            or journal["journal_version"] != 1 or not isinstance(journal.get("executions"), dict)):
        raise ValueError("execution journal version or structure is invalid")
    execution_id = journal.get("active_execution_id")
    if execution_id == "":
        raise ValueError("no active execution is recorded; no queue abort was sent")
    if not isinstance(execution_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", execution_id):
        raise ValueError("active execution identity is invalid")
    record = journal["executions"].get(execution_id)
    if (not isinstance(record, dict) or record.get("execution_id") != execution_id
            or not isinstance(record.get("mission_id"), str) or not record["mission_id"].strip()):
        raise ValueError("active execution record is missing or inconsistent")
    broker = config.get("mqtt_broker", "localhost")
    port = config.get("mqtt_port", 1883)
    topic = config.get("topic_abort", "alin1/mission/abort")
    for value in (broker, topic):
        if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError("MQTT endpoint or abort topic is invalid")
    if type(port) is not int or not 1 <= port <= 65535 or any(c in topic for c in "#+"):
        raise ValueError("MQTT port or abort topic is invalid")
    payload = json.dumps({"execution_id": execution_id, "reason": "emergency_stop.sh CLI"})
    print("\t".join((broker, str(port), topic, payload)))
except Exception as error:
    print("[estop] Cannot prepare a verified queue abort: " + str(error), file=sys.stderr)
    sys.exit(2)
PY
); then
    echo "[estop] Queue abort was not published; check the onboard mission state" >&2
    exit 1
fi

IFS=$'\t' read -r MQTT_HOST MQTT_PORT MQTT_TOPIC ABORT_PAYLOAD <<< "$ABORT_DETAILS"
echo "[estop] publishing abort for the recorded active execution..."
if ! timeout --kill-after=1s 5s mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$MQTT_TOPIC" -q 1 -m "$ABORT_PAYLOAD"; then
    echo "[estop] MQTT abort publication failed or timed out; queue cancellation is unconfirmed" >&2
    RESULT=1
fi

echo "[estop] Command attempts finished; verify UAV status and physical landing separately"
exit "$RESULT"
