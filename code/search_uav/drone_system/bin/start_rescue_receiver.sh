#!/bin/bash

set -euo pipefail
umask 077

CONFIG=/home/mike/drone_system/config/rescue_receiver.env
RECEIVER=/home/mike/drone_system/receiver/phone_sos_receiver.py
LOG_DIR=/home/mike/drone_system/logs
PID_FILE=/home/mike/drone_system/rescue_receiver.pid

if [ ! -f "$CONFIG" ]; then
    echo "[rescue_receiver] missing $CONFIG"
    echo "[rescue_receiver] copy rescue_receiver.env.example and set a runtime token"
    exit 2
fi


chmod 600 "$CONFIG"

set -a
source "$CONFIG"
set +a

if [ -z "${UAV_RESCUE_TOKEN:-}" ]; then
    echo "[rescue_receiver] refusing to start without UAV_RESCUE_TOKEN"
    exit 2
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "[rescue_receiver] already running pid=$(cat "$PID_FILE")"
    exit 0
fi

mkdir -p "$LOG_DIR" "${UAV_RESCUE_DATA_DIR:-/home/mike/drone_system/data/rescue_records}"
chmod 700 "$LOG_DIR" "${UAV_RESCUE_DATA_DIR:-/home/mike/drone_system/data/rescue_records}"
TS=$(date +%Y%m%d_%H%M%S)
nohup python3 "$RECEIVER" > "$LOG_DIR/rescue_receiver_$TS.log" 2>&1 < /dev/null &
PID=$!
echo "$PID" > "$PID_FILE"
chmod 600 "$PID_FILE"
echo "[rescue_receiver] started pid=$PID log=$LOG_DIR/rescue_receiver_$TS.log"
