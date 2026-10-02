#!/usr/bin/env python3


import os
import sys
import json
import time
import math
import threading
import subprocess
import datetime
import collections
import re

from recording_state import recording_result_error, RecordingJournal, RecordingStateError, matches_recording_status


for p in (
    "/home/mike/Fast-Drone-250/devel/lib/python3/dist-packages",
    "/home/mike/catkin_ws/devel/lib/python3/dist-packages",
    "/opt/ros/noetic/lib/python3/dist-packages",
):
    if p not in sys.path:
        sys.path.insert(0, p)

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

import paho.mqtt.client as mqtt
try:
    from paho.mqtt.client import CallbackAPIVersion
    _PAHO_V2 = True
except ImportError:
    _PAHO_V2 = False

import yaml


COLOR_BG       = "#0d1b2a"
COLOR_PANEL    = "#1b263b"
COLOR_PANEL_HI = "#2c3e50"
COLOR_BORDER   = "#3a4a6b"
COLOR_TEXT     = "#e0e1dd"
COLOR_DIM      = "#778899"
COLOR_OK       = "#00ff9d"
COLOR_WARN     = "#ffcc00"
COLOR_BAD      = "#ff4444"
COLOR_ACCENT   = "#E53935"
COLOR_ACCENT_H = "#C62828"

FONT_TITLE = ("Segoe UI", 18, "bold")
FONT_HDR   = ("Segoe UI", 14, "bold")
FONT_BODY  = ("Segoe UI", 12)
FONT_MONO  = ("Consolas", 10)


CONFIG_PATH = "/home/mike/drone_system/config/mission_settings.yaml"
LOG_PATH    = "/home/mike/drone_system/logs/drone_console.log"
VIDEO_DIR      = os.path.expanduser("~/Videos/drone_recordings")
VIDEO_MANIFEST_DIR = os.path.join(VIDEO_DIR, "manifests")
CAMERA_DEV     = 6
CAM_RECORDER   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cam_recorder.py")
UAV_SOFTWARE_VERSION = "1.2.0"
SYSTEM_RELEASE_ID = "MASS26-20260806"
RECORDER_EVENT_PREFIX = "RECORDER_EVENT "

DEFAULTS = {
    "mqtt_broker": "localhost",
    "mqtt_port": 1883,
    "topic_target_multi": "alin1/mission/multi_waypoint",
    "topic_status":       "alin1/mission/status",
    "topic_abort":        "alin1/mission/abort",
    "flight_altitude_meters": 5.0,
    "hover_seconds": 5.0,
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            disk = yaml.safe_load(f) or {}
        cfg.update(disk)
    except Exception:
        pass
    return cfg


STACK_NODES = [
    ("ROS Master",     "pgrep -af rosmaster"),
    ("Mosquitto",      "systemctl is-active mosquitto"),
    ("Realsense",      "rosnode list 2>/dev/null | grep -q camera"),
    ("MAVROS",         "rosnode list 2>/dev/null | grep -q mavros"),
    ("VINS",           "rosnode list 2>/dev/null | grep -q vins"),
    ("EGO-Planner",    "rosnode list 2>/dev/null | grep -q ego"),
    ("px4ctrl",        "rosnode list 2>/dev/null | grep -q px4ctrl"),
    ("mqtt_bridge",    "rosnode list 2>/dev/null | grep -q mqtt_bridge"),
    ("mission_cmdr",   "rosnode list 2>/dev/null | grep -q mission_commander"),
    ("Phone receiver", "pgrep -af phone_sos_receiver.py"),
]


def _check_node(cmd):

    try:
        if cmd.startswith("systemctl is-active"):
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
            return "ok" if r.stdout.strip() == "active" else "down"
        if "pgrep" in cmd:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
            return "ok" if r.returncode == 0 and r.stdout.strip() else "down"

        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
        return "ok" if r.returncode == 0 else "down"
    except Exception:
        return "down"


class DroneConsole:
    def __init__(self):
        self.cfg = load_config()
        self._log_buf = collections.deque(maxlen=500)
        self._lock = threading.RLock()


        self.current_status = "OFFLINE"
        self.current_phase = "OFFLINE"
        self.land_command_requested = False
        self.touchdown_confirmed = False
        self.current_message = ""
        self.current_mission_id = ""
        self.current_execution_id = ""
        self.current_wp_index = 0
        self.current_wp_total = 0
        self.queue_waypoints = []
        self.drone_gps = None
        self.takeoff_gps = None


        self._recording = False
        self._rec_starting = False
        self._rec_stopping = False
        self._rec_paused = False
        self._rec_proc = None
        self._rec_start_time = None
        self._rec_elapsed_at_pause = 0
        self._rec_filename = ""
        self._rec_mission_id = ""
        self._rec_execution_id = ""
        self._rec_journal = None


        self.stack_status = {name: "down" for name, _ in STACK_NODES}

        self._build_ui()
        self._init_mqtt()
        self._start_pollers()


    def _build_ui(self):
        ctk.set_appearance_mode("dark")
        self.root = ctk.CTk()
        self.root.title(
            "Drone Mission Control Center — UAV {} ({})".format(
                UAV_SOFTWARE_VERSION, SYSTEM_RELEASE_ID
            )
        )
        self.root.geometry("1500x900")
        self.root.minsize(1280, 760)
        self.root.configure(fg_color=COLOR_BG)


        topbar = ctk.CTkFrame(self.root, fg_color=COLOR_PANEL, corner_radius=0, height=60)
        topbar.pack(side="top", fill="x")
        topbar.pack_propagate(False)

        title = ctk.CTkLabel(
            topbar,
            text="DRONE MISSION CONTROL CENTER",
            font=FONT_TITLE,
            text_color=COLOR_TEXT,
        )
        title.pack(side="left", padx=24, pady=12)

        self.estop_btn = ctk.CTkButton(
            topbar,
            text="EMERGENCY STOP",
            font=("Segoe UI", 14, "bold"),
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_H,
            text_color="white",
            width=220,
            height=40,
            command=self._on_emergency_stop,
        )
        self.estop_btn.pack(side="right", padx=24, pady=10)


        body = ctk.CTkFrame(self.root, fg_color=COLOR_BG)
        body.pack(side="top", fill="both", expand=True, padx=8, pady=8)

        body.grid_columnconfigure(0, weight=0, minsize=280)
        body.grid_columnconfigure(1, weight=2)
        body.grid_columnconfigure(2, weight=1, minsize=380)
        body.grid_rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_center(body)
        self._build_right(body)

    def _build_left(self, parent):
        left = ctk.CTkScrollableFrame(
            parent, fg_color=COLOR_PANEL, corner_radius=8,
            scrollbar_button_color=COLOR_PANEL_HI,
            scrollbar_button_hover_color=COLOR_BORDER,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        hdr = ctk.CTkLabel(left, text="STACK STATUS", font=FONT_HDR, text_color=COLOR_TEXT)
        hdr.pack(anchor="w", padx=16, pady=(16, 8))

        self.stack_rows = {}
        for name, _cmd in STACK_NODES:
            row = ctk.CTkFrame(left, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=2)
            dot = ctk.CTkLabel(row, text="●", text_color=COLOR_DIM, font=("Arial", 16), width=20)
            dot.pack(side="left")
            lbl = ctk.CTkLabel(row, text=name, font=FONT_BODY, text_color=COLOR_TEXT, anchor="w")
            lbl.pack(side="left", padx=(6, 0))
            self.stack_rows[name] = dot


        ctk.CTkFrame(left, fg_color="transparent", height=12).pack()


        btn_sim = ctk.CTkButton(
            left, text="Start Sim (Lion Rock)", font=FONT_BODY,
            fg_color="#00897b", hover_color="#00695c",
            command=self._on_start_sim, height=36,
        )
        btn_sim.pack(fill="x", padx=16, pady=4)

        btn_start = ctk.CTkButton(
            left, text="Start All (Real Flight)", font=FONT_BODY,
            fg_color="#2e7d32", hover_color="#1b5e20",
            command=self._on_start_all, height=36,
        )
        btn_start.pack(fill="x", padx=16, pady=4)

        btn_stop = ctk.CTkButton(
            left, text="Stop All", font=FONT_BODY,
            fg_color="#455a64", hover_color="#37474f",
            command=self._on_stop_all, height=36,
        )
        btn_stop.pack(fill="x", padx=16, pady=4)

        btn_restart = ctk.CTkButton(
            left, text="Restart", font=FONT_BODY,
            fg_color="#455a64", hover_color="#37474f",
            command=self._on_restart, height=36,
        )
        btn_restart.pack(fill="x", padx=16, pady=4)

        ctk.CTkFrame(left, fg_color="transparent", height=12).pack()


        sep = ctk.CTkLabel(left, text="QUICK FLIGHT", font=FONT_HDR, text_color=COLOR_TEXT)
        sep.pack(anchor="w", padx=16, pady=(8, 4))

        btn_to = ctk.CTkButton(
            left, text="Takeoff", font=FONT_BODY,
            fg_color="#1976d2", hover_color="#0d47a1",
            command=self._on_takeoff, height=36,
        )
        btn_to.pack(fill="x", padx=16, pady=4)

        btn_land = ctk.CTkButton(
            left, text="Land", font=FONT_BODY,
            fg_color="#5d4037", hover_color="#3e2723",
            command=self._on_land, height=36,
        )
        btn_land.pack(fill="x", padx=16, pady=4)

        ctk.CTkButton(left, text="Release completed flight lock", font=FONT_BODY,
                      command=self._on_operator_reset, height=36).pack(fill="x", padx=16, pady=4)


        ctk.CTkFrame(left, fg_color="transparent", height=12).pack()
        cam_hdr = ctk.CTkLabel(left, text="CAMERA", font=FONT_HDR, text_color=COLOR_TEXT)
        cam_hdr.pack(anchor="w", padx=16, pady=(8, 4))

        self.rec_indicator = ctk.CTkLabel(
            left, text="[STANDBY]", font=("Segoe UI", 12, "bold"),
            text_color=COLOR_DIM,
        )
        self.rec_indicator.pack(anchor="w", padx=16)

        self.rec_timer_label = ctk.CTkLabel(
            left, text="", font=("Consolas", 10), text_color=COLOR_DIM,
        )
        self.rec_timer_label.pack(anchor="w", padx=16)

        rec_btns = ctk.CTkFrame(left, fg_color="transparent")
        rec_btns.pack(fill="x", padx=16, pady=4)


        self.btn_rec_toggle = ctk.CTkButton(
            rec_btns, text="REC", width=100, height=30,
            font=("Segoe UI", 11, "bold"),
            fg_color="#b71c1c", hover_color="#7f0000",
            command=self._on_rec_toggle,
        )
        self.btn_rec_toggle.pack(side="left", padx=(0, 4))

        self.btn_rec_stop = ctk.CTkButton(
            rec_btns, text="STOP", width=100, height=30,
            font=("Segoe UI", 11, "bold"),
            fg_color="#455a64", hover_color="#37474f",
            command=self._on_manual_rec_stop, state="disabled",
        )
        self.btn_rec_stop.pack(side="left")


        self.uptime_label = ctk.CTkLabel(
            left, text="Console uptime: 00:00:00",
            font=("Segoe UI", 11), text_color=COLOR_DIM,
        )
        self.uptime_label.pack(anchor="w", padx=16, pady=(16, 8))
        self._uptime_start = time.time()

    def _build_center(self, parent):
        center = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=8)
        center.grid(row=0, column=1, sticky="nsew", padx=4)

        hdr = ctk.CTkLabel(center, text="MISSION TACTICAL MAP", font=FONT_HDR, text_color=COLOR_TEXT)
        hdr.pack(anchor="w", padx=16, pady=(16, 8))


        canvas_holder = ctk.CTkFrame(center, fg_color=COLOR_BG, corner_radius=4)
        canvas_holder.pack(fill="both", expand=True, padx=16, pady=8)
        self.canvas = tk.Canvas(canvas_holder, bg="#06101a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self.canvas.bind("<Configure>", lambda e: self._draw_map())


        tele = ctk.CTkFrame(center, fg_color="transparent")
        tele.pack(fill="x", padx=16, pady=8)

        self.status_label = ctk.CTkLabel(
            tele, text="Status: OFFLINE", font=FONT_HDR, text_color=COLOR_WARN,
        )
        self.status_label.pack(side="left", padx=(0, 24))

        self.mission_label = ctk.CTkLabel(
            tele, text="Mission: —", font=FONT_BODY, text_color=COLOR_DIM,
        )
        self.mission_label.pack(side="left", padx=(0, 24))

        self.wp_label = ctk.CTkLabel(
            tele, text="WP: 0/0", font=FONT_BODY, text_color=COLOR_DIM,
        )
        self.wp_label.pack(side="left", padx=(0, 24))

        self.gps_label = ctk.CTkLabel(
            tele, text="GPS: —", font=FONT_BODY, text_color=COLOR_DIM,
        )
        self.gps_label.pack(side="left")

    def _build_right(self, parent):
        right = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=8)
        right.grid(row=0, column=2, sticky="nsew", padx=(4, 0))

        hdr = ctk.CTkLabel(right, text="LIVE LOGS", font=FONT_HDR, text_color=COLOR_TEXT)
        hdr.pack(anchor="w", padx=16, pady=(16, 8))


        self.log_text = ctk.CTkTextbox(
            right, font=FONT_MONO, fg_color="#06101a",
            text_color=COLOR_TEXT, wrap="none", height=600,
        )
        self.log_text.pack(fill="both", expand=True, padx=16, pady=8)
        self.log_text.configure(state="disabled")


        filt = ctk.CTkFrame(right, fg_color="transparent")
        filt.pack(fill="x", padx=16, pady=(0, 12))

        self.autoscroll = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            filt, text="Auto-scroll", font=FONT_BODY,
            variable=self.autoscroll, text_color=COLOR_TEXT,
        ).pack(side="left")

        ctk.CTkButton(
            filt, text="Clear", width=70, height=28,
            fg_color="#455a64", hover_color="#37474f",
            command=self._clear_log,
        ).pack(side="right")


    def _init_mqtt(self):
        if _PAHO_V2:
            self.mqtt_client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION1,
                client_id="drone_console_" + str(int(time.time())),
            )
        else:
            self.mqtt_client = mqtt.Client(
                client_id="drone_console_" + str(int(time.time())),
            )
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
        self.mqtt_client.on_message = self._on_mqtt_message
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

        try:
            self.mqtt_client.connect(self.cfg["mqtt_broker"], int(self.cfg["mqtt_port"]), keepalive=60)
            self.mqtt_client.loop_start()
            self._log("mqtt", "connecting to {}:{}".format(self.cfg["mqtt_broker"], self.cfg["mqtt_port"]))
        except Exception as e:
            self._log("mqtt", "connect failed: {}".format(e), level="error")

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self._log("mqtt", "connect rc={}".format(rc), level="error")
            return
        self._log("mqtt", "connected")
        for t in (self.cfg["topic_status"],
                  self.cfg["topic_target_multi"],
                  "alin1/mission/target_gps"):
            client.subscribe(t, qos=1)
            self._log("mqtt", "subscribed " + t)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        if rc != 0:
            self._log("mqtt", "disconnect rc={}".format(rc), level="warn")

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode()
        except Exception:
            return
        try:
            data = json.loads(payload)
        except Exception:
            self._log("mqtt", "{} -> non-json".format(msg.topic), level="warn")
            return

        if msg.topic == self.cfg["topic_status"]:
            self._on_status_update(data)
        elif msg.topic in (self.cfg["topic_target_multi"], "alin1/mission/target_gps"):
            self._on_dispatch_seen(msg.topic, data)

    def _on_status_update(self, data):
        if not isinstance(data, dict):
            return
        message_type = data.get('message_type')
        if message_type is not None and message_type != 'EXECUTION':
            self._log('transport', '{}: {}'.format(message_type, data.get('reason', data.get('status', ''))))
            return
        if message_type == 'EXECUTION':
            eid = data.get('execution_id')
            revision = data.get('state_revision')
            if not isinstance(eid, str) or not eid or type(revision) is not int or revision < 1:
                return
            if data.get('active_execution_id') == '' and eid != self.current_execution_id:
                self._log('status', 'Historical execution status: ' + eid)
                return
            seen = getattr(self, '_execution_revisions', {})
            if revision <= seen.get(eid, 0):
                return
            seen[eid] = revision
            self._execution_revisions = seen
        if data.get("active_execution_id") and data.get("execution_id") != data["active_execution_id"]:
            self._log("status", "Other request rejected: {}".format(data.get("reason", "")), level="warn")
            return
        if message_type == 'EXECUTION' and data.get('execution_id') != getattr(self, '_route_execution_id', None):
            if not self._load_verified_route(data):
                with self._lock:
                    self.queue_waypoints = []
                self.root.after(0, self._draw_map)
        with self._lock:
            prev_status = self.current_status
            self.current_status = data.get("status", "?")
            self.current_phase = data.get("phase", self.current_status)
            self.land_command_requested = bool(data.get("land_command_requested", False))


            self.touchdown_confirmed = bool(data.get("touchdown_confirmed", False))
            self.current_message = data.get("message", "")
            if data.get("mission_id"):
                self.current_mission_id = data["mission_id"]
            if data.get("execution_id"):
                self.current_execution_id = data["execution_id"]
            raw_index = data.get("waypoint_index")
            self.current_wp_index = (raw_index + (1 if data.get("schema_version") == 2 else 0)) if type(raw_index) is int and raw_index >= 0 else 0
            self.current_wp_total = int(data.get("waypoint_total", 0) or 0)
        self._log("status", "{} / {} ({}) wp={}/{}".format(
            self.current_status, self.current_phase, self.current_message,
            self.current_wp_index, self.current_wp_total))
        self.root.after(0, self._refresh_telemetry)


        status = self.current_status
        recorder_busy = self._recording or self._rec_starting or self._rec_stopping
        if status == "NAVIGATING" and prev_status != "NAVIGATING" and not recorder_busy:
            self._start_recording(trigger="MISSION_NAVIGATING")
        elif (
            status in ("LANDING", "ABORTED")
            or self.current_phase == "LAND_REQUESTED"
        ) and recorder_busy and matches_recording_status(
            self._rec_mission_id, self._rec_execution_id, data
        ):
            self._stop_recording(reason=self.current_phase or status)

    def _load_verified_route(self, data):
        import json
        import os
        import re
        import sys
        from pathlib import Path
        eid = data.get('execution_id')
        fingerprint = data.get('content_fingerprint')
        if (not isinstance(eid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', eid)
                or not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint)):
            return False
        journal = Path(self.cfg.get('execution_journal_path', '/home/mike/drone_system/data/mission_state/executions.json')).expanduser()
        source = Path(__file__).resolve().parents[2] / 'catkin_ws' / 'src' / 'rescue_bridge' / 'src'
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        try:
            from execution_protocol import normalize_execution_payload
            task_path = Path(str(journal) + '.transfers') / eid / 'task.json'
            if task_path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('saved task exceeds size limit')
            with task_path.open(encoding='utf-8') as handle:
                task = json.load(handle)
            mission = normalize_execution_payload(task)
            if (mission['execution_id'] != eid or mission['mission_id'] != data.get('mission_id')
                    or mission['content_fingerprint'] != fingerprint
                    or len(mission['waypoints']) != data.get('source_waypoint_total')):
                raise ValueError('saved task does not match accepted execution')
            with self._lock:
                self.queue_waypoints = list(mission['waypoints'])
                self._route_execution_id = eid
            self._log('dispatch', 'Verified task restored: {} ({} source waypoints)'.format(eid, len(mission['waypoints'])))
            self.root.after(0, self._draw_map)
            return True
        except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
            self._log('dispatch', 'Verified route unavailable: ' + str(exc), level='warn')
            return False

    def _on_dispatch_seen(self, topic, data):
        wps = []
        if "waypoints" in data and isinstance(data["waypoints"], list):
            for wp in data["waypoints"]:
                if isinstance(wp, dict):
                    lat = wp["latitude"] if "latitude" in wp else wp.get("lat")
                    lon = wp["longitude"] if "longitude" in wp else wp.get("lon")
                    if lat is not None and lon is not None:
                        try:
                            wps.append((float(lat), float(lon)))
                        except Exception:
                            pass
        else:
            lat = data.get("latitude")
            lon = data.get("longitude")
            if lat is not None and lon is not None:
                try:
                    wps.append((float(lat), float(lon)))
                except Exception:
                    pass

        with self._lock:
            self.queue_waypoints = wps
            if wps and self.takeoff_gps is None:
                self.takeoff_gps = wps[0]

        self._log("dispatch", "{} -> {} waypoints".format(topic.split('/')[-1], len(wps)))
        self.root.after(0, self._draw_map)


    def _start_pollers(self):
        threading.Thread(target=self._stack_poller, daemon=True).start()
        self._tick_uptime()

    def _stack_poller(self):
        while True:
            try:
                for name, cmd in STACK_NODES:
                    self.stack_status[name] = _check_node(cmd)
                self.root.after(0, self._refresh_stack_dots)
            except Exception:
                pass
            time.sleep(2.0)

    def _refresh_stack_dots(self):
        for name, dot in self.stack_rows.items():
            st = self.stack_status.get(name, "down")
            color = COLOR_OK if st == "ok" else COLOR_BAD
            dot.configure(text_color=color)

    def _refresh_telemetry(self):

        s = self.current_status
        if s in ("NAVIGATING", "ARRIVED"):
            color = COLOR_OK
        elif s in ("HOVERING", "ONLINE"):
            color = COLOR_OK
        elif s in ("ABORTED", "REJECTED", "OFFLINE"):
            color = COLOR_BAD
        elif s == "LANDING":
            color = COLOR_WARN
        else:
            color = COLOR_DIM
        status_text = "Status: {}".format(s)
        if self.current_phase and self.current_phase != s:
            status_text += " / {}".format(self.current_phase)
        if self.current_phase == "LAND_REQUESTED":
            status_text += " (touchdown not confirmed)"
        self.status_label.configure(text=status_text, text_color=color)
        self.mission_label.configure(text="Mission: " + (self.current_mission_id or "—"))
        self.wp_label.configure(text="WP: {}/{}".format(self.current_wp_index, self.current_wp_total))
        if self.drone_gps:
            self.gps_label.configure(text="GPS: {:.5f}, {:.5f}".format(*self.drone_gps))

    def _tick_uptime(self):
        secs = int(time.time() - self._uptime_start)
        self.uptime_label.configure(text="Console uptime: {:02d}:{:02d}:{:02d}".format(
            secs // 3600, (secs % 3600) // 60, secs % 60))
        self.root.after(1000, self._tick_uptime)


    def _draw_map(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 50:
            return


        for x in range(0, w, 40):
            c.create_line(x, 0, x, h, fill="#10202c", width=1)
        for y in range(0, h, 40):
            c.create_line(0, y, w, y, fill="#10202c", width=1)

        c.create_text(8, 8, anchor="nw", text="TACTICAL MAP — VINS local + GPS overlay",
                      fill=COLOR_DIM, font=("Consolas", 9))

        with self._lock:
            wps = list(self.queue_waypoints)
            drone = self.drone_gps
            cur_idx = self.current_wp_index

        if not wps:
            c.create_text(w // 2, h // 2,
                          text="(no active mission)\nWaiting for dispatch...",
                          fill=COLOR_DIM, font=("Segoe UI", 14), justify="center")
            return


        lats = [p[0] for p in wps]
        lons = [p[1] for p in wps]
        if drone:
            lats.append(drone[0]); lons.append(drone[1])

        lat_min, lat_max = min(lats), max(lats)
        lon_min, lon_max = min(lons), max(lons)

        lat_pad = max(0.0001, (lat_max - lat_min) * 0.2)
        lon_pad = max(0.0001, (lon_max - lon_min) * 0.2)
        lat_min -= lat_pad; lat_max += lat_pad
        lon_min -= lon_pad; lon_max += lon_pad

        def project(lat, lon):
            x = (lon - lon_min) / max(1e-9, lon_max - lon_min) * (w - 60) + 30
            y = (lat_max - lat) / max(1e-9, lat_max - lat_min) * (h - 60) + 30
            return x, y


        source_total = len(wps)
        preview_indices = list(range(source_total)) if source_total <= 1000 else sorted(
            {round(index * (source_total - 1) / 999) for index in range(1000)}
            | ({cur_idx - 1} if 1 <= cur_idx <= source_total else set()))
        label_indices = set(preview_indices) if source_total <= 30 else {
            preview_indices[round(index * (len(preview_indices) - 1) / 29)] for index in range(30)}
        if 1 <= cur_idx <= source_total:
            label_indices.add(cur_idx - 1)
        c.create_text(8, 28, anchor='nw', text='Route preview: {} of {} source waypoints; full route retained'.format(
            len(preview_indices), source_total), fill=COLOR_DIM, font=('Consolas', 9))
        for first_index, last_index in zip(preview_indices, preview_indices[1:]):
            x1, y1 = project(*wps[first_index])
            x2, y2 = project(*wps[last_index])
            c.create_line(x1, y1, x2, y2, fill="#3a4a6b", width=2, dash=(4, 4))


        for i in preview_indices:
            lat, lon = wps[i]
            x, y = project(lat, lon)
            wp_num = i + 1
            if wp_num < cur_idx:
                fill = "#2e7d32"
            elif wp_num == cur_idx:
                fill = COLOR_WARN
            else:
                fill = "#1565c0"
            r = 14 if wp_num == cur_idx else 10
            c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=COLOR_TEXT, width=2)
            if i in label_indices:
                c.create_text(x, y, text=str(wp_num), fill="white", font=("Consolas", 10, "bold"))
                c.create_text(x, y + r + 12,
                              text="{:.5f},{:.5f}".format(lat, lon),
                              fill=COLOR_DIM, font=("Consolas", 8))


        if drone:
            dx, dy = project(*drone)
            c.create_polygon(
                dx, dy - 12, dx - 10, dy + 8, dx + 10, dy + 8,
                fill=COLOR_ACCENT, outline=COLOR_TEXT, width=2,
            )
            c.create_text(dx, dy + 22, text="DRONE", fill=COLOR_ACCENT,
                          font=("Consolas", 9, "bold"))


    def _log(self, source, message, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = "[{}] {:9s} {}".format(ts, source, message)
        with self._lock:
            self._log_buf.append(line)

        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        try:
            self.root.after(0, self._append_log_line, line)
        except Exception:
            pass

    def _append_log_line(self, line):
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            if self.autoscroll.get():
                self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _clear_log(self):
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass


    def _run_script(self, label, script_path, *extra_args, confirm=None):


        if confirm and not messagebox.askyesno(confirm[0], confirm[1]):
            return
        self._log(label, "STARTED " + os.path.basename(script_path))
        try:
            proc = subprocess.Popen(
                [script_path, *extra_args],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True,
            )
        except FileNotFoundError as e:
            self._log(label, "spawn FAIL: " + str(e), level="error")
            return
        threading.Thread(
            target=self._stream_proc, args=(proc, label), daemon=True
        ).start()

    def _stream_proc(self, proc, label):
        try:
            for raw in iter(proc.stdout.readline, ""):
                line = raw.rstrip()
                if line:
                    self._log(label, line)
            proc.stdout.close()
        except Exception as e:
            self._log(label, "stream err: " + str(e), level="error")
        rc = proc.wait()
        self._log(label, "EXITED rc={}".format(rc))

    def _on_start_sim(self):
        self._run_script(
            "sim", "/home/mike/drone_system/bin/start_sim.sh",
            confirm=("Start Sim", "Launch Lion Rock simulation (RViz + EGO-Planner)?"),
        )

    def _on_start_all(self):
        self._run_script(
            "start", "/home/mike/drone_system/bin/start_stack.sh",
            confirm=("Start Real Flight", "Launch the REAL hardware stack (realsense + mavros + VINS)?\nMake sure the drone is powered and connected."),
        )

    def _on_stop_all(self):
        self._run_script(
            "stop", "/home/mike/drone_system/bin/stop_stack.sh",
            confirm=("Stop All", "Stop the entire ROS stack?"),
        )

    def _on_restart(self):
        if not messagebox.askyesno("Restart", "Stop and re-start the ROS stack?"):
            return
        self._log("ctl", "Restart triggered")
        try:
            proc = subprocess.Popen(
                ["bash", "-c",
                 "/home/mike/drone_system/bin/stop_stack.sh; sleep 3; "
                 "/home/mike/drone_system/bin/start_stack.sh"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True,
            )
            threading.Thread(target=self._stream_proc, args=(proc, "restart"), daemon=True).start()
        except Exception as e:
            self._log("restart", "FAIL: " + str(e), level="error")

    def _on_takeoff(self):
        self._run_script(
            "takeoff", "/home/mike/drone_system/bin/takeoff.sh",
            confirm=("Takeoff", "Send TAKEOFF command?"),
        )

    def _on_land(self):
        self._run_script(
            "land", "/home/mike/drone_system/bin/land.sh",
            confirm=("Land", "Send LAND command?"),
        )

    def _on_emergency_stop(self):
        if not messagebox.askyesno(
            "EMERGENCY STOP", "Land the drone IMMEDIATELY and clear the queue?"
        ):
            return
        self._log("estop", "EMERGENCY STOP triggered", level="error")

        if self._recording or self._rec_starting:
            self._stop_recording(reason="EMERGENCY_ABORT")

        try:
            self.mqtt_client.publish(
                self.cfg["topic_abort"],
                json.dumps({"reason": "console emergency stop", "execution_id": self.current_execution_id}),
                qos=1,
            )
            self._log("estop", "MQTT abort published")
        except Exception as e:
            self._log("estop", "MQTT abort FAIL: " + str(e), level="error")

        self._run_script("estop", "/home/mike/drone_system/bin/emergency_stop.sh")

    def _on_operator_reset(self):
        if not self.current_execution_id:
            messagebox.showwarning("Release flight lock", "No execution is selected.")
            return
        if not messagebox.askyesno(
            "Release flight lock",
            "Have you checked that the aircraft has landed and is ready for a new mission? "
            "This records your confirmation; it is not automatic touchdown detection.",
        ):
            return
        request = {"execution_id": self.current_execution_id, "operator_confirmed": True,
                   "reason": "Operator inspected aircraft: landed and ready for a new mission"}
        self.mqtt_client.publish(self.cfg.get("topic_operator_reset", "alin1/mission/operator_reset"),
                                 json.dumps(request), qos=1)
        self._log("reset", "Release requested; awaiting execution controller confirmation")


    def _journal_transition(self, status, reason="", details=None):
        if not self._rec_journal:
            return None
        try:
            return self._rec_journal.transition(
                status, reason=reason, details=details or {}
            )
        except RecordingStateError as e:
            self._log("camera", "journal transition ignored: {}".format(e), level="warn")
            return None

    def _start_recording(self, trigger="MANUAL"):
        if self._recording or self._rec_starting or self._rec_stopping:
            return
        os.makedirs(VIDEO_DIR, exist_ok=True)
        os.makedirs(VIDEO_MANIFEST_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        mid = self.current_mission_id or "manual"
        mid = re.sub(r"[^A-Za-z0-9._-]+", "_", mid).strip("._-") or "manual"
        fname = "flight_{}_{}.avi".format(mid, ts)
        fpath = os.path.join(VIDEO_DIR, fname)
        session_id = "recording_{}_{}".format(mid, ts)
        manifest_path = os.path.join(VIDEO_MANIFEST_DIR, session_id + ".json")

        self._rec_paused = False
        self._rec_starting = True
        self._rec_stopping = False
        self._rec_filename = fpath
        self._rec_mission_id = self.current_mission_id
        self._rec_execution_id = self.current_execution_id
        self._rec_start_time = time.time()
        self._rec_elapsed_at_pause = 0
        self._rec_journal = RecordingJournal(manifest_path)
        try:
            self._rec_journal.start(
                session_id,
                self.current_mission_id or "manual",
                fname,
                trigger,
                execution_id=self._rec_execution_id,
            )
        except Exception as e:
            self._rec_starting = False
            self._rec_journal = None
            self._log("camera", "journal start FAIL: {}".format(e), level="error")
            return

        try:
            env = os.environ.copy()
            env.pop("DISPLAY", None)
            self._rec_proc = subprocess.Popen(
                [sys.executable, CAM_RECORDER, str(CAMERA_DEV), fpath],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
            )
        except Exception as e:
            self._journal_transition(
                "FAILED", reason="recorder process spawn failed",
                details={"error_code": type(e).__name__},
            )
            self._rec_starting = False
            self._log("camera", "spawn FAIL: {}".format(e), level="error")
            self.root.after(0, self._update_rec_ui)
            return

        threading.Thread(target=self._rec_stdout_reader,
                         args=(self._rec_proc, self._rec_journal, fpath), daemon=True).start()
        self._log("camera", "REC START REQUESTED → {}".format(fname))
        self.root.after(0, self._update_rec_ui)

    def _rec_stdout_reader(self, proc=None, journal=None, output_file=None):

        proc = proc or self._rec_proc
        journal = journal or self._rec_journal
        output_file = output_file or getattr(self, "_rec_filename", None)
        pending_done = None
        if not proc:
            return

        def transition(status, reason="", details=None):
            if journal:
                try:
                    journal.transition(status, reason=reason, details=details or {})
                except RecordingStateError as exc:
                    self._log("camera", "journal transition ignored: {}".format(exc), level="warn")

        try:
            for raw in iter(proc.stdout.readline, ""):
                line = raw.strip()
                if not line.startswith(RECORDER_EVENT_PREFIX):
                    if line:
                        self._log("camera", line)
                    continue
                try:
                    event_data = json.loads(line[len(RECORDER_EVENT_PREFIX):])
                    if not isinstance(event_data, dict):
                        continue
                except (ValueError, TypeError):
                    self._log("camera", "malformed recorder event", level="warn")
                    continue
                event = event_data.get("event", "")
                details = {key: event_data[key] for key in (
                    "width", "height", "fps", "frames_written", "opencv_version",
                    "error_code", "incomplete_file",
                ) if key in event_data}
                current = self._rec_proc is proc
                if event == "READY":
                    if not (current and self._rec_stopping):
                        transition("RECORDING", details=details)
                    if current:
                        self._rec_starting = False
                        self._recording = not self._rec_stopping
                elif event == "PAUSED":
                    transition("PAUSED", reason="operator pause")
                    if current:
                        self._rec_paused = True
                elif event == "RESUMED":
                    transition("RECORDING", reason="operator resume")
                    if current:
                        self._rec_paused = False
                        self._rec_start_time = time.time()
                elif event in ("DONE", "ERROR"):
                    if event == "DONE":
                        pending_done = details
                    else:
                        transition("FAILED", reason="recorder error", details=details)

                    if current:
                        self._recording = False
                        self._rec_starting = False
                        self._rec_stopping = True
                        self._rec_paused = False
                self._log("camera", "recorder {} {}".format(event, details))
                if current:
                    self.root.after(0, self._update_rec_ui)
            proc.stdout.close()
        except Exception as exc:
            self._log("camera", "recorder event stream failed: {}".format(exc), level="error")
        rc = proc.wait()
        if journal:
            try:
                terminal = journal.load().get("status")
            except Exception:
                terminal = ""
            if terminal not in ("COMPLETED", "FAILED"):
                error = recording_result_error(output_file, (pending_done or {}).get("frames_written"), rc)
                if pending_done is not None and not error:
                    transition("COMPLETED", details=pending_done)
                else:
                    transition("FAILED", reason="recording completion could not be verified",
                               details={"error_code": error or "TERMINAL_EVENT_MISSING"})
        if self._rec_proc is proc:
            self._rec_proc = None
            self._recording = False
            self._rec_starting = False
            self._rec_stopping = False
            self._rec_paused = False
            self.root.after(0, self._update_rec_ui)

    def _rec_send(self, cmd):

        try:
            if self._rec_proc and self._rec_proc.stdin:
                self._rec_proc.stdin.write(cmd + "\n")
                self._rec_proc.stdin.flush()
                return True
        except Exception as e:
            self._log("camera", "send '{}' failed: {}".format(cmd, e), level="error")
        return False

    def _pause_recording(self):
        if not self._recording or self._rec_paused:
            return
        self._rec_elapsed_at_pause += time.time() - self._rec_start_time
        if self._rec_send("pause"):
            self._log("camera", "REC PAUSE REQUESTED")

    def _resume_recording(self):
        if not self._recording or not self._rec_paused:
            return
        if self._rec_send("resume"):
            self._log("camera", "REC RESUME REQUESTED")

    def _stop_recording(self, reason="MANUAL_STOP"):
        if not (self._recording or self._rec_starting) or self._rec_stopping:
            return
        self._rec_stopping = True
        self._journal_transition("STOP_REQUESTED", reason=reason)
        sent = self._rec_send("stop")
        self._recording = False
        self._rec_starting = False
        self._rec_paused = False
        if sent:
            self._log("camera", "REC STOP REQUESTED → {}".format(
                os.path.basename(self._rec_filename)))
        else:
            self._journal_transition(
                "FAILED", reason="stop command failed",
                details={"error_code": "STOP_COMMAND_FAILED"},
            )
            self._rec_stopping = False
        self.root.after(0, self._update_rec_ui)

    def _update_rec_ui(self):
        if self._rec_starting:
            self.rec_indicator.configure(text="[STARTING]", text_color=COLOR_WARN)
            self.btn_rec_toggle.configure(state="disabled")
            self.btn_rec_stop.configure(state="normal")
        elif self._rec_stopping:
            self.rec_indicator.configure(text="[STOPPING]", text_color=COLOR_WARN)
            self.btn_rec_toggle.configure(state="disabled")
            self.btn_rec_stop.configure(state="disabled")
        elif self._recording and not self._rec_paused:

            self.rec_indicator.configure(text="[REC]", text_color=COLOR_BAD)
            self.btn_rec_toggle.configure(
                text="PAUSE", fg_color="#e65100", hover_color="#bf360c", state="normal",
            )
            self.btn_rec_stop.configure(state="normal")
            self._tick_rec_timer()
        elif self._recording and self._rec_paused:

            self.rec_indicator.configure(text="[PAUSED]", text_color=COLOR_WARN)
            self.btn_rec_toggle.configure(
                text="RESUME", fg_color="#2e7d32", hover_color="#1b5e20", state="normal",
            )
            self.btn_rec_stop.configure(state="normal")
        else:

            self.rec_indicator.configure(text="[STANDBY]", text_color=COLOR_DIM)
            self.rec_timer_label.configure(text="")
            self.btn_rec_toggle.configure(
                text="REC", fg_color="#b71c1c", hover_color="#7f0000", state="normal",
            )
            self.btn_rec_stop.configure(state="disabled")

    def _tick_rec_timer(self):
        if not self._recording or self._rec_paused:
            return
        elapsed = int(self._rec_elapsed_at_pause + time.time() - self._rec_start_time)
        self.rec_timer_label.configure(
            text="REC {:02d}:{:02d}  {}".format(
                elapsed // 60, elapsed % 60,
                os.path.basename(self._rec_filename)),
            text_color=COLOR_BAD,
        )
        self.root.after(1000, self._tick_rec_timer)

    def _on_rec_toggle(self):
        if not self._recording:
            self._start_recording(trigger="MANUAL")
        elif not self._rec_paused:
            self._pause_recording()
        else:
            self._resume_recording()

    def _on_manual_rec_stop(self):
        if self._recording or self._rec_starting:
            self._stop_recording(reason="MANUAL_STOP")


    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):

        if self._recording or self._rec_starting:
            self._stop_recording(reason="CONSOLE_CLOSE")
        if self._rec_proc:
            try:
                self._rec_proc.terminate()
            except Exception:
                pass
        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        except Exception:
            pass
        self.root.destroy()


def main():

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    app = DroneConsole()
    app.run()


if __name__ == "__main__":
    main()
