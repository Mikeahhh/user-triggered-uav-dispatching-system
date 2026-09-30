import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import firebase_admin
from firebase_admin import credentials, db
from tkintermapview import TkinterMapView
import json
import time
import math
import paho.mqtt.client as paho_mqtt
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
import sys
import os
import re
import threading
import hashlib
import uuid

from app_metadata import APP_VERSION, SYSTEM_RELEASE_ID
from ground_station_demo import (
    DEMO_BANNER,
    DEMO_BROKER_LABEL,
    DEMO_DATA_NOTICE,
    DEMO_DATABASE_STATUS,
    DEMO_LOCATION_LABEL,
    DEMO_NOW_MS,
    DEMO_RAW_RECORDS_NOTICE,
    DEMO_TEST_PARAMETER_NOTICE,
    build_demo_drone_status,
    build_demo_onboard_summary,
    build_demo_ordered_rescue_events,
    build_demo_pending_alerts,
    build_demo_users_data,
)
from rescue_record_protocol import (
    ACK_PUBLISHED,
    RECEIVED_STORED,
    AckPublishError,
    GroundRescueStore,
    MqttConfigurationError,
    RescueRecordError,
    RescueRecordPersistenceError,
    configure_mqtt_client_auth,
    format_record_summary,
    process_onboard_envelope,
    publish_ground_ack,
)
from rescue_event_manager import (
    BOOKED_EVENTS,
    CONTACT_EMERGENCY,
    CONTACT_USER,
    EVENT_BOOKING_TIMEOUT,
    NOT_AVAILABLE,
    PENDING,
    QUICK_START_INACTIVITY,
    QUICK_START_LOCATION_TIMEOUT,
    QUICK_START_SESSIONS,
    RESCUE_REQUESTS,
    RESOLUTION,
    SAFE_CONFIRMED,
    SAFETY_UNCONFIRMED,
    SOS,
    RescueEventError,
    convert_alert_to_rescue_event,
    expected_end_at_ms,
    record_emergency_contact,
    record_user_contact,
    scan_user_records,
    require_sos_verification,
    session_gps_points,
    valid_gps_points,
)
from priority_scheduler import (
    SchedulerValidationError,
    order_pending_events,
    schedule_metrics,
)
from rescue_repository import (user_transaction, transition_alert, reconcile_user,
                               apply_user_reconciliation)
from firebase_runtime_config import load_firebase_runtime_config
from dispatch_journal import DispatchJournal
from quick_start_observations import ObservationStore
from mission_execution_protocol import task_fingerprint
from mission_transfer_protocol import messages as transfer_messages, query as transfer_query, canonical as transfer_canonical
from receiver_ready_protocol import ReceiverReadyGate
import active_event_queue as active_queue


ctk.set_appearance_mode("dark")


try:
    from sos_pattern import generate_spiral, generate_grid
    _SOS_PATTERN_OK = True
except Exception as _sos_imp_err:
    _SOS_PATTERN_OK = False
    print(f"[WARN] sos_pattern import failed: {_sos_imp_err}")


try:
    from paho.mqtt.client import CallbackAPIVersion
    _PAHO_V2 = True
except ImportError:
    _PAHO_V2 = False


def get_resource_path(relative_path):

    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):

        base_path = Path(sys._MEIPASS)
    else:

        base_path = Path(__file__).resolve().parent

    return str(base_path / relative_path)


APP_CONFIG_DIR_NAME = "MASS26GroundStation"
FIREBASE_CREDENTIAL_FILENAME = "serviceAccountKey.json"
FIREBASE_DATABASE_URL = load_firebase_runtime_config()["database_url"]
FIREBASE_APP_NAME = "mass26-ground-station"
FIREBASE_HTTP_TIMEOUT_SECONDS = 10
INVALID_FIREBASE_KEY_CHARS = re.compile(r'[.#$\[\]/\x00-\x1f\x7f]')
DEMO_SCREENSHOT_MODE = "--demo-screenshot" in sys.argv


def get_firebase_credential_path():

    configured_path = os.environ.get("GS_FIREBASE_CREDENTIALS", "").strip()
    candidates = []
    if configured_path:
        explicit = Path(configured_path).expanduser()
        if not explicit.is_file():
            raise FileNotFoundError("GS_FIREBASE_CREDENTIALS does not name an existing file")
        return str(explicit)

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(
            Path(local_app_data) / APP_CONFIG_DIR_NAME / FIREBASE_CREDENTIAL_FILENAME
        )


    if not getattr(sys, "frozen", False):
        candidates.append(Path(__file__).resolve().parent / FIREBASE_CREDENTIAL_FILENAME)

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(
        "Firebase credential is not configured. "
        "Set GS_FIREBASE_CREDENTIALS or place the key in the application config directory."
    )


MQTT_BROKER = os.environ.get("MQTT_BROKER", "").strip()
try:
    MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
except ValueError:
    MQTT_PORT = 0
MQTT_TOPIC_TARGET = "alin1/mission/target_gps"
MQTT_TOPIC_MULTI  = "alin1/mission/multi_waypoint"
MQTT_TOPIC_TRANSFER = "alin1/mission/transfer"
MQTT_TOPIC_OPERATOR_RETRY = "alin1/mission/operator_retry"
MQTT_TOPIC_STATUS = "alin1/mission/status"
MQTT_TOPIC_ABORT  = "alin1/mission/abort"
MQTT_TOPIC_ONBOARD_RECORD = "alin1/rescue/onboard_record"
MQTT_TOPIC_GROUND_ACK = "alin1/rescue/ground_ack"
MQTT_TOPIC_RESCUE_SYNC = "alin1/rescue/sync_request"
MQTT_TOPIC_RECEIVER_READY = "alin1/rescue/receiver_ready"
receiver_ready_gate = ReceiverReadyGate()
mqtt_connection_generation = 0


DEFAULT_FLIGHT_ALTITUDE = 5.0
DEFAULT_HOVER_SECONDS   = 5.0


RESCUE_CONFIG_ENV_NAMES = (
    "GS_T_LOCATION_UPDATE_SECONDS",
    "GS_T_WAIT_SECONDS",
)
MISSION_PUBLISH_TIMEOUT_SECONDS = 5.0
AUTO_REFRESH_INTERVAL_MS = 30_000
MAX_MISSION_WAYPOINTS = 100000
MISSION_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MISSION_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
DISPATCH_CANCELLED = "CANCELLED"
DISPATCH_INVALID_PARAMETERS = "INVALID_PARAMETERS"
DISPATCH_MQTT_UNAVAILABLE = "MQTT_UNAVAILABLE"
DISPATCH_PUBLISH_FAILED = "PUBLISH_FAILED"
DISPATCH_PUBLISHED = "PUBLISHED"
DISPATCH_UNKNOWN = "UNKNOWN"


def _configured_positive_number(environ, name):
    raw = environ.get(name, "")
    raw = raw.strip() if isinstance(raw, str) else ""
    if not raw:
        return None, f"{name} is required"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be a positive finite number"
    if not math.isfinite(value) or value <= 0:
        return None, f"{name} must be a positive finite number"
    return value, None


def _seconds_to_positive_milliseconds(value, name):
    if value is None:
        return None, None
    milliseconds = value * 1000.0
    if not math.isfinite(milliseconds):
        return None, f"{name} is too large to convert to milliseconds"
    converted = int(round(milliseconds))
    if converted <= 0:
        return None, f"{name} must resolve to at least one millisecond"
    return converted, None


def load_rescue_runtime_config(environ=None):

    source = os.environ if environ is None else environ
    errors = []
    timeout_seconds, error = _configured_positive_number(
        source, "GS_T_LOCATION_UPDATE_SECONDS"
    )
    if error:
        errors.append(error)
    wait_seconds, error = _configured_positive_number(source, "GS_T_WAIT_SECONDS")
    if error:
        errors.append(error)
    timeout_ms, error = _seconds_to_positive_milliseconds(
        timeout_seconds, "GS_T_LOCATION_UPDATE_SECONDS"
    )
    if error:
        errors.append(error)
    wait_ms, error = _seconds_to_positive_milliseconds(
        wait_seconds, "GS_T_WAIT_SECONDS"
    )
    if error:
        errors.append(error)
    timezone_name = source.get("GS_EVENT_TIMEZONE", "")
    timezone_name = timezone_name.strip() if isinstance(timezone_name, str) else ""
    return {
        "quick_start_timeout_ms": timeout_ms,
        "ignored_legacy_settings": [name for name in ("GS_D_MOVE_METERS", "GS_T_INACTIVE_SECONDS")
                                    if source.get(name)],
        "wait_threshold_ms": wait_ms,
        "event_timezone": timezone_name or None,
        "errors": errors,
        "ready": not errors,
    }


rescue_runtime_config = load_rescue_runtime_config()


LANGUAGES = {
    "zh": {
        "title": f"救援無人機地面站 - v{APP_VERSION}",
        "left_title": "使用者與任務",
        "drone_title": "無人機任務狀態",
        "drone_text": "等待任務...",
        "onboard_rescue_title": "機載救援資料",
        "onboard_rescue_waiting": "等待 UAV 儲存並回傳資料...",
        "mqtt_title": "MQTT 連線狀態",
        "mqtt_text": "未連線",
        "mqtt_connect_btn": "連接 MQTT",
        "mqtt_disconnect_btn": "斷開 MQTT",
        "fb_title": "資料庫連線狀態",
        "fb_connected": "已連線",
        "reload_btn": "重新載入資料",
        "lang_btn": "切換英文",
        "logout_btn": "登出",
        "sessions_title": "QuickStartSessions",
        "events_title": "Booked Events",
        "rescue_requests_title": "救援請求",
        "pending_alerts_title": "待處理警報",
        "pending_events_title": "待處理救援事件",
        "accepted_execution": "無人機已接受的任務：",
        "retry_rejected_waypoint": "重試被拒絕的航點",
        "retry_waypoint_title": "確認重試航點",
        "retry_waypoint_confirm": "在同一項已接受的任務中，重試被拒絕的航點？",
        "awaiting_execution_status": "等待任務執行狀態",
        "raw_records_title": "原始記錄（僅供查看）",
        "configuration_required": "需先配置救援閾值；目前不排序、不派遣",
        "no_pending_alerts": "目前沒有待處理警報",
        "no_pending_events": "目前沒有待處理救援事件",
        "user_safe_btn": "使用者安全",
        "user_unconfirmed_btn": "無法確認使用者安全",
        "emergency_safe_btn": "已確認安全",
        "still_unconfirmed_btn": "仍無法確認",
        "emergency_not_available_btn": "記錄為 NOT_AVAILABLE",
        "search_confirm_btn": "確認開展搜索並建立事件",
        "search_confirm_title": "確認開展搜索",
        "search_confirm_text": "請確認已完成聯繫核實，並決定開展搜索。",
        "prepare_mission_btn": "準備任務",
        "preview_only": "僅預覽，不會派遣",
        "dispatch_db_warning": "MQTT 已發布，但 Firebase 狀態寫入失敗。請勿重複派遣；系統將只重試狀態寫入。",
        "no_data": "暫無資料，或載入失敗",
        "login_title": "登入",
        "identity": "身份",
        "user": "使用者",
        "admin": "管理員",
        "username": "使用者名稱",
        "password": "密碼",
        "login_btn": "登入",
        "login_error": "帳號或密碼錯誤",
        "login_connecting": "正在連接資料庫...",
        "login_db_error": "資料庫暫時無法使用，請稍後再試",
        "login_invalid_username": "使用者名稱包含無效字元",
        "add_user": "新增使用者",
        "delete_user": "刪除使用者",
        "user_list": "使用者列表",
        "new_username": "新使用者名稱",
        "new_password": "新密碼",
        "confirm_delete": "確定要刪除使用者 {} 嗎？",
        "admin_panel_title": "管理員面板 - Ground Station 使用者",
        "manage_users": "管理 Ground Station 使用者",
        "start_date": "開始日期",
        "start_time": "開始時間",
        "end_date": "結束日期",
        "end_time": "結束時間",
        "status": "狀態",
        "event_title": "活動名稱",
        "event_date": "活動日期",
        "dispatch_btn": "派遣無人機",
        "dispatch_confirm": "確定派遣無人機到此位置？\nLat: {}\nLon: {}",
        "dispatch_success": "任務已派遣！\n目標: ({}, {})",
        "mqtt_not_connected": "MQTT 未連線，無法派遣無人機",
        "sos_title": "SOS 救援搜索模式",
        "sos_subtitle": "最後已知位置 + 半徑 → 自動生成搜索航點",
        "sos_lat_label": "緯度:",
        "sos_lon_label": "經度:",
        "sos_radius_label": "半徑 (米):",
        "sos_status_idle": "模式: 待命",
        "sos_status_fmt": "模式: {} ({} 個航點)",
        "sos_spiral_btn": "預覽螺旋搜索路線",
        "sos_grid_btn": "預覽網格搜索路線",
        "sos_module_err": "sos_pattern 模組未載入",
        "sos_invalid_input": "緯度、經度或半徑格式錯誤",
        "sos_generation_err": "搜索路徑生成失敗",
    },
    "en": {
        "title": f"Rescue Drone Ground Station - v{APP_VERSION}",
        "left_title": "Users & Missions",
        "drone_title": "Drone Mission Status",
        "drone_text": "Awaiting mission...",
        "onboard_rescue_title": "Onboard Rescue Record",
        "onboard_rescue_waiting": "Waiting for UAV-stored rescue data...",
        "mqtt_title": "MQTT Connection",
        "mqtt_text": "Disconnected",
        "mqtt_connect_btn": "Connect MQTT",
        "mqtt_disconnect_btn": "Disconnect MQTT",
        "fb_title": "Database Connection Status",
        "fb_connected": "Connected",
        "reload_btn": "Reload Data",
        "lang_btn": "Switch to Chinese",
        "logout_btn": "Logout",
        "sessions_title": "QuickStart Sessions",
        "events_title": "Booked Events",
        "rescue_requests_title": "Rescue Requests",
        "pending_alerts_title": "Pending Alerts",
        "pending_events_title": "Pending Rescue Events",
        "accepted_execution": "Accepted execution: ",
        "retry_rejected_waypoint": "Retry Rejected Waypoint",
        "retry_waypoint_title": "Retry Waypoint",
        "retry_waypoint_confirm": "Retry the rejected waypoint in the SAME accepted execution?",
        "awaiting_execution_status": "Awaiting execution status",
        "raw_records_title": "Raw Records (Read-only)",
        "configuration_required": "Rescue thresholds are required; ranking and dispatch are disabled",
        "no_pending_alerts": "No pending alerts",
        "no_pending_events": "No pending rescue events",
        "user_safe_btn": "User Safe",
        "user_unconfirmed_btn": "User Safety Unconfirmed",
        "emergency_safe_btn": "Safety Confirmed",
        "still_unconfirmed_btn": "Still Unconfirmed",
        "emergency_not_available_btn": "Record NOT_AVAILABLE",
        "search_confirm_btn": "Confirm Search - Create Rescue Event",
        "search_confirm_title": "Confirm Search",
        "search_confirm_text": "Confirm that contact checks are complete and a search is required.",
        "prepare_mission_btn": "Prepare Mission",
        "preview_only": "Preview only; no dispatch",
        "dispatch_db_warning": "MQTT was published, but the Firebase status update failed. Do not dispatch again; only the status write will be retried.",
        "no_data": "No data available or load failed",
        "login_title": "Login",
        "identity": "Identity",
        "user": "User",
        "admin": "Admin",
        "username": "Username",
        "password": "Password",
        "login_btn": "Login",
        "login_error": "Incorrect username or password",
        "login_connecting": "Connecting to database...",
        "login_db_error": "Database unavailable. Please try again.",
        "login_invalid_username": "Username contains invalid characters",
        "add_user": "Add User",
        "delete_user": "Delete User",
        "user_list": "User List",
        "new_username": "New Username",
        "new_password": "New Password",
        "confirm_delete": "Confirm delete user {}?",
        "admin_panel_title": "Admin Panel - Ground Station Users",
        "manage_users": "Manage Ground Station Users",
        "start_date": "Start Date",
        "start_time": "Start Time",
        "end_date": "End Date",
        "end_time": "End Time",
        "status": "Status",
        "event_title": "Event Title",
        "event_date": "Event Date",
        "dispatch_btn": "Dispatch Drone",
        "dispatch_confirm": "Dispatch drone to this location?\nLat: {}\nLon: {}",
        "dispatch_success": "Mission dispatched!\nTarget: ({}, {})",
        "mqtt_not_connected": "MQTT not connected, cannot dispatch",
        "sos_title": "SOS Rescue Pattern",
        "sos_subtitle": "Last-known coord + radius -> auto search route",
        "sos_lat_label": "Lat:",
        "sos_lon_label": "Lon:",
        "sos_radius_label": "Radius (m):",
        "sos_status_idle": "Pattern: idle",
        "sos_status_fmt": "Pattern: {} ({} WPs)",
        "sos_spiral_btn": "Preview Spiral Route",
        "sos_grid_btn": "Preview Grid Route",
        "sos_module_err": "sos_pattern module not loaded",
        "sos_invalid_input": "Invalid lat/lon/radius",
        "sos_generation_err": "Pattern generation failed",
    }
}

current_lang = "zh"


rt_db = None
_firebase_active_target = None
firebase_init_error = None
_firebase_init_lock = threading.Lock()


def initialize_firebase():

    global rt_db, firebase_init_error, _firebase_active_target

    if DEMO_SCREENSHOT_MODE:
        raise RuntimeError("Firebase is disabled in local demo mode")

    config = load_firebase_runtime_config()
    if not config["ready"]:
        raise ValueError("; ".join(config["errors"]))
    target = config["database_url"]
    if rt_db is not None:
        if _firebase_active_target != target:
            raise ValueError("Firebase target changed; restart the Ground Station before continuing")
        return rt_db

    with _firebase_init_lock:
        if rt_db is not None:
            if _firebase_active_target != target:
                raise ValueError("Firebase target changed; restart the Ground Station before continuing")
            return rt_db

        try:
            try:
                app = firebase_admin.get_app(FIREBASE_APP_NAME)
            except ValueError:
                credential_path = get_firebase_credential_path()
                cred = credentials.Certificate(credential_path)
                app = firebase_admin.initialize_app(
                    cred,
                    {
                        "databaseURL": target,
                        "httpTimeout": FIREBASE_HTTP_TIMEOUT_SECONDS,
                    },
                    name=FIREBASE_APP_NAME,
                )

            app_target = load_firebase_runtime_config({"GS_FIREBASE_DATABASE_URL": app.options.get("databaseURL")})
            if not app_target["ready"] or app_target["database_url"] != target:
                raise ValueError("Existing Firebase application uses a different target")
            rt_db = db.reference("/", app=app)
            _firebase_active_target = target
            firebase_init_error = None
            print("Firebase initialized")
            return rt_db
        except Exception as exc:
            firebase_init_error = type(exc).__name__
            print(f"[ERROR] Firebase initialization failed: {firebase_init_error}")
            raise


root = None
left_title = None
scroll_frame = None
map_widget = None
drone_status_label = None
fb_status_label = None
reload_button = None
lang_button = None
drone_title_label = None
fb_title_label = None
logout_btn = None
onboard_rescue_title_label = None
onboard_rescue_status_label = None


class OfflineDemoMap(ctk.CTkFrame):


    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="#101827", corner_radius=0, **kwargs)
        self._markers = []
        self._path = []
        self._canvas = tk.Canvas(
            self,
            bg="#101827",
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda _event: self._redraw_schematic())

    def set_position(self, _latitude, _longitude):
        return None

    def set_zoom(self, _zoom):
        return None

    def delete_all_marker(self):
        self._markers = []
        self._redraw_schematic()

    def delete_all_path(self):
        self._path = []
        self._redraw_schematic()

    def set_marker(self, latitude, longitude, text=""):
        self._markers.append((float(latitude), float(longitude), str(text)))
        self._redraw_schematic()

    def set_path(self, coordinates):
        self._path = [(float(lat), float(lon)) for lat, lon in coordinates]
        self._redraw_schematic()

    def _project(self, points, width, height):
        if not points:
            return []
        latitudes = [point[0] for point in points]
        longitudes = [point[1] for point in points]
        lat_min, lat_max = min(latitudes), max(latitudes)
        lon_min, lon_max = min(longitudes), max(longitudes)
        lat_span = max(lat_max - lat_min, 0.0001)
        lon_span = max(lon_max - lon_min, 0.0001)
        margin_x = max(80, width * 0.14)
        margin_y = max(100, height * 0.18)
        usable_w = max(1, width - 2 * margin_x)
        usable_h = max(1, height - 2 * margin_y)
        return [
            (
                margin_x + ((lon - lon_min) / lon_span) * usable_w,
                height - margin_y - ((lat - lat_min) / lat_span) * usable_h,
            )
            for lat, lon in points
        ]

    def _redraw_schematic(self):
        canvas = self._canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 640)
        height = max(canvas.winfo_height(), 600)

        for x in range(60, width, 60):
            canvas.create_line(x, 0, x, height, fill="#1d2a3d", width=1)
        for y in range(60, height, 60):
            canvas.create_line(0, y, width, y, fill="#1d2a3d", width=1)

        canvas.create_text(
            width / 2,
            40,
            text="OFFLINE ROUTE SCHEMATIC",
            fill="#7dd3fc",
            font=("Segoe UI", 20, "bold"),
        )
        canvas.create_text(
            width / 2,
            70,
            text="Coordinate values intentionally hidden — no map tiles loaded",
            fill="#a8b3c7",
            font=("Segoe UI", 12),
        )

        raw_points = [(lat, lon) for lat, lon, _text in self._markers]
        projected = self._project(raw_points, width, height)
        if self._path:
            path_projected = self._project(self._path, width, height)
            if len(path_projected) >= 2:
                flattened = [value for point in path_projected for value in point]
                canvas.create_line(
                    *flattened,
                    fill="#38bdf8",
                    width=4,
                    smooth=True,
                    arrow=tk.LAST,
                )

        for index, ((x, y), (_lat, _lon, label)) in enumerate(
            zip(projected, self._markers),
            start=1,
        ):
            canvas.create_oval(
                x - 10,
                y - 10,
                x + 10,
                y + 10,
                fill="#fbbf24" if index == 1 else "#22c55e",
                outline="#ffffff",
                width=2,
            )
            canvas.create_text(
                x,
                y - 24,
                text=label or f"WP {index}",
                fill="#ffffff",
                font=("Segoe UI", 11, "bold"),
            )

        canvas.create_text(
            24,
            height - 30,
            anchor="w",
            text=DEMO_DATA_NOTICE,
            fill="#fb7185",
            font=("Segoe UI", 11, "bold"),
        )


sos_title_label = None
sos_subtitle_label = None
sos_lat_label_widget = None
sos_lon_label_widget = None
sos_radius_label_widget = None
sos_status_label = None
sos_spiral_btn_widget = None
sos_grid_btn_widget = None
sos_current_pattern = None


mqtt_client = None
mqtt_connected = False
mqtt_status_label = None
mqtt_title_label = None
mqtt_connect_btn_widget = None
ground_rescue_store = None
ground_rescue_store_lock = threading.Lock()
mission_cards = []
current_users_data = {}
quick_start_observations = {}
pending_rescue_event_card_ids = []
refresh_issues = []
periodic_refresh_after_id = None


published_uncommitted_events = {}


def safe_configure(widget, **kwargs):

    if widget is None:
        return
    try:
        if widget.winfo_exists():
            widget.configure(**kwargs)
    except Exception:
        pass


def _cancel_periodic_refresh():
    global periodic_refresh_after_id
    if periodic_refresh_after_id is None:
        return True
    callback_id = periodic_refresh_after_id
    try:
        if root is not None and root.winfo_exists():
            root.after_cancel(callback_id)
    except Exception:


        periodic_refresh_after_id = callback_id
        return False
    periodic_refresh_after_id = None
    return True


def _periodic_refresh_tick():
    global periodic_refresh_after_id
    periodic_refresh_after_id = None
    try:
        root_exists = root is not None and bool(root.winfo_exists())
    except Exception:
        root_exists = False
    if not root_exists:
        return
    try:
        refresh_data()
    except Exception as exc:
        print(f"[ERROR] Periodic refresh failed: {type(exc).__name__}")
    finally:


        _schedule_periodic_refresh()


def _schedule_periodic_refresh():

    global periodic_refresh_after_id
    if not _cancel_periodic_refresh():
        return
    try:
        root_exists = root is not None and bool(root.winfo_exists())
    except Exception:
        root_exists = False
    if not root_exists:
        return
    try:
        periodic_refresh_after_id = root.after(
            AUTO_REFRESH_INTERVAL_MS, _periodic_refresh_tick
        )
    except Exception:
        periodic_refresh_after_id = None


def logout(current_window):
    _cancel_periodic_refresh()
    disconnect_mqtt()
    if current_window and current_window.winfo_exists():
        current_window.destroy()
    show_login_page()


def toggle_language():
    global current_lang
    current_lang = "en" if current_lang == "zh" else "zh"

def update_language():
    lang = LANGUAGES[current_lang]
    if root is not None and root.winfo_exists():
        root.title(lang["title"])
        safe_configure(left_title, text=lang["left_title"])
        safe_configure(drone_title_label, text=lang["drone_title"])
        safe_configure(onboard_rescue_title_label, text=lang["onboard_rescue_title"])
        safe_configure(mqtt_title_label, text=lang["mqtt_title"])
        safe_configure(fb_title_label, text=lang["fb_title"])
        safe_configure(reload_button, text=lang["reload_btn"])
        safe_configure(lang_button, text=lang["lang_btn"])
        safe_configure(logout_btn, text=lang["logout_btn"])

        if mqtt_connect_btn_widget:
            btn_text = lang["mqtt_disconnect_btn"] if mqtt_connected else lang["mqtt_connect_btn"]
            safe_configure(mqtt_connect_btn_widget, text=btn_text)


        safe_configure(sos_title_label, text=lang["sos_title"])
        safe_configure(sos_subtitle_label, text=lang["sos_subtitle"])
        safe_configure(sos_lat_label_widget, text=lang["sos_lat_label"])
        safe_configure(sos_lon_label_widget, text=lang["sos_lon_label"])
        safe_configure(sos_radius_label_widget, text=lang["sos_radius_label"])
        safe_configure(sos_spiral_btn_widget, text=lang["sos_spiral_btn"])
        safe_configure(sos_grid_btn_widget, text=lang["sos_grid_btn"])

        if sos_current_pattern is None:
            safe_configure(sos_status_label, text=lang["sos_status_idle"])
        else:
            kind, count = sos_current_pattern
            safe_configure(
                sos_status_label,
                text=lang["sos_status_fmt"].format(kind, count),
            )


def mqtt_endpoint_label():
    if DEMO_SCREENSHOT_MODE:
        return DEMO_BROKER_LABEL
    if not MQTT_BROKER or not 1 <= MQTT_PORT <= 65535:
        return "Broker: not configured"
    return f"Broker: {MQTT_BROKER}:{MQTT_PORT}"


def connect_mqtt():
    global mqtt_client, mqtt_connected

    if DEMO_SCREENSHOT_MODE:
        messagebox.showinfo("Local Demo", DEMO_BANNER)
        return

    if not MQTT_BROKER or not 1 <= MQTT_PORT <= 65535:
        messagebox.showerror(
            "MQTT Configuration Error",
            "Set MQTT_BROKER and an optional valid MQTT_PORT before connecting.",
        )
        safe_configure(
            mqtt_status_label,
            text="Configuration Required",
            text_color="red",
        )
        return

    if mqtt_connected:
        disconnect_mqtt()
        return

    if _PAHO_V2:
        mqtt_client = paho_mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION1,
            client_id="ground_station_" + str(int(time.time()))
        )
    else:
        mqtt_client = paho_mqtt.Client(client_id="ground_station_" + str(int(time.time())))

    mqtt_client.on_connect = _on_mqtt_connect
    mqtt_client.on_disconnect = _on_mqtt_disconnect
    mqtt_client.on_message = _on_mqtt_message

    try:


        configure_mqtt_client_auth(mqtt_client)
        print(f"Connecting to MQTT broker: {MQTT_BROKER}:{MQTT_PORT}")
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
    except MqttConfigurationError as e:
        messagebox.showerror("MQTT Configuration Error", str(e))
        safe_configure(mqtt_status_label, text="Configuration Failed", text_color="red")
    except Exception as e:
        messagebox.showerror("MQTT Error", f"Cannot connect:\n{e}")
        safe_configure(mqtt_status_label, text="Connection Failed", text_color="red")

def disconnect_mqtt():
    global mqtt_client, mqtt_connected

    if mqtt_client:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass
        mqtt_client = None

    mqtt_connected = False

    safe_configure(mqtt_status_label,
                   text=LANGUAGES[current_lang]["mqtt_text"],
                   text_color="orange")

    safe_configure(mqtt_connect_btn_widget,
                   text=LANGUAGES[current_lang]["mqtt_connect_btn"])

def _schedule_ui(func):

    if root and root.winfo_exists():
        root.after(0, func)


def _get_ground_rescue_store():

    global ground_rescue_store
    with ground_rescue_store_lock:
        if ground_rescue_store is None:
            ground_rescue_store = GroundRescueStore()
        return ground_rescue_store


def _process_onboard_record_message(client, payload):

    latest_record = None

    def update_state(state, record):
        nonlocal latest_record
        latest_record = record
        summary = format_record_summary(record, state)
        color = "spring green" if state == ACK_PUBLISHED else "orange"
        _schedule_ui(
            lambda text=summary, text_color=color: safe_configure(
                onboard_rescue_status_label,
                text=text,
                text_color=text_color,
            )
        )
        if record.get("schema_version") == 2:
            identity = (f"capture={record['capture_id']} request={record['request_id']} "
                        f"carrier_mission={record['carrier_mission_id']} "
                        f"carrier_execution={record['carrier_execution_id']}")
        else:
            identity = f"request={record['request_id']} mission={record['mission_id']}"
        if state == RECEIVED_STORED:
            print("Onboard rescue record received and stored locally: " + identity
                  + f" envelope={record['envelope_sha256'][:12]}")
        elif state == ACK_PUBLISHED:
            print("Ground ACK published: " + identity
                  + f" envelope={record['envelope_sha256'][:12]}")


    try:
        process_onboard_envelope(
            payload,
            store=_get_ground_rescue_store(),
            publish_ack=lambda ack: publish_ground_ack(
                client,
                MQTT_TOPIC_GROUND_ACK,
                ack,
            ),
            on_state=update_state,
        )
    except AckPublishError as exc:
        print(f"Stored onboard rescue record but ACK publish failed: {exc}")
        if latest_record is not None:
            summary = format_record_summary(latest_record, RECEIVED_STORED)
            summary += f"\nACK publish failed: {exc}"
        else:
            summary = f"ACK publish failed: {exc}"
        _schedule_ui(
            lambda text=summary: safe_configure(
                onboard_rescue_status_label,
                text=text,
                text_color="orange",
            )
        )
    except (RescueRecordError, RescueRecordPersistenceError) as exc:
        print(f"Rejected onboard rescue record: {exc}")
        _schedule_ui(
            lambda detail=str(exc): safe_configure(
                onboard_rescue_status_label,
                text=f"Rejected: {detail}",
                text_color="red",
            )
        )

def _on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected, mqtt_connection_generation
    if rc == 0:
        mqtt_connected = True
        mqtt_connection_generation += 1
        client.subscribe(MQTT_TOPIC_STATUS, qos=1)
        client.subscribe(MQTT_TOPIC_ONBOARD_RECORD, qos=1)
        client.subscribe(MQTT_TOPIC_RECEIVER_READY, qos=1)
        threading.Thread(target=_respond_to_receiver_ready, args=(client, None, mqtt_connection_generation),
                         daemon=True).start()
        threading.Thread(target=query_saved_executions, daemon=True).start()
        print("MQTT connected, subscribed to drone status and onboard rescue records")

        _schedule_ui(lambda: safe_configure(mqtt_status_label,
                       text="已連線" if current_lang == "zh" else "Connected",
                       text_color="spring green"))

        _schedule_ui(lambda: safe_configure(mqtt_connect_btn_widget,
                       text=LANGUAGES[current_lang]["mqtt_disconnect_btn"]))
    else:
        print(f"MQTT connect failed, rc={rc}")
        _schedule_ui(lambda: safe_configure(mqtt_status_label,
                       text=f"Failed (rc={rc})", text_color="red"))

def _on_mqtt_disconnect(client, userdata, rc):
    global mqtt_connected, mqtt_connection_generation
    mqtt_connected = False
    mqtt_connection_generation += 1
    print(f"MQTT disconnected (rc={rc})")

    _schedule_ui(lambda: safe_configure(mqtt_status_label,
                   text="已斷線" if current_lang == "zh" else "Disconnected",
                   text_color="orange"))

    _schedule_ui(lambda: safe_configure(mqtt_connect_btn_widget,
                   text=LANGUAGES[current_lang]["mqtt_connect_btn"]))

def _respond_to_receiver_ready(client, key, generation=None):


    generation = mqtt_connection_generation if generation is None else generation
    success = False
    delay = 1.0
    try:
        while True:
            try:
                info = client.publish(MQTT_TOPIC_RESCUE_SYNC,
                    json.dumps({"requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}),
                    qos=1, retain=False)
                if getattr(info, "rc", None) != getattr(paho_mqtt, "MQTT_ERR_SUCCESS", 0):
                    raise RuntimeError("SYNC publication failed")
                if info.wait_for_publish(timeout=MISSION_PUBLISH_TIMEOUT_SECONDS) is False or not info.is_published():
                    raise RuntimeError("SYNC publication is unconfirmed")
                success = True
                break
            except Exception as exc:
                print(f"[WARN] Receiver SYNC reply is unconfirmed: {type(exc).__name__}")
                if (not mqtt_connected or client is not mqtt_client
                        or generation != mqtt_connection_generation):
                    break


                _wait_for_sync_retry(delay)
                delay = min(delay * 2, 30.0)
                if (not mqtt_connected or client is not mqtt_client
                        or generation != mqtt_connection_generation):
                    break
    finally:
        if key is not None:
            receiver_ready_gate.finish(key, success)


def _wait_for_sync_retry(seconds):
    threading.Event().wait(seconds)


def correlated_status_display(data, journal=None):


    if not isinstance(data, dict):
        raise ValueError("mission status must be an object")
    status = data.get("status", "UNKNOWN")
    if not isinstance(status, str):
        raise ValueError("mission status must be text")
    message = data.get("message", "")
    if not isinstance(message, str):
        message = ""
    display = status + ("\n" + message[:512] if message else "")
    execution_id = data.get("execution_id")
    if not execution_id:
        if status in {"ONLINE", "OFFLINE"}:
            return display
        return display + "\nLegacy status; no execution correlation"
    if type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        raise ValueError("execution report requires schema v2")
    owned = journal is None
    try:
        if journal is None:
            journal = get_dispatch_journal()
        intent = journal.get_by_execution_id(execution_id)
        if intent is None or data.get("mission_id") != intent["payload"]["mission_id"]:
            return None
        display += f"\nExecution: {execution_id}"
        index = data.get("waypoint_index")
        if index is not None:
            total = len(intent["payload"]["waypoints"])
            if (type(index) is not int or index < 0 or index > total
                    or (index == total and not intent["payload"].get("return_to_launch", True))):
                raise ValueError("waypoint index does not match the authorized route")
            display += "\nReturn-to-launch waypoint" if index == total else f"\nSource waypoint: {index + 1}/{total}"
        if data.get("phase") == "LAND_REQUESTED" or status == "LANDING":
            display += "\nLanding requested; physical touchdown not confirmed"
        return display
    finally:
        if owned and journal is not None:
            journal.close()


def _process_drone_status_message(payload):
    try:
        data = json.loads(payload.decode("utf-8"))
        status = data.get("status", "UNKNOWN")
        message = data.get("message", "")
        if data.get('message_type') == 'ADMISSION':
            process_admission_report(data)
        elif data.get('message_type') == 'TRANSFER':
            process_transfer_report(data)
            return
        elif data.get('message_type') == 'EXECUTION':
            process_execution_report(data)
        elif data.get('message_type') == 'PROTOCOL_ERROR':
            data = process_protocol_error(data)
            if data is None: return
        display = correlated_status_display(data)
        if display is None:
            print("[INFO] Ignored status for an unrecognized execution")
            return

        if drone_status_label:
            if status in ("ONLINE", "NAVIGATING"):
                color = "lime"
            elif status == "ARRIVED":
                color = "spring green"
            elif status == "OFFLINE":
                color = "orange"
            elif status == "REJECTED":
                color = "red"
            else:
                color = "white"

            _schedule_ui(lambda d=display, c=color: safe_configure(drone_status_label, text=d, text_color=c))
    except Exception as e:
        print(f"MQTT message parse error: {e}")


def _on_mqtt_message(client, userdata, msg):
    if msg.topic == MQTT_TOPIC_STATUS:
        threading.Thread(target=_process_drone_status_message, args=(bytes(msg.payload),), daemon=True).start()
    elif msg.topic == MQTT_TOPIC_RECEIVER_READY:
        try:
            key = receiver_ready_gate.begin(bytes(msg.payload), bool(getattr(msg, "retain", False)))
            if key is not None:
                worker = threading.Thread(target=_respond_to_receiver_ready, args=(client, key, mqtt_connection_generation), daemon=True)
                try:
                    worker.start()
                except Exception:
                    receiver_ready_gate.finish(key, False)
                    raise
        except (ValueError, UnicodeError, TypeError):
            print("[WARN] Ignored invalid receiver readiness message")
    elif msg.topic == MQTT_TOPIC_ONBOARD_RECORD:


        threading.Thread(
            target=_process_onboard_record_message,
            args=(client, bytes(msg.payload)),
            daemon=True,
        ).start()


def _show_message_safely(method_name, title, text):
    try:
        getattr(messagebox, method_name)(title, text)
    except Exception as exc:
        print(f"[WARN] Operator dialog unavailable: {type(exc).__name__}")


def _dispatch_result(status, message="", mission_id=None, waypoint_count=0):
    return {
        "status": status,
        "message": message,
        "mission_id": mission_id,
        "waypoint_count": waypoint_count,
    }


def _validate_dispatch_inputs(
    waypoints, user_id, mission_id, mission_type, return_to_launch
):
    if not isinstance(user_id, str) or not MISSION_ID_TOKEN_RE.fullmatch(user_id):
        raise ValueError("invalid user_id for the UAV mission protocol")
    if not isinstance(mission_id, str) or not MISSION_ID_TOKEN_RE.fullmatch(mission_id):
        raise ValueError("invalid mission_id for the UAV mission protocol")
    if not isinstance(mission_type, str) or not MISSION_TYPE_RE.fullmatch(mission_type):
        raise ValueError("invalid mission_type for the UAV mission protocol")
    if not isinstance(return_to_launch, bool):
        raise ValueError("return_to_launch must be boolean")
    if not isinstance(waypoints, (list, tuple)) or not waypoints:
        raise ValueError("no valid waypoints to dispatch")
    if len(waypoints) > MAX_MISSION_WAYPOINTS:
        raise ValueError(f"mission exceeds the {MAX_MISSION_WAYPOINTS}-waypoint limit")

    normalized = []
    for index, point in enumerate(waypoints, start=1):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"waypoint {index} must contain latitude and longitude")
        latitude, longitude = point
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError(f"waypoint {index} coordinates must be numeric")
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            raise ValueError(f"waypoint {index} coordinates must be numeric") from None
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError(f"waypoint {index} coordinates must be finite")
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise ValueError(f"waypoint {index} coordinates are out of range")
        normalized.append((latitude, longitude))
    return normalized


def dispatch_mission(
    waypoints,
    user_id,
    mission_id,
    mission_type="event",
    return_to_launch=True,
    *,
    confirm_callback=None,
    publish_timeout=MISSION_PUBLISH_TIMEOUT_SECONDS,
    execution=None,
    before_publish=None,
    admission_attempt=1,
):


    lang = LANGUAGES[current_lang]
    try:
        normalized = _validate_dispatch_inputs(
            waypoints, user_id, mission_id, mission_type, return_to_launch
        )
        publish_timeout = float(publish_timeout)
        if not math.isfinite(publish_timeout) or publish_timeout <= 0:
            raise ValueError("publish_timeout must be a positive finite number")
    except ValueError as exc:
        _show_message_safely("showerror", "Invalid Mission", str(exc))
        return _dispatch_result(DISPATCH_INVALID_PARAMETERS, str(exc))

    full_mission_id = f"{user_id}/{mission_id}"
    if DEMO_SCREENSHOT_MODE:
        _show_message_safely("showinfo", "Local Demo", DEMO_BANNER)
        return _dispatch_result(
            DISPATCH_MQTT_UNAVAILABLE,
            "MQTT is disabled in local demo mode",
            full_mission_id,
            len(normalized),
        )
    if not mqtt_connected or mqtt_client is None:
        _show_message_safely("showerror", "Error", lang["mqtt_not_connected"])
        return _dispatch_result(
            DISPATCH_MQTT_UNAVAILABLE,
            lang["mqtt_not_connected"],
            full_mission_id,
            len(normalized),
        )

    first = normalized[0]
    last = normalized[-1]
    if len(normalized) == 1:
        confirm_msg = lang["dispatch_confirm"].format(
            f"{first[0]:.6f}", f"{first[1]:.6f}"
        )
    else:
        confirm_msg = (
            f"Dispatch {len(normalized)} waypoints?\n"
            f"First: {first[0]:.6f}, {first[1]:.6f}\n"
            f"Last:  {last[0]:.6f}, {last[1]:.6f}\n"
            f"Return to launch: {return_to_launch}"
        )
    confirmer = confirm_callback or messagebox.askyesno
    try:
        confirmed = bool(confirmer("Confirm Dispatch", confirm_msg))
    except Exception as exc:
        detail = f"operator confirmation unavailable: {type(exc).__name__}"
        _show_message_safely("showerror", "Confirmation Failed", detail)
        return _dispatch_result(
            DISPATCH_CANCELLED,
            detail,
            full_mission_id,
            len(normalized),
        )
    if not confirmed:
        return _dispatch_result(
            DISPATCH_CANCELLED,
            "operator cancelled",
            full_mission_id,
            len(normalized),
        )

    attempted = False
    try:
        if execution is None or not callable(before_publish):
            raise ValueError("a durable authorized execution is required before publishing")
        task_fingerprint(execution)
        if (execution["mission_id"] != full_mission_id or execution["mission_type"] != mission_type
                or execution.get("return_to_launch", True) != return_to_launch
                or [(float(p["latitude"]), float(p["longitude"])) for p in execution["waypoints"]] != normalized):
            raise ValueError("authorized execution differs from reviewed mission")
        before_publish()
        attempted = True
        _publish_transfer_messages(transfer_messages(execution, task_fingerprint(execution), admission_attempt), publish_timeout)
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        _show_message_safely("showerror", "Publication Unknown" if attempted else "Publication Blocked",
                             detail + ("; reconcile the saved execution before retrying" if attempted else ""))
        return _dispatch_result(
            DISPATCH_UNKNOWN if attempted else DISPATCH_PUBLISH_FAILED,
            detail,
            full_mission_id,
            len(normalized),
        )

    print(
        f"Mission publish confirmed by broker: type={mission_type}, "
        f"waypoints={len(normalized)}, RTL={return_to_launch}"
    )
    success_text = (
        "MQTT 發布已由 broker 確認；尚不代表 UAV 已收到或完成任務。"
        if current_lang == "zh"
        else "MQTT publish confirmed by the broker; UAV receipt or completion is not yet confirmed."
    )


    _show_message_safely("showinfo", "Publish Confirmed", success_text)
    return _dispatch_result(
        DISPATCH_PUBLISHED,
        success_text,
        full_mission_id,
        len(normalized),
    )


def _all_valid_latlons_from_dict(points_dict):

    if not isinstance(points_dict, dict):
        return []
    out = []
    for k in sorted(points_dict.keys(), key=lambda x: (str(x))):
        pt = points_dict[k]
        if isinstance(pt, dict) and "latitude" in pt and "longitude" in pt:
            try:
                out.append((float(pt["latitude"]), float(pt["longitude"])))
            except (TypeError, ValueError):
                continue
    return out


def _all_valid_latlons_from_list(points_list):

    if not isinstance(points_list, list):
        return []
    out = []
    for pt in points_list:
        if isinstance(pt, dict) and "latitude" in pt and "longitude" in pt:
            try:
                out.append((float(pt["latitude"]), float(pt["longitude"])))
            except (TypeError, ValueError):
                continue
    return out


def _firebase_safe_value(value):

    if isinstance(value, dict):
        return {
            str(key): _firebase_safe_value(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_firebase_safe_value(item) for item in value]
    return value


def detect_rescue_updates(users_data, now_ms, config):

    if not isinstance(users_data, dict):
        raise ValueError("Firebase users data must be an object")
    updates = {}
    additions = {}
    issues = []
    for user_id, user_info in sorted(users_data.items(), key=lambda item: str(item[0])):
        if not isinstance(user_info, dict):
            issues.append(
                {
                    "user_id": str(user_id),
                    "record_type": "users",
                    "record_id": str(user_id),
                    "error": "user record is not an object",
                }
            )
            continue
        result = scan_user_records(
            user_id,
            user_info,
            now_ms,
            quick_start_timeout_ms=config.get("quick_start_timeout_ms"),
            legacy_timezone=config.get("event_timezone"),
        )
        additions[user_id] = {
            "rescue_alerts": result["alerts"],
            "rescue_events": result["events"],
            "quick_start_monitoring": result.get("monitoring", {}),
            "alert_updates": result.get("alert_updates", {}),
        }
        for alert_id, alert in result["alerts"].items():
            updates[
                f"users/{user_id}/rescue_alerts/{alert_id}"
            ] = _firebase_safe_value(alert)
        for event_id, event in result["events"].items():
            updates[
                f"users/{user_id}/rescue_events/{event_id}"
            ] = _firebase_safe_value(event)
        for session_id, monitoring in result.get("monitoring", {}).items():
            updates[f"users/{user_id}/quick_start_monitoring/{session_id}"] = _firebase_safe_value(monitoring)
        for alert_id, fields in result.get("alert_updates", {}).items():
            for field, value in fields.items():
                updates[f"users/{user_id}/rescue_alerts/{alert_id}/{field}"] = _firebase_safe_value(value)
        issues.extend(result["issues"])
    return updates, additions, issues


def _merge_rescue_additions(users_data, additions):
    for user_id, groups in additions.items():
        user_info = users_data.get(user_id)
        if not isinstance(user_info, dict):
            continue
        for group_name in ("rescue_alerts", "rescue_events", "quick_start_monitoring"):
            current = user_info.get(group_name)
            if not isinstance(current, dict):
                current = {}
                user_info[group_name] = current
            current.update(groups.get(group_name, {}))
        for alert_id, fields in groups.get("alert_updates", {}).items():
            if isinstance(user_info["rescue_alerts"].get(alert_id), dict):
                user_info["rescue_alerts"][alert_id].update(fields)


def _normalize_ordered_waypoints(raw_points):
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("primary record has no waypoint list")
    waypoints = []
    for index, point in enumerate(raw_points, start=1):
        if not isinstance(point, dict):
            raise ValueError(f"primary waypoint {index} is invalid")
        if "latitude" not in point or "longitude" not in point:
            raise ValueError(f"primary waypoint {index} has no coordinate")
        waypoints.append((point["latitude"], point["longitude"]))
    return waypoints


def _primary_record(user_info, event):
    record_type = event.get("primary_record_type")
    record_id = event.get("primary_record_id")
    if not isinstance(record_type, str) or not record_type:
        raise ValueError("primary_record_type must be a non-empty string")
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("primary_record_id must be a non-empty string")
    records = user_info.get(record_type)
    if not isinstance(records, dict):
        raise ValueError("primary record group is missing")
    record = records.get(record_id)
    if not isinstance(record, dict) or record.get("_deleted") is True:
        raise ValueError("primary record is missing or deleted")
    return record_type, str(record_id), record


def _booking_episode_problem(user_info, workflow):
    if workflow.get("trigger_type") != EVENT_BOOKING_TIMEOUT:
        return None
    records = user_info.get(BOOKED_EVENTS)
    booking = records.get(workflow.get("primary_record_id")) if isinstance(records, dict) else None
    if not isinstance(booking, dict) or booking.get("_deleted") is True:
        return "Booking source is missing or deleted; current timeout episode cannot be verified"
    try:
        current_end = expected_end_at_ms(booking, rescue_runtime_config.get("event_timezone"))
    except RescueEventError as exc:
        return f"Booking source cannot be verified: {exc}"
    episode_end = workflow.get("primary_expected_end_at_ms", workflow.get("abnormal_since_ms"))
    if episode_end != current_end:
        return "Booking source changed; verify the current timeout episode"
    return None


def prepare_mission_for_rescue_event(user_info, event, now_ms=None):

    if not isinstance(user_info, dict) or not isinstance(event, dict):
        raise ValueError("user and rescue event data must be objects")
    if str(event.get("status", "")).upper() != PENDING:
        raise ValueError("only a PENDING rescue event can be dispatched")
    require_sos_verification(event)
    user_id = event.get("user_id")
    event_id = event.get("event_id")
    if not isinstance(user_id, str) or not isinstance(event_id, str):
        raise ValueError("rescue event identity is invalid")
    expected_record_type = {
        EVENT_BOOKING_TIMEOUT: BOOKED_EVENTS,
        QUICK_START_INACTIVITY: QUICK_START_SESSIONS,
        QUICK_START_LOCATION_TIMEOUT: QUICK_START_SESSIONS,
        SOS: RESCUE_REQUESTS,
    }.get(event.get("trigger_type"))
    if expected_record_type is None:
        raise ValueError("rescue event trigger_type is unsupported")
    record_type, record_id, record = _primary_record(user_info, event)
    if record_type != expected_record_type:
        raise ValueError("rescue event trigger_type does not match its primary record")

    if record_type == BOOKED_EVENTS:
        episode_problem = _booking_episode_problem(user_info, event)
        if episode_problem:
            raise ValueError(episode_problem)
        waypoints = _normalize_ordered_waypoints(record.get("waypoints"))
        mission_type = "event"
        mqtt_mission_token = event_id
    elif record_type == QUICK_START_SESSIONS:
        try:
            points, ignored_count = session_gps_points(
                record, _epoch_now_ms() if now_ms is None else now_ms
            )
        except RescueEventError as exc:
            raise ValueError(str(exc)) from None
        from quick_start_freshness import sample_identity
        monitor = (user_info.get("quick_start_monitoring") or {}).get(record_id, {})
        rejected = set(monitor.get("rejected_future_samples", []))
        if any(sample_identity(point) in rejected for point in points):
            raise ValueError("Quick Start primary record contains quarantined future GPS points; review the source record")
        if ignored_count:
            raise ValueError(
                f"Quick Start primary record contains {ignored_count} invalid GPS points "
                "(coordinate or session time range); review the source record"
            )
        if not points:
            raise ValueError("Quick Start primary record has no valid GPS points")
        waypoints = [
            (point["latitude"], point["longitude"])
            for point in points
        ]
        mission_type = "session"
        mqtt_mission_token = event_id
    elif record_type == RESCUE_REQUESTS:
        if record.get("status") is not None and str(record["status"]).upper() != PENDING:
            raise ValueError("SOS request is no longer pending")
        if not _SOS_PATTERN_OK:
            raise ValueError("SOS search-pattern module is unavailable")
        if "latitude" not in record or "longitude" not in record:
            raise ValueError("SOS primary record has no coordinate")
        try:
            generated = generate_spiral(
                record["latitude"],
                record["longitude"],
                radius_m=200.0,
                spacing_m=30.0,
            )
        except Exception as exc:
            raise ValueError(f"SOS route generation failed: {exc}") from None
        waypoints = [
            (point["latitude"], point["longitude"])
            for point in generated
        ]
        mission_type = "rescue"


        mqtt_mission_token = record_id
    else:
        raise ValueError(f"unsupported primary record type: {record_type!r}")

    normalized = _validate_dispatch_inputs(
        waypoints,
        user_id,
        mqtt_mission_token,
        mission_type,
        True,
    )
    return {
        "rescue_event_id": event_id,
        "primary_record_type": record_type,
        "primary_record_id": record_id,
        "mqtt_mission_token": mqtt_mission_token,
        "mqtt_mission_id": f"{user_id}/{mqtt_mission_token}",
        "mission_type": mission_type,
        "waypoints": normalized,
        "return_to_launch": True,
    }


def supplementary_record_summary(user_info, event):

    primary_type = event.get("primary_record_type")
    primary_id = str(event.get("primary_record_id"))
    parts = []
    for group_name, label in (
        (BOOKED_EVENTS, "Event Booking"),
        (QUICK_START_SESSIONS, "Quick Start"),
        (RESCUE_REQUESTS, "SOS"),
    ):
        records = user_info.get(group_name)
        if not isinstance(records, dict):
            continue
        count = sum(
            1
            for record_id, record in records.items()
            if isinstance(record, dict) and record.get("_deleted") is not True
            and not (group_name == primary_type and str(record_id) == primary_id)
        )
        if count:
            parts.append(f"{label}: {count}")
    return ", ".join(parts) if parts else "None"


def _epoch_now_ms():
    return int(time.time() * 1000)


def _format_duration_ms(value):
    if value is None:
        return "N/A"
    try:
        total_seconds = max(0, int(value) // 1000)
    except (TypeError, ValueError):
        return "Invalid"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _profile_contact_data(user_id, user_info):
    profile = user_info.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    phone = profile.get("phone")
    phone = str(phone).strip() if phone is not None else ""
    raw_contacts = profile.get("emergency_contacts")
    if isinstance(raw_contacts, str):
        try:
            raw_contacts = json.loads(raw_contacts)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_contacts = []
    contacts = []
    if isinstance(raw_contacts, list):
        for item in raw_contacts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            raw_number = item.get("phone")
            number = raw_number.strip() if isinstance(raw_number, str) else ""
            if number:
                contacts.append({"name": name, "phone": number})
    return {
        "phone": phone or str(user_id),
        "emergency_contacts": contacts,
        "emergency_contact_available": bool(contacts),
    }


def _add_card_button(card, text, command, color="#2563EB", state="normal"):
    button = ctk.CTkButton(
        card,
        text=text,
        command=command,
        fg_color=color,
        hover_color="#1D4ED8" if color != "#E53935" else "#C62828",
        height=30,
        font=("Segoe UI", 11, "bold"),
        state=state,
    )
    button.pack(fill="x", padx=12, pady=(4, 4))
    return button


def _show_waypoint_route(waypoints, label="Primary route"):
    if not waypoints or map_widget is None:
        return
    map_widget.delete_all_marker()
    map_widget.delete_all_path()
    for index in sorted({0, len(waypoints) - 1}) if len(waypoints) > 256 else range(len(waypoints)):
        latitude, longitude = waypoints[index]
        map_widget.set_marker(latitude, longitude, text=f"{label} {index + 1}")
    if len(waypoints) >= 2:
        map_widget.set_path(waypoints)
    average_lat = sum(point[0] for point in waypoints) / len(waypoints)
    average_lon = sum(point[1] for point in waypoints) / len(waypoints)
    map_widget.set_position(average_lat, average_lon)
    map_widget.set_zoom(17 if len(waypoints) == 1 else 14)


def _firebase_user(database_root, user_id):
    return database_root.child("users").child(user_id)


def _load_live_user(user_id, database_root=None):
    if database_root is None:
        database_root = initialize_firebase()
    user_info = _firebase_user(database_root, user_id).get()
    if not isinstance(user_info, dict):
        raise ValueError("user record is unavailable")
    return database_root, user_info


def _alert_at_firebase_path(user_id, alert_id, user_info):
    alerts = user_info.get("rescue_alerts")
    alert = alerts.get(alert_id) if isinstance(alerts, dict) else None
    if not isinstance(alert, dict):
        raise ValueError("alert is no longer available")
    if alert.get("user_id") != user_id or alert.get("alert_id") != alert_id:
        raise ValueError("alert identity does not match its Firebase path")
    return alert


def _show_workflow_error(exc):
    _show_message_safely(
        "showerror", "Rescue Workflow", str(exc) or type(exc).__name__
    )


def _handle_user_contact(user_id, alert_id, result):
    try:
        database_root, user_info = _load_live_user(user_id)
        transition_alert(
            database_root, user_id, alert_id,
            lambda alert, current: record_user_contact(
                alert, result,
                _profile_contact_data(user_id, current)["emergency_contact_available"]),
        )
        refresh_data()
    except Exception as exc:
        _show_workflow_error(exc)


def _convert_alert_atomically(
    database_root, user_id, alert_id, user_info, alert
):
    if not isinstance(alert, dict):
        raise ValueError("alert is no longer available")
    if alert.get("user_id") != user_id or alert.get("alert_id") != alert_id:
        raise ValueError("alert identity does not match its Firebase path")
    if not messagebox.askyesno(
        LANGUAGES[current_lang]["search_confirm_title"],
        LANGUAGES[current_lang]["search_confirm_text"],
    ):
        return False
    def convert_current(current):
        now_ms = _epoch_now_ms()
        current_alert = _alert_at_firebase_path(user_id, alert_id, current)
        if current_alert.get("trigger_type") == QUICK_START_LOCATION_TIMEOUT:
            _primary_record(current, current_alert)
            current, result = apply_user_reconciliation(current, user_id, now_ms, rescue_runtime_config)
            session_id = current_alert.get("primary_record_id")
            problems = [issue for issue in result["issues"]
                        if issue.get("record_type") == QUICK_START_SESSIONS
                        and issue.get("record_id") == session_id]
            if problems:
                raise ValueError("Quick Start source cannot be reconciled: " + problems[0]["error"])
            if session_id not in result["monitoring"]:
                raise ValueError("Quick Start monitoring history is unavailable; review the source episode")
            current_alert = _alert_at_firebase_path(user_id, alert_id, current)
        if current_alert.get("trigger_type") == SOS:
            _, _, request = _primary_record(current, current_alert)
            if str(request.get("status", "")).upper() != PENDING:
                raise ValueError("SOS request is no longer pending")
        episode_problem = _booking_episode_problem(current, current_alert)
        if episode_problem:
            raise ValueError(episode_problem)
        updated_alert, event = convert_alert_to_rescue_event(
            current_alert, now_ms, search_confirmed=True,
            existing_events=current.get("rescue_events", {}),
        )
        current["rescue_alerts"][alert_id] = _firebase_safe_value(updated_alert)
        current.setdefault("rescue_events", {})[event["event_id"]] = _firebase_safe_value(event)
        return current
    user_transaction(database_root, user_id, convert_current)
    return True


def _handle_emergency_contact(user_id, alert_id, result):
    try:
        database_root, user_info = _load_live_user(user_id)

        user_info = transition_alert(
            database_root, user_id, alert_id,
            lambda alert, current: _firebase_safe_value(record_emergency_contact(
                alert, result,
                _profile_contact_data(user_id, current)["emergency_contact_available"])),
        )
        updated = _alert_at_firebase_path(user_id, alert_id, user_info)
        if result == SAFETY_UNCONFIRMED:
            _convert_alert_atomically(
                database_root, user_id, alert_id, user_info, updated
            )
        refresh_data()
    except Exception as exc:
        _show_workflow_error(exc)


def _handle_resolution_conversion(user_id, alert_id):
    try:
        database_root, user_info = _load_live_user(user_id)
        alert = _alert_at_firebase_path(user_id, alert_id, user_info)
        if _convert_alert_atomically(
            database_root, user_id, alert_id, user_info, alert
        ):
            refresh_data()
    except Exception as exc:
        _show_workflow_error(exc)


def _refresh_after_committed_dispatch(result):

    try:
        refresh_data()
        result["refresh_status"] = "OK"
    except Exception as exc:
        result["refresh_status"] = "FAILED"
        print(f"[ERROR] Post-dispatch refresh failed: {type(exc).__name__}")
        _show_message_safely(
            "showerror",
            "View Refresh Failed",
            "Mission status is already DISPATCHED. The view could not refresh; "
            "do not publish the mission again.",
        )
    return result


def build_execution_payload(prepared, execution_id, *, altitude=DEFAULT_FLIGHT_ALTITUDE,
                            hover_seconds=DEFAULT_HOVER_SECONDS):

    payload = {"schema_version": 2, "execution_id": execution_id,
               "mission_id": prepared["mqtt_mission_id"], "mission_type": prepared["mission_type"],
               "waypoints": [{"latitude": a, "longitude": b} for a, b in prepared["waypoints"]],
               "return_to_launch": prepared["return_to_launch"],
               "altitude": altitude, "hover_seconds": hover_seconds}
    task_fingerprint(payload)
    return payload


def _local_state_directory():
    default_directory = (Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
                         / "MASS26GroundStation" / "state" if os.name == "nt"
                         else Path.home() / ".local" / "state" / "trigger_search_gs")
    directory = Path(os.environ.get("GS_STATE_DIR", str(default_directory))).expanduser()
    return directory


def get_dispatch_journal():
    config = load_firebase_runtime_config()
    if not config["ready"]:
        raise ValueError("; ".join(config["errors"]))
    return DispatchJournal(_local_state_directory() / "executions.sqlite3", config["database_url"])


def get_observation_store():
    config = load_firebase_runtime_config()
    if not config["ready"]:
        raise ValueError("; ".join(config["errors"]))
    return ObservationStore(_local_state_directory() / "gps_observations.sqlite3", config["database_url"])


def observe_committed_quick_start(user_id, user, now_ms, store):

    observations = {}
    from quick_start_freshness import sample_identity
    monitors = user.get("quick_start_monitoring", {})
    sessions = user.get(QUICK_START_SESSIONS, {})
    if not isinstance(monitors, dict) or not isinstance(sessions, dict):
        return observations
    for session_id, monitor in monitors.items():
        if not isinstance(monitor, dict) or not isinstance(sessions.get(session_id), dict):
            continue
        sample = monitor.get("latest_sample")
        if not isinstance(sample, dict) or monitor.get("latest_sample_at_ms") != sample.get("timestamp_ms"):
            continue
        points, _ = session_gps_points(sessions[session_id], now_ms)
        identity = sample_identity(sample)
        if identity in monitor.get("rejected_future_samples", []):
            continue
        if not any(sample_identity(point) == identity for point in points):
            continue
        first_seen = store.observe(user_id, session_id, sample["timestamp_ms"], now_ms)
        observations[session_id] = {"sample_at_ms": sample["timestamp_ms"],
                                    "first_seen_at_ms": first_seen,
                                    "workstation_id": store.workstation_id}
    return observations


def _source_record_hash(user, event):
    return active_queue.source_fingerprint(user, event)


def _queue_operation(user_id, event_id, operation, database_root=None, journal=None):
    owned = journal is None
    try:
        database_root, user = _load_live_user(user_id, database_root)
        journal = get_dispatch_journal() if journal is None else journal
        return operation(database_root, user, journal, journal.workstation_id())
    finally:
        if owned and journal is not None:
            journal.close()


def select_rescue_event(user_id, event_id, database_root=None, *, journal=None):

    def select(database_root, user, ledger, owner_id):
        if not rescue_runtime_config.get("ready"):
            raise ValueError("configure the rescue thresholds before selecting an event")
        if ledger.get((user_id, event_id)) is not None:
            raise ValueError("a saved execution requires recovery, not a new selection")
        return active_queue.select_event(database_root, user_id, event_id, owner_id,
                                         uuid.uuid4().hex, _epoch_now_ms())
    return _queue_operation(user_id, event_id, select, database_root, journal)


def prepare_active_rescue_event(user_id, event_id, database_root=None, *, journal=None):

    def prepare(database_root, user, ledger, owner_id):
        if ledger.get((user_id, event_id)) is not None:
            raise ValueError("an authorized execution cannot be prepared again")
        entry = active_queue.active_entry(user, user_id, event_id, owner_id)
        event = user["rescue_events"][event_id]
        now_ms = _epoch_now_ms()
        prepared = prepare_mission_for_rescue_event(user, event, now_ms)
        prepared.update({"altitude": DEFAULT_FLIGHT_ALTITUDE, "hover_seconds": DEFAULT_HOVER_SECONDS})
        return active_queue.save_preparation(database_root, user_id, event_id, owner_id,
            entry["selection_id"], entry["version"], prepared, _source_record_hash(user, event), now_ms)
    return _queue_operation(user_id, event_id, prepare, database_root, journal)


def cancel_active_rescue_event(user_id, event_id, database_root=None, *, journal=None):
    def cancel(database_root, user, ledger, owner_id):
        if ledger.get((user_id, event_id)) is not None:
            raise ValueError("a saved execution cannot be cancelled or returned to pending")
        entry = active_queue.active_entry(user, user_id, event_id, owner_id)
        return active_queue.return_to_pending(database_root, user_id, event_id, owner_id,
                                              entry["selection_id"], entry["version"])
    return _queue_operation(user_id, event_id, cancel, database_root, journal)


def recover_active_confirmation(user_id, event_id, database_root=None, *, journal=None):
    def recover(database_root, user, ledger, owner_id):
        if ledger.get((user_id, event_id)) is not None:
            raise ValueError("a saved execution must be reconciled; confirmation cannot be reset")
        entry = active_queue.active_entry(user, user_id, event_id, owner_id)
        return active_queue.recover_confirmation(database_root, user_id, event_id, owner_id,
                                                entry["selection_id"], entry["version"])
    return _queue_operation(user_id, event_id, recover, database_root, journal)


def _run_active_queue_action(action, user_id, event_id):
    try:
        result = action(user_id, event_id)
        refresh_data()
        return result
    except Exception as exc:
        _show_workflow_error(exc)
        return None


def _claim_execution(database_root, user_id, event_id, payload, source_hash=None,
                     *, owner_id=None, selection_id=None, confirmation_id=None):
    fingerprint = task_fingerprint(payload)
    def claim(current):
        event = active_queue.current_event(current, user_id, event_id)
        entry = active_queue.active_entry(current, user_id, event_id, owner_id, selection_id)
        source_group = current.get(event.get('primary_record_type'), {})
        source = source_group.get(event.get('primary_record_id')) if isinstance(source_group, dict) else None
        if isinstance(source, dict) and source.get('_deleted') is True:
            raise ValueError('primary record was deleted; dispatch requires review')
        existing = event.get("execution")
        if existing:
            if (existing.get("execution_id") != payload["execution_id"]
                    or existing.get("fingerprint") != fingerprint
                    or entry.get("phase") != active_queue.AUTHORIZED
                    or entry.get("execution_id") != payload["execution_id"]):
                raise ValueError("event is already occupied or requires non-publishing recovery")
            return current
        if (entry.get("phase") != active_queue.CONFIRMING
                or not confirmation_id or entry.get("confirmation_id") != confirmation_id):
            raise ValueError("confirmation changed; the old confirmation cannot authorize dispatch")
        if source_hash is None or _source_record_hash(current, event) != source_hash:
            raise ValueError("primary record changed during confirmation; prepare and review it again")
        active_queue.validate_quick_start_quarantine(current, event)
        event["execution"] = {"execution_id": payload["execution_id"], "fingerprint": fingerprint,
                              "payload": payload, "state": "CLAIMED", "transport_version": 1, "attempt": 1}
        entry.update({"phase": active_queue.AUTHORIZED, "execution_id": payload["execution_id"],
                      "version": entry["version"] + 1})
        entry.pop("confirmation_id", None)
        return current
    return user_transaction(database_root, user_id, claim)


def _publish_transfer_messages(messages, timeout=MISSION_PUBLISH_TIMEOUT_SECONDS):
    if not mqtt_connected or mqtt_client is None:
        raise ValueError('MQTT is unavailable')
    for message in messages:
        info = mqtt_client.publish(MQTT_TOPIC_TRANSFER, transfer_canonical(message).decode(), qos=1, retain=False)
        if getattr(info, 'rc', None) != getattr(paho_mqtt, 'MQTT_ERR_SUCCESS', 0):
            raise RuntimeError('mission transfer publication failed')
        if info.wait_for_publish(timeout=timeout) is False or not info.is_published():
            raise RuntimeError('mission transfer publication is unconfirmed')


def process_protocol_error(data, journal=None):
    owned = journal is None
    try:
        journal = journal or get_dispatch_journal()
        intent = journal.get_by_execution_id(data.get('execution_id'))
        if (intent is None or data.get('content_fingerprint') != intent['fingerprint']
                or data.get('attempt') != intent['attempt']): return None
        key = journal.key_for_execution(intent['execution_id'])
        journal.set_state(key, intent['execution_id'], 'UNKNOWN', str(data.get('reason', 'Transfer requires review'))[:512])
        return dict(data, mission_id=intent['payload']['mission_id'])
    finally:
        if owned and journal is not None: journal.close()


def process_execution_report(data, database_root=None, journal=None):
    owned = journal is None
    try:
        journal = journal or get_dispatch_journal()
        intent = journal.get_by_execution_id(data.get('execution_id'))
        if (not intent or data.get('schema_version') != 2
                or data.get('mission_id') != intent['payload']['mission_id']
                or data.get('content_fingerprint') != intent['fingerprint']): return None
        key = journal.key_for_execution(intent['execution_id'])
        root = database_root if database_root is not None else initialize_firebase()
        def update(user):
            execution = user.get('rescue_events', {}).get(key[1], {}).get('execution', {})
            if execution.get('execution_id') != intent['execution_id']:
                raise ValueError('execution cloud identity changed')
            revision = data.get('state_revision')
            old_revision = execution.get('last_report', {}).get('state_revision', -1)
            if type(revision) is not int or revision < 1: return user
            if type(old_revision) is int and revision <= old_revision: return user
            execution['last_report'] = data
            return user
        user_transaction(root, key[0], update)
        _schedule_ui(refresh_data)
        return data
    finally:
        if owned and journal is not None: journal.close()


def query_saved_executions():
    journal = None
    try:
        journal = get_dispatch_journal()
        for intent in journal.outstanding():
            _publish_transfer_messages([transfer_query(intent['payload'], intent['fingerprint'], intent['attempt'])])
    except Exception as exc:
        print('Saved execution query deferred: ' + type(exc).__name__)
    finally:
        if journal is not None: journal.close()


def retry_rejected_waypoint(user_id, event_id, database_root=None, confirm_callback=None):
    root, user = _load_live_user(user_id, database_root)
    execution = user.get('rescue_events', {}).get(event_id, {}).get('execution', {})
    report = execution.get('last_report', {})
    if execution.get('state') != 'ACCEPTED' or report.get('phase') != 'TARGET_REJECTED':
        raise ValueError('only the same explicitly rejected waypoint can be retried')
    if not (confirm_callback or messagebox.askyesno)(LANGUAGES[current_lang]['retry_waypoint_title'], LANGUAGES[current_lang]['retry_waypoint_confirm']):
        return
    if not mqtt_connected or mqtt_client is None: raise ValueError('MQTT is unavailable')
    payload = dict(execution_id=execution['execution_id'], mission_id=report['mission_id'],
                   operator_confirmed=True, reason='Operator confirmed retry of rejected waypoint')
    info = mqtt_client.publish(MQTT_TOPIC_OPERATOR_RETRY, json.dumps(payload), qos=1, retain=False)
    if getattr(info, 'rc', None) != 0 or info.wait_for_publish(timeout=5) is False or not info.is_published():
        raise ValueError('waypoint retry publication is unconfirmed; query before retrying')


def _render_execution_progress(users_data):
    for uid, user in users_data.items():
        if not isinstance(user, dict): continue
        for eid, event in user.get('rescue_events', {}).items():
            if not isinstance(event, dict) or event.get('status') != 'DISPATCHED': continue
            execution = event.get('execution', {})
            if not isinstance(execution, dict): continue
            report = execution.get('last_report', {})
            if not isinstance(report, dict): report = {}
            card = create_mission_card(scroll_frame, title=f'{uid}/{eid}',
                subtitle=LANGUAGES[current_lang]['accepted_execution'] + str(execution.get('execution_id', '')),
                status='warning', extra_info=str(report.get('phase', LANGUAGES[current_lang]['awaiting_execution_status'])) + '\n' + str(report.get('reason', '')))
            mission_cards.append(card)
            if report.get('phase') == 'TARGET_REJECTED':
                _add_card_button(card, LANGUAGES[current_lang]['retry_rejected_waypoint'],
                    lambda u=uid, e=eid: _run_active_queue_action(retry_rejected_waypoint, u, e))


def process_admission_report(data, database_root=None, journal=None):
    owned = journal is None
    try:
        journal = journal or get_dispatch_journal()
        intent = journal.record_admission(data)
        if intent is None: return None
        key = journal.key_for_execution(intent['execution_id'])
        root = database_root if database_root is not None else initialize_firebase()
        if intent['state'] == 'ACCEPTED':
            _commit_execution(root, key[0], key[1], intent, _epoch_now_ms())
        else:
            def update(user):
                event = user.get('rescue_events', {}).get(key[1], {})
                execution = event.get('execution', {})
                if execution.get('execution_id') != intent['execution_id'] or execution.get('fingerprint') != intent['fingerprint']:
                    raise ValueError('admission cloud identity changed')
                if event.get('status') == PENDING:
                    execution.update(state=intent['state'], admission=intent['admission'], attempt=intent['attempt'])
                return user
            user_transaction(root, key[0], update)
        _schedule_ui(refresh_data)
        return intent
    finally:
        if owned and journal is not None: journal.close()


def process_transfer_report(data, journal=None, database_root=None):
    if data.get('message_type') != 'TRANSFER' or data.get('request_kind') != 'QUERY': return
    owned = journal is None
    try:
        journal = journal or get_dispatch_journal()
        intent = journal.get_by_execution_id(data.get('execution_id'))
        if (not intent or intent['protocol_version'] != 1 or intent['state'] == 'ACCEPTED'
                or data.get('content_fingerprint') != intent['fingerprint']
                or data.get('attempt') != intent['attempt']): return
        key = journal.key_for_execution(intent['execution_id'])
        root, user = _load_live_user(key[0], database_root)
        event = user.get('rescue_events', {}).get(key[1], {})
        remote = event.get('execution', {})
        if remote.get('execution_id') != intent['execution_id'] or remote.get('fingerprint') != intent['fingerprint']:
            raise ValueError('saved execution ownership changed')
        source_group = user.get(event.get('primary_record_type'), {})
        source = source_group.get(event.get('primary_record_id')) if isinstance(source_group, dict) else None
        if isinstance(source, dict) and source.get('_deleted') is True:
            journal.set_state(key, intent['execution_id'], 'UNKNOWN', 'Primary record deleted; transfer recovery requires review')
            return
        missing = data.get('missing_chunks')
        if not isinstance(missing, list): raise ValueError('missing chunk list required')
        _publish_transfer_messages(transfer_messages(intent['payload'], intent['fingerprint'], intent['attempt'],
                                   None if data.get('manifest_required') is True else missing))
    finally:
        if owned and journal is not None: journal.close()


def _commit_execution(database_root, user_id, event_id, intent, published_at_ms):
    admission = intent.get('admission') or {}
    if (intent.get('state') != 'ACCEPTED' or admission.get('accepted') is not True
            or admission.get('execution_id') != intent['execution_id']
            or admission.get('content_fingerprint') != intent['fingerprint']):
        raise ValueError('UAV admission evidence required before dispatch commit')
    def commit(current):
        event = current.get("rescue_events", {}).get(event_id)
        if not isinstance(event, dict):
            raise ValueError("event is no longer available")
        execution = event.get("execution", {})
        if (execution.get("execution_id") != intent["execution_id"]
                or execution.get("fingerprint") != intent["fingerprint"]):
            raise ValueError("execution ownership changed; manual reconciliation required")
        if event.get("status") not in {PENDING, "DISPATCHED"}:
            raise ValueError("event state changed; manual reconciliation required")
        event.update({"status": "DISPATCHED", "dispatch_mission_id": intent["payload"]["mission_id"]})
        event.setdefault("dispatch_published_at_ms", intent.get("broker_confirmed_at_ms") or published_at_ms)
        event.setdefault("dispatch_accepted_at_ms", published_at_ms)
        execution.update(state="ACCEPTED", admission=admission, attempt=intent["attempt"])
        active_entries = current.get("active_events", {})
        entry = active_entries.get(event_id) if isinstance(active_entries, dict) else None
        if entry is not None:
            if not isinstance(entry, dict) or entry.get("execution_id") != intent["execution_id"]:
                raise ValueError("active queue execution changed; manual reconciliation required")
            del active_entries[event_id]
        return current
    return user_transaction(database_root, user_id, commit)


def dispatch_rescue_event(user_id, event_id, database_root=None, *, journal=None,
                          confirm_callback=None, resume_execution=False):


    owned_journal = journal is None
    key = (user_id, event_id)
    try:
        database_root, user = _load_live_user(user_id, database_root)
        event = user.get("rescue_events", {}).get(event_id)
        if not isinstance(event, dict) or event.get("user_id") != user_id or event.get("event_id") != event_id:
            raise ValueError("rescue event identity does not match its Firebase path")
        if journal is None:
            journal = get_dispatch_journal()
        intent = journal.get(key)
        remote = event.get("execution")
        owner_id = journal.workstation_id()
        entry = user.get("active_events", {}).get(event_id)
        if event.get("status") == PENDING and entry is None and (intent or remote):
            entry = active_queue.migrate_execution_to_active(
                database_root, user_id, event_id, owner_id, intent, _epoch_now_ms())
        if intent is None and isinstance(remote, dict):
            payload = remote.get("payload")
            if (task_fingerprint(payload) != remote.get("fingerprint")
                    or remote.get("execution_id") != payload["execution_id"]):
                raise ValueError("stored execution content is inconsistent")
            intent = journal.restore_remote(key, remote)
            intent = journal.set_state(key, intent["execution_id"], "UNKNOWN")
        if intent and isinstance(remote, dict) and (
                remote.get("execution_id") != intent["execution_id"]
                or remote.get("fingerprint") != intent["fingerprint"]):
            raise ValueError("local and cloud execution ownership differ; manual reconciliation required")
        if intent and intent["state"] == "ACCEPTED":
            _commit_execution(database_root, user_id, event_id, intent, _epoch_now_ms())
            journal.set_state(key, intent["execution_id"], "ACCEPTED")
            published_uncommitted_events.pop(key, None)
            result = _dispatch_result(DISPATCH_PUBLISHED, "Recovered without republishing",
                                      intent["payload"]["mission_id"], len(intent["payload"]["waypoints"]))
            result.update({"execution_id": intent["execution_id"], "firebase_status": "DISPATCHED"})
            return _refresh_after_committed_dispatch(result)
        if entry and entry.get("phase") == active_queue.RECOVERY_ONLY:
            if intent:
                _publish_transfer_messages([transfer_query(intent['payload'], intent['fingerprint'], intent['attempt'])])
            return {**_dispatch_result(DISPATCH_UNKNOWN, "Legacy execution: querying UAV evidence without republishing"),
                    "execution_id": intent["execution_id"] if intent else entry.get("execution_id")}
        if intent and resume_execution and intent['state'] != 'REJECTED':
            _publish_transfer_messages([transfer_query(intent['payload'], intent['fingerprint'], intent['attempt'])])
            return {**_dispatch_result(DISPATCH_UNKNOWN, 'Querying saved execution; no new execution created'),
                    'execution_id': intent['execution_id'], 'firebase_status': 'PENDING'}
        if intent and not resume_execution:
            return {**_dispatch_result(DISPATCH_UNKNOWN, "Existing authorized execution requires reconciliation"),
                    "execution_id": intent["execution_id"], "recovery_state": intent["state"]}
        if not rescue_runtime_config.get("ready"):
            return _dispatch_result(DISPATCH_INVALID_PARAMETERS, "rescue configuration is required")
        if event.get("status") != PENDING:
            raise ValueError("rescue event is no longer PENDING")
        if not mqtt_connected or mqtt_client is None:
            return _dispatch_result(DISPATCH_MQTT_UNAVAILABLE, "MQTT is unavailable")
        entry = active_queue.active_entry(
            _firebase_user(database_root, user_id).get(), user_id, event_id, owner_id)
        source_hash = None
        confirmation_id = None
        if intent:
            payload = intent["payload"]
            confirmation = "Retry the SAME saved execution? Its earlier publication may have succeeded. No new flight ID will be created.\n"
        else:
            if entry.get("phase") != active_queue.PREPARED:
                raise ValueError("prepare the selected active event before dispatch confirmation")
            prepared = entry["prepared"]
            source_hash = entry["source_hash"]
            payload = build_execution_payload(prepared, uuid.uuid4().hex,
                altitude=prepared["altitude"], hover_seconds=prepared["hover_seconds"])
            confirmation_id = uuid.uuid4().hex
            entry = active_queue.begin_confirmation(database_root, user_id, event_id, owner_id,
                entry["selection_id"], entry["version"], confirmation_id)
            confirmation = "Confirm the prepared mission shown in the active-event queue?\n"
        first, last = payload["waypoints"][0], payload["waypoints"][-1]
        confirmation += (f"Execution: {payload['execution_id']}\n"
                         f"Source waypoints: {len(payload['waypoints'])}\n"
                         f"First: {first['latitude']:.6f}, {first['longitude']:.6f}\n"
                         f"Last: {last['latitude']:.6f}, {last['longitude']:.6f}\n"
                         f"Return to launch: {payload['return_to_launch']}\n"
                         f"Altitude: {payload.get('altitude')} m; hover: {payload.get('hover_seconds')} s")
        confirmer = confirm_callback or messagebox.askyesno
        if not bool(confirmer("Confirm Dispatch", confirmation)):
            if intent is None:
                active_queue.return_to_pending(database_root, user_id, event_id, owner_id,
                    entry["selection_id"], entry["version"], confirmation_id=confirmation_id)
            return _dispatch_result(DISPATCH_CANCELLED, "operator cancelled")


        _claim_execution(database_root, user_id, event_id, payload, source_hash,
                         owner_id=owner_id, selection_id=entry["selection_id"], confirmation_id=confirmation_id)
        if intent is None:
            intent = journal.prepare(key, payload)
        if intent['state'] == 'REJECTED':
            intent = journal.next_attempt(key, payload['execution_id'])
            def record_attempt(user):
                execution = user['rescue_events'][event_id]['execution']
                if execution['execution_id'] != payload['execution_id']: raise ValueError('execution ownership changed')
                execution.update(attempt=intent['attempt'], state='CLAIMED')
                return user
            user_transaction(database_root, user_id, record_attempt)
        journal.set_state(key, payload["execution_id"], "CLAIMED")
        def before_publish():

            _claim_execution(database_root, user_id, event_id, payload,
                             owner_id=owner_id, selection_id=entry["selection_id"])
            journal.set_state(key, payload["execution_id"], "PUBLISHING")
        mission_user, mission_token = payload["mission_id"].split("/", 1)
        result = dispatch_mission(
            [(p["latitude"], p["longitude"]) for p in payload["waypoints"]],
            mission_user, mission_token, payload["mission_type"], payload["return_to_launch"],
            confirm_callback=lambda *_args: True, execution=payload, before_publish=before_publish, admission_attempt=intent["attempt"])
        result["execution_id"] = payload["execution_id"]
        if result["status"] != DISPATCH_PUBLISHED:
            journal.set_state(key, payload["execution_id"], "UNKNOWN", result.get("status", "unknown"))
            return result
        intent = journal.set_state(key, payload["execution_id"], "AWAITING_ACCEPTANCE")
        if intent['state'] == 'ACCEPTED':
            _commit_execution(database_root, user_id, event_id, intent, _epoch_now_ms())
            result['firebase_status'] = 'DISPATCHED'
        else:
            result['firebase_status'] = 'PENDING'
        return _refresh_after_committed_dispatch(result)
    except (ValueError, RescueEventError) as exc:
        _show_workflow_error(exc)
        return _dispatch_result(DISPATCH_INVALID_PARAMETERS, str(exc))
    except Exception as exc:
        _show_workflow_error(exc)
        return _dispatch_result(DISPATCH_UNKNOWN, "execution state requires reconciliation")
    finally:
        if owned_journal and journal is not None:
            journal.close()


def _section_heading(text, color="#7dd3fc"):
    label = ctk.CTkLabel(
        scroll_frame,
        text=text,
        font=("Segoe UI", 16, "bold"),
        text_color=color,
    )
    label.pack(anchor="w", pady=(16, 6))
    return label


def _render_pending_alerts(users_data, now_ms):
    _section_heading(LANGUAGES[current_lang]["pending_alerts_title"], "#fbbf24")
    entries = []
    for user_id, user_info in users_data.items():
        if not isinstance(user_info, dict):
            continue
        alerts = user_info.get("rescue_alerts")
        if not isinstance(alerts, dict):
            continue
        for alert_id, alert in alerts.items():
            if isinstance(alert, dict) and str(alert.get("status", "")).upper() == PENDING:
                entries.append((str(user_id), str(alert_id), alert, user_info))
    entries.sort(
        key=lambda item: (
            int(item[2].get("created_at_ms", 0))
            if str(item[2].get("created_at_ms", "")).isdigit()
            else 0,
            item[0],
            item[1],
        )
    )
    if not entries:
        ctk.CTkLabel(
            scroll_frame,
            text=LANGUAGES[current_lang]["no_pending_alerts"],
            text_color="#94a3b8",
        ).pack(anchor="w", padx=6, pady=(0, 8))
        return

    for user_id, alert_id, alert, user_info in entries:
        contacts = _profile_contact_data(user_id, user_info)
        identity_matches_path = (
            alert.get("user_id") == user_id
            and alert.get("alert_id") == alert_id
        )
        episode_problem = _booking_episode_problem(user_info, alert)
        emergency_text = "; ".join(
            f"{item['name']}: {item['phone']}" if item["name"] else item["phone"]
            for item in contacts["emergency_contacts"]
        ) or "NOT_AVAILABLE"
        try:
            abnormal_duration = now_ms - int(alert.get("abnormal_since_ms"))
        except (TypeError, ValueError):
            abnormal_duration = None
        subtitle = (
            f"Trigger: {alert.get('trigger_type', 'UNKNOWN')}\n"
            f"User phone: {contacts['phone']}\n"
            f"Emergency contact: {emergency_text}\n"
            f"Primary: {alert.get('primary_record_type')} / "
            f"{alert.get('primary_record_id')}\n"
            f"Abnormal duration: {'recovered; awaiting verification' if alert.get('location_status') == 'RECOVERED' else _format_duration_ms(abnormal_duration)}"
        )
        card = create_mission_card(
            scroll_frame,
            title=f"{user_id}/{alert_id}",
            subtitle=subtitle,
            status="warning",
            extra_info=(
                f"Stage: {alert.get('stage', 'UNKNOWN')}"
                + ("\n" + _location_alert_summary(alert, now_ms)
                   if alert.get("trigger_type") == QUICK_START_LOCATION_TIMEOUT else "")
                + (f"\n{episode_problem}" if episode_problem else "")
                + (
                    "\nProcessing blocked: identity does not match Firebase path"
                    if not identity_matches_path
                    else ""
                )
            ),
        )
        mission_cards.append(card)
        if not identity_matches_path or episode_problem:
            continue
        stage = alert.get("stage")
        if stage == CONTACT_USER:
            _add_card_button(
                card,
                LANGUAGES[current_lang]["user_safe_btn"],
                lambda uid=user_id, aid=alert_id: _handle_user_contact(
                    uid, aid, SAFE_CONFIRMED
                ),
                color="#15803D",
            )
            _add_card_button(
                card,
                LANGUAGES[current_lang]["user_unconfirmed_btn"],
                lambda uid=user_id, aid=alert_id: _handle_user_contact(
                    uid, aid, SAFETY_UNCONFIRMED
                ),
                color="#B45309",
            )
        elif stage == CONTACT_EMERGENCY:
            if contacts["emergency_contact_available"]:
                _add_card_button(
                    card,
                    LANGUAGES[current_lang]["emergency_safe_btn"],
                    lambda uid=user_id, aid=alert_id: _handle_emergency_contact(
                        uid, aid, SAFE_CONFIRMED
                    ),
                    color="#15803D",
                )
                _add_card_button(
                    card,
                    LANGUAGES[current_lang]["still_unconfirmed_btn"],
                    lambda uid=user_id, aid=alert_id: _handle_emergency_contact(
                        uid, aid, SAFETY_UNCONFIRMED
                    ),
                    color="#B45309",
                )
            else:
                _add_card_button(
                    card,
                    LANGUAGES[current_lang]["emergency_not_available_btn"],
                    lambda uid=user_id, aid=alert_id: _handle_emergency_contact(
                        uid, aid, NOT_AVAILABLE
                    ),
                    color="#B45309",
                )
        elif stage == RESOLUTION:
            _add_card_button(
                card,
                LANGUAGES[current_lang]["search_confirm_btn"],
                lambda uid=user_id, aid=alert_id: _handle_resolution_conversion(
                    uid, aid
                ),
                color="#E53935",
            )


def _collect_pending_event_entries(users_data):

    entries = []
    for user_id, user_info in users_data.items():
        if not isinstance(user_info, dict):
            continue
        events = user_info.get("rescue_events")
        if not isinstance(events, dict):
            continue
        for event_id, event in events.items():
            active_entries = user_info.get("active_events", {})
            is_active = isinstance(active_entries, dict) and event_id in active_entries
            if (isinstance(event, dict) and str(event.get("status", "")).upper() == PENDING
                    and not is_active and not event.get("execution")):
                entries.append((str(user_id), str(event_id), event, user_info))
    embedded_counts = {}
    for _user_id, _event_id, event, _user_info in entries:
        embedded_user_id = event.get("user_id")
        embedded_event_id = event.get("event_id")
        if isinstance(embedded_user_id, str) and isinstance(embedded_event_id, str):
            embedded_identity = (embedded_user_id, embedded_event_id)
            embedded_counts[embedded_identity] = (
                embedded_counts.get(embedded_identity, 0) + 1
            )

    invalid_by_key = {}
    for user_id, event_id, event, _user_info in entries:
        path_identity = (user_id, event_id)
        embedded_identity = (event.get("user_id"), event.get("event_id"))
        problems = []
        if embedded_identity != path_identity:
            problems.append("embedded identity does not match Firebase path")
        if (
            isinstance(embedded_identity[0], str)
            and isinstance(embedded_identity[1], str)
            and embedded_counts.get(embedded_identity, 0) > 1
        ):
            problems.append("embedded identity is duplicated across pending paths")
        if problems:
            invalid_by_key[path_identity] = "; ".join(problems)
    return entries, invalid_by_key


def _pending_execution_snapshots(entries):

    snapshots, errors = {}, {}
    journal = None
    try:
        journal = get_dispatch_journal()
        for user_id, event_id, event, _user in entries:
            key = (user_id, event_id)
            try:
                intent = journal.get(key)
                remote = event.get("execution")
                if remote is not None:
                    if not isinstance(remote, dict):
                        raise ValueError("invalid cloud execution metadata")
                    payload = remote.get("payload")
                    fingerprint = task_fingerprint(payload)
                    if (payload["execution_id"] != remote.get("execution_id")
                            or fingerprint != remote.get("fingerprint")):
                        raise ValueError("cloud execution content is inconsistent")
                    if intent and (intent["execution_id"] != payload["execution_id"]
                                   or intent["fingerprint"] != fingerprint):
                        raise ValueError("local/cloud execution conflict; manual reconciliation required")
                    if intent is None:
                        intent = {"execution_id": payload["execution_id"], "payload": payload,
                                  "state": "UNKNOWN", "fingerprint": fingerprint}
                if intent:
                    snapshots[key] = intent
            except (ValueError, TypeError, KeyError) as exc:
                errors[key] = str(exc)
    except Exception as exc:
        errors.update({(uid, eid): f"Execution storage unavailable ({type(exc).__name__})"
                       for uid, eid, _event, _user in entries})
    finally:
        if journal is not None:
            journal.close()
    return snapshots, errors


def _render_pending_events(users_data, now_ms):
    global pending_rescue_event_card_ids
    pending_rescue_event_card_ids = []
    _section_heading(LANGUAGES[current_lang]["pending_events_title"], "#fb7185")
    entries, invalid_by_key = _collect_pending_event_entries(users_data)
    if not entries:
        ctk.CTkLabel(
            scroll_frame,
            text=LANGUAGES[current_lang]["no_pending_events"],
            text_color="#94a3b8",
        ).pack(anchor="w", padx=6, pady=(0, 8))
        return

    metrics_by_key = {}
    if rescue_runtime_config.get("ready"):
        schedulable = []
        entry_by_object_id = {}
        for entry in entries:
            identity = (entry[0], entry[1])
            if identity in invalid_by_key:
                continue
            try:
                metrics_by_key[identity] = schedule_metrics(
                    entry[2],
                    now_ms,
                    rescue_runtime_config["wait_threshold_ms"],
                )
                schedulable.append(entry[2])
                entry_by_object_id[id(entry[2])] = entry
            except (SchedulerValidationError, TypeError, ValueError) as exc:
                invalid_by_key[identity] = str(exc)
        ordered_events = order_pending_events(
            schedulable,
            now_ms,
            rescue_runtime_config["wait_threshold_ms"],
        )
        ordered = [
            entry_by_object_id[id(event)]
            for event in ordered_events
        ]
        ordered.extend(
            sorted(
                (entry for entry in entries if (entry[0], entry[1]) in invalid_by_key),
                key=lambda entry: (entry[0], entry[1]),
            )
        )
    else:


        ordered = sorted(entries, key=lambda entry: (entry[0], entry[1]))

    for user_id, event_id, event, user_info in ordered:
        identity = (user_id, event_id)
        pending_rescue_event_card_ids.append(f"{user_id}/{event_id}")
        metrics = metrics_by_key.get(identity)
        problem = invalid_by_key.get(identity)
        for field in ("primary_record_type", "primary_record_id"):
            if not isinstance(event.get(field), str) or not event[field]:
                problem = f"{field} must be a non-empty string"
                break
        priority = "INVALID EVENT" if problem else metrics["effective_priority"] if metrics else "CONFIGURATION REQUIRED"
        subtitle = (f"Trigger: {event.get('trigger_type', 'UNKNOWN')}\n"
                    f"Queue waiting: {_format_duration_ms(metrics['queue_waiting_time_ms'] if metrics else None)}\n"
                    f"Abnormal duration: {_format_duration_ms(metrics['abnormal_duration_ms'] if metrics else None)}\n"
                    f"Primary: {event.get('primary_record_type')} / {event.get('primary_record_id')}")
        card = create_mission_card(scroll_frame, title=f"{user_id}/{event_id}", subtitle=subtitle,
            status="critical" if priority in {"HIGH", "INVALID EVENT"} else "warning",
            extra_info=f"Effective priority: {priority}" + (f"\n{problem}" if problem else ""))
        mission_cards.append(card)
        _add_card_button(card, "Select → Active Queue",
            lambda uid=user_id, eid=event_id: _run_active_queue_action(select_rescue_event, uid, eid),
            color="#2563EB", state="normal" if rescue_runtime_config.get("ready") and not problem else "disabled")


def _migrate_unqueued_executions(database_root, users_data, now_ms):

    ledger = None
    issues = []
    try:
        ledger = get_dispatch_journal()
        owner = ledger.workstation_id()
        for user_id, user in users_data.items():
            if not isinstance(user, dict) or not isinstance(user.get("rescue_events"), dict):
                continue
            for event_id, event in user["rescue_events"].items():
                if not isinstance(event, dict): continue
                remote = event.get('execution', {})
                if not isinstance(remote, dict):
                    issues.append({'record_type':'execution_storage','record_id':event_id,'error':'invalid saved execution metadata'})
                    continue
                if event.get('status') == 'DISPATCHED' and not (remote.get('admission') or {}).get('accepted'):
                    changed = active_queue.migrate_legacy_dispatch(database_root, user_id, event_id)
                    user.update(changed)
                    event = user['rescue_events'][event_id]
                if event.get('status') != PENDING: continue
                if event_id in user.get("active_events", {}):
                    continue
                try:
                    intent = ledger.get((user_id, event_id))
                    if intent or event.get("execution"):
                        entry = active_queue.migrate_execution_to_active(
                            database_root, user_id, event_id, owner, intent, now_ms)
                        user.setdefault("active_events", {})[event_id] = entry
                except Exception as exc:
                    issues.append({"record_type": "active_events", "record_id": event_id,
                                   "error": f"execution recovery requires reconciliation ({type(exc).__name__})"})
    except Exception as exc:
        issues.append({"record_type": "execution_storage", "record_id": "configuration",
                       "error": f"active queue storage unavailable ({type(exc).__name__})"})
    finally:
        if ledger is not None:
            ledger.close()
    return issues


def _show_associated_records(user, event):

    try:
        kind, record_id, primary = _primary_record(user, event)
    except (ValueError, TypeError) as exc:
        _show_workflow_error(exc)
        return
    supplementary = {}
    for group in (BOOKED_EVENTS, QUICK_START_SESSIONS, RESCUE_REQUESTS):
        records = user.get(group, {})
        if isinstance(records, dict):
            supplementary[group] = {key: value for key, value in records.items()
                                    if isinstance(value, dict) and value.get("_deleted") is not True and not (group == kind and key == record_id)}
    popup = ctk.CTkToplevel(root)
    popup.title("Associated Records — Read Only")
    popup.geometry("800x600")
    text = ctk.CTkTextbox(popup, wrap="word")
    text.pack(fill="both", expand=True, padx=12, pady=12)
    text.insert("1.0", json.dumps({"primary_record": {"type": kind, "id": record_id, "record": primary},
                                  "supplementary_records": supplementary}, ensure_ascii=False, indent=2))
    text.configure(state="disabled")


def _render_active_events(users_data, now_ms):

    _section_heading("Active Events — Operator Processing", "#a78bfa")
    entries = []
    for uid, user in users_data.items():
        if not isinstance(user, dict) or not isinstance(user.get("active_events"), dict):
            continue
        for eid, entry in user["active_events"].items():
            if isinstance(entry, dict):
                event = user.get("rescue_events", {}).get(eid, {})
                entries.append((str(uid), str(eid), event, user, entry))
    entries.sort(key=lambda item: (item[4].get("selected_at_ms") if type(item[4].get("selected_at_ms")) is int else -1, item[0], item[1]))
    if not entries:
        ctk.CTkLabel(scroll_frame, text="No selected active events", text_color="#94a3b8").pack(anchor="w", padx=6, pady=(0, 8))
        return
    snapshots, errors = _pending_execution_snapshots([item[:4] for item in entries])
    ledger = None
    try:
        ledger = get_dispatch_journal()
        owner_id = ledger.workstation_id()
    except Exception:
        owner_id = None
    finally:
        if ledger is not None:
            ledger.close()
    for uid, eid, event, user, entry in entries:
        identity = (uid, eid)
        intent = snapshots.get(identity)
        phase = entry.get("phase", "INVALID")
        owned = owner_id is not None and entry.get("owner_id") == owner_id
        problem = errors.get(identity)
        try:
            active_queue.active_entry(user, uid, eid)
        except Exception as exc:
            problem = str(exc)
        prepared = entry.get("prepared")
        points = prepared.get("waypoints") if isinstance(prepared, dict) else None
        if intent:
            points = [(p.get("latitude", p.get("lat")), p.get("longitude", p.get("lon"))) for p in intent["payload"]["waypoints"]]
        extra = f"Processing: {phase}; workstation: {'this instance' if owned else 'another instance (read only)'}"
        if points:
            extra += f"\nPrepared source waypoints: {len(points)}/{MAX_MISSION_WAYPOINTS}; RTL is additional"
        if intent:
            extra += f"\nExecution: {intent['execution_id']} ({intent['state']})"
            extra += "\n" + str(intent.get("detail", ""))
        if problem:
            extra += "\n" + problem
        card = create_mission_card(scroll_frame, title=f"{uid}/{eid}",
            subtitle=(f"Primary: {event.get('primary_record_type')} / {event.get('primary_record_id')}\n"
                      f"Supplementary (display only): {supplementary_record_summary(user, event)}"),
            status="warning", extra_info=extra)
        mission_cards.append(card)
        can_act = owned and problem is None
        def action_button(label, action, enabled=can_act):
            _add_card_button(card, label,
                lambda fn=action, u=uid, e=eid: _run_active_queue_action(fn, u, e),
                color="#7C3AED", state="normal" if enabled else "disabled")
        if points:
            _add_card_button(card, "Review Prepared Route", lambda route=tuple(tuple(p) for p in points): _show_waypoint_route(list(route)), color="#2563EB")
        _add_card_button(card, "Review Associated Records",
            lambda u=user, e=event: _show_associated_records(u, e), color="#2563EB")
        if phase in {active_queue.SELECTED, active_queue.PREPARED}:
            action_button("Prepare Mission" if phase == active_queue.SELECTED else "Prepare Again", prepare_active_rescue_event,
                          can_act and intent is None)
            if phase == active_queue.PREPARED:
                action_button("Confirm Dispatch", dispatch_rescue_event, can_act and intent is None)
            action_button("Cancel → Pending Queue", cancel_active_rescue_event, can_act and intent is None)
        elif phase == active_queue.CONFIRMING:
            action_button("Recover Interrupted Confirmation", recover_active_confirmation, can_act and intent is None)
        elif phase in {active_queue.AUTHORIZED, active_queue.RECOVERY_ONLY}:
            commit_only = bool(intent and intent["state"] == "ACCEPTED")
            retry = phase == active_queue.AUTHORIZED and not commit_only
            action_button("Recover Firebase Status (No Republish)" if commit_only else
                          "Review / Retry Same Saved Execution" if retry else "Reconcile Legacy Execution (No Republish)",
                          lambda u, e, resume=retry: dispatch_rescue_event(u, e, resume_execution=resume))


def _quick_start_gps_summary(session, now_ms, monitor=None, observation=None):
    try:
        if isinstance(monitor, dict):
            sample = monitor.get("latest_sample_at_ms")
            if sample is None:
                return "GPS: location not ready; timeout monitoring has not started"
            text = (f"GPS: {monitor.get('monitoring_status', 'UNKNOWN')}; "
                    f"last accepted sample: {sample} ms since epoch; "
                    f"ignored samples: {monitor.get('ignored_gps_point_count', 0)}")
            timeout = monitor.get("effective_timeout_ms")
            if timeout is not None:
                text += f"\nLocation update timeout: {timeout} ms"
            if monitor.get("configuration_pending"):
                text += "\nThreshold change pending a new fresh sample; previous policy retained"
            if isinstance(observation, dict) and observation.get("sample_at_ms") == sample:
                text += (f"\nFirst observed on this workstation: {observation['first_seen_at_ms']} ms"
                         " (local diagnostic; not cloud receipt)")
            else:
                text += "\nLocal first observation unavailable"
            return text
        points, ignored = session_gps_points(session, now_ms)
        if not points:
            return "GPS: location not ready; timeout monitoring has not started"
        return (f"Raw GPS samples: {len(points)}; ignored: {ignored}; "
                f"last valid sample: {points[-1]['timestamp_ms']} ms since epoch"
                "\nFreshness monitoring unavailable")
    except RescueEventError as exc:
        return f"GPS validation unavailable: {exc}"


def _location_alert_summary(alert, now_ms):
    if alert.get("trigger_type") != QUICK_START_LOCATION_TIMEOUT:
        return ""
    if alert.get("location_status") == "RECOVERED":
        return "Location recovered — manual safety verification still required"
    return ("Location update timed out — manual safety verification required\n"
            f"Last sample: {alert.get('sample_at_ms')} ms; "
            f"timeout onset: {alert.get('abnormal_since_ms')} ms")


def _render_raw_records_read_only(users_data, now_ms=None):
    now_ms = _epoch_now_ms() if now_ms is None else now_ms
    _section_heading(LANGUAGES[current_lang]["raw_records_title"], "#94a3b8")
    for user_id, user_info in sorted(users_data.items(), key=lambda item: str(item[0])):
        if not isinstance(user_info, dict):
            continue
        ctk.CTkLabel(
            scroll_frame,
            text=f"User: {user_id}",
            font=("Segoe UI", 14, "bold"),
            text_color="#cbd5e1",
        ).pack(anchor="w", pady=(10, 4))

        sessions = user_info.get(QUICK_START_SESSIONS)
        if isinstance(sessions, dict):
            for session_id, session in sorted(sessions.items(), key=lambda item: str(item[0])):
                if not isinstance(session, dict):
                    continue
                card = create_mission_card(
                    scroll_frame,
                    title=f"Quick Start / {session_id}",
                    subtitle=(
                        f"Start: {session.get('startTime', 'Unknown')}\n"
                        f"End: {session.get('endTime', 'Unknown')}"
                    ),
                    status="normal",
                    extra_info=(f"Status: {session.get('status', 'UNKNOWN')} | READ-ONLY\n"
                                + _quick_start_gps_summary(
                                    session, now_ms,
                                    (user_info.get("quick_start_monitoring") or {}).get(session_id),
                                    quick_start_observations.get(user_id, {}).get(session_id))),
                )
                card.bind(
                    "<Button-1>",
                    lambda _event, data=json.dumps(session): on_card_click(data, "session"),
                )
                mission_cards.append(card)

        bookings = user_info.get(BOOKED_EVENTS)
        if isinstance(bookings, dict):
            for booking_id, booking in sorted(bookings.items(), key=lambda item: str(item[0])):
                if not isinstance(booking, dict) or booking.get("_deleted") is True:
                    continue
                card = create_mission_card(
                    scroll_frame,
                    title=f"Event Booking / {booking_id}",
                    subtitle=(
                        f"Title: {booking.get('title', 'Unknown')}\n"
                        f"Date: {booking.get('date', 'Unknown')}\n"
                        f"Time: {booking.get('startTime', 'Unknown')} - "
                        f"{booking.get('endTime', 'Unknown')}"
                    ),
                    status="normal",
                    extra_info="READ-ONLY",
                )
                card.bind(
                    "<Button-1>",
                    lambda _event, data=json.dumps(booking): on_card_click(data, "event"),
                )
                mission_cards.append(card)

        requests = user_info.get(RESCUE_REQUESTS)
        if isinstance(requests, dict):
            for request_id, request in sorted(requests.items(), key=lambda item: str(item[0])):
                if not isinstance(request, dict):
                    continue
                card = create_mission_card(
                    scroll_frame,
                    title=f"SOS / {request_id}",
                    subtitle="Primary coordinate available" if (
                        request.get("latitude") is not None
                        and request.get("longitude") is not None
                    ) else "Primary coordinate missing",
                    status="normal",
                    extra_info=f"Status: {request.get('status', 'UNKNOWN')} | READ-ONLY",
                )
                card.bind(
                    "<Button-1>",
                    lambda _event, data=json.dumps(request): on_card_click(data, "rescue"),
                )
                mission_cards.append(card)


def _render_demo_mission_cards(users_data):

    global pending_rescue_event_card_ids
    demo_user = users_data["DEMO_USER"]
    _section_heading("Pending Alerts — synthetic preview", "#fbbf24")
    for alert in build_demo_pending_alerts(users_data):
        alert_card = create_mission_card(
            scroll_frame,
            title=alert["alert_id"],
            subtitle=(
                f"Trigger: {alert['trigger_type']}\n"
                f"Primary: {alert['primary_record_type']} / "
                f"{alert['primary_record_id']}\n"
                "Emergency contact: NOT_AVAILABLE"
            ),
            status="warning",
            extra_info=(
                f"Stage: {alert['stage']} — PREVIEW ONLY\n"
                "Demonstration contact outcomes; no calls are placed"
            ),
        )
        mission_cards.append(alert_card)

    _section_heading("Pending Rescue Events — scheduler order", "#fb7185")
    ordered_events = build_demo_ordered_rescue_events(users_data)
    pending_rescue_event_card_ids = [
        f"{event['user_id']}/{event['event_id']}" for event in ordered_events
    ]
    first_route = None
    for queue_position, event in enumerate(ordered_events, start=1):
        prepared = prepare_mission_for_rescue_event(demo_user, event)
        if first_route is None:
            first_route = list(prepared["waypoints"])
        search_detail = (
            "\nSearch points: 19" if event["trigger_type"] == "SOS" else ""
        )
        event_card = create_mission_card(
            scroll_frame,
            title=f"#{queue_position} {event['event_id']}",
            subtitle=(
                f"Priority: {event['effective_priority']}\n"
                f"Trigger: {event['trigger_type']}\n"
                f"Queue wait: {_format_duration_ms(event['queue_waiting_time_ms'])}\n"
                f"Primary: {event['primary_record_type']} / "
                f"{event['primary_record_id']}"
                f"{search_detail}"
            ),
            status="critical" if event["effective_priority"] == "HIGH" else "warning",
            extra_info="PREVIEW ONLY — dispatch disabled",
        )
        event_card.bind(
            "<Button-1>",
            lambda _event, points=tuple(prepared["waypoints"]):
                _show_waypoint_route(list(points), "Synthetic primary route"),
        )
        mission_cards.append(event_card)

    _section_heading("Raw Records — read-only reference", "#94a3b8")
    raw_summary = create_mission_card(
        scroll_frame,
        title="Event Booking / Quick Start / SOS",
        subtitle=(
            "2 Event Bookings; 1 Quick Start; 1 SOS\n"
            "SOS route preview: 19 search points"
        ),
        status="normal",
        extra_info=DEMO_RAW_RECORDS_NOTICE,
    )
    mission_cards.append(raw_summary)
    ctk.CTkLabel(
        scroll_frame,
        text=f"{DEMO_TEST_PARAMETER_NOTICE}\nPreview time: {DEMO_NOW_MS}",
        text_color="#fbbf24",
        justify="left",
        wraplength=330,
    ).pack(fill="x", padx=5, pady=(6, 10))
    safe_configure(fb_status_label, text=DEMO_DATABASE_STATUS, text_color="#fbbf24")

    if first_route:
        root.after(
            150,
            lambda points=tuple(first_route): _show_waypoint_route(
                list(points), "Synthetic queue leader"
            ),
        )


def refresh_data():
    global mission_cards, current_users_data, refresh_issues, quick_start_observations
    print("Reloading Ground Station data...")

    if DEMO_SCREENSHOT_MODE:
        for widget in scroll_frame.winfo_children():
            widget.destroy()
        mission_cards = []
        _render_demo_mission_cards(build_demo_users_data())
        print("Local demo records loaded; Firebase and MQTT remain disabled")
        return

    try:
        database_root = initialize_firebase()
        users_data = database_root.child("users").get() or {}
        if not isinstance(users_data, dict):
            raise ValueError("Firebase users data must be an object")
    except Exception as exc:
        error_type = type(exc).__name__
        print(f"[ERROR] Firebase refresh failed: {error_type}")
        safe_configure(
            fb_status_label,
            text=LANGUAGES[current_lang]["login_db_error"],
            text_color="#ff6b6b",
        )
        return

    updates = {}
    committed_users = {}
    committed_observation_times = {}
    quick_start_observations = {}
    try:
        issues = []
        now_scan = _epoch_now_ms()
        for user_id, user_info in list(users_data.items()):
            if not isinstance(user_info, dict):
                continue
            try:
                current, result = reconcile_user(database_root, user_id, now_scan, rescue_runtime_config,
                                                 clock_ms=_epoch_now_ms)
                users_data[user_id] = current
                committed_users[user_id] = current
                committed_observation_times[user_id] = _epoch_now_ms()
                issues.extend(result.get("issues", []))
                updates[user_id] = len(result.get("alerts", {})) + len(result.get("events", {}))
            except Exception as exc:
                issues.append({"record_type": "reconciliation", "record_id": str(user_id),
                               "error": f"conditional update failed ({type(exc).__name__})"})
    except Exception as exc:
        issues = [
            {
                "record_type": "reconciliation",
                "record_id": "batch",
                "error": f"batch update failed ({type(exc).__name__})",
            }
        ]
        print(f"[ERROR] Rescue reconciliation failed: {type(exc).__name__}")


    if any(user.get("quick_start_monitoring") for user in committed_users.values()):
        observation_store = None
        try:
            observation_store = get_observation_store()
            for user_id, user in committed_users.items():
                quick_start_observations[user_id] = observe_committed_quick_start(
                    user_id, user, committed_observation_times[user_id], observation_store)
        except Exception as exc:
            issues.append({"record_type": "local GPS observation", "record_id": "workstation",
                           "error": f"diagnostic unavailable ({type(exc).__name__}); alert state retained"})
        finally:
            if observation_store is not None:
                observation_store.close()

    issues.extend(_migrate_unqueued_executions(database_root, users_data, _epoch_now_ms()))
    current_users_data = users_data
    refresh_issues = issues
    for widget in scroll_frame.winfo_children():
        widget.destroy()
    mission_cards = []

    now_ms = _epoch_now_ms()
    if not rescue_runtime_config.get("ready"):
        warning = ctk.CTkLabel(
            scroll_frame,
            text=(
                f"{LANGUAGES[current_lang]['configuration_required']}\n"
                + "\n".join(rescue_runtime_config.get("errors", []))
            ),
            text_color="#fbbf24",
            justify="left",
            wraplength=330,
        )
        warning.pack(fill="x", padx=5, pady=(8, 2))
    if refresh_issues:
        issue_lines = []
        for issue in refresh_issues[:5]:
            issue_lines.append(
                f"{issue.get('record_type', 'record')} / "
                f"{issue.get('record_id', 'unknown')}: "
                f"{issue.get('error', 'invalid data')}"
            )
        if len(refresh_issues) > len(issue_lines):
            issue_lines.append(f"... and {len(refresh_issues) - len(issue_lines)} more")
        ctk.CTkLabel(
            scroll_frame,
            text=(
                f"Data validation issues: {len(refresh_issues)} "
                "(dispatch remains fail-closed)\n"
                + "\n".join(issue_lines)
            ),
            text_color="#fb7185",
            justify="left",
            wraplength=330,
        ).pack(fill="x", padx=5, pady=(4, 2))

    _render_pending_alerts(users_data, now_ms)
    _render_pending_events(users_data, now_ms)
    _render_active_events(users_data, now_ms)
    _render_execution_progress(users_data)
    _render_raw_records_read_only(users_data, now_ms)

    safe_configure(
        fb_status_label,
        text="已重新載入" if current_lang == "zh" else "Reloaded",
        text_color="yellow",
    )
    root.after(
        2000,
        lambda: safe_configure(
            fb_status_label,
            text=LANGUAGES[current_lang]["fb_connected"],
            text_color="spring green",
        ),
    )
    print(
        f"Ground Station data loaded: users={len(users_data)}, "
        f"new_records={len(updates)}, issues={len(refresh_issues)}"
    )


last_click_time = 0

def on_card_click(data_str, mission_type):
    global last_click_time
    now = time.time()
    if now - last_click_time < 0.5:
        return
    last_click_time = now

    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        return

    if not isinstance(data, dict) or data.get("_deleted") is True:
        return

    coords = []
    markers_to_add = []

    if mission_type == "rescue":
        lat = data.get("latitude")
        lon = data.get("longitude")
        if lat is not None and lon is not None:
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                coords.append((lat_f, lon_f))
                markers_to_add.append((lat_f, lon_f, "Rescue Request"))
            except (TypeError, ValueError):
                pass

    elif mission_type == "session":
        try:
            points, _ignored = session_gps_points(data, _epoch_now_ms())
        except RescueEventError:
            points = []
        for index, point in enumerate(points, start=1):
            lat = point["latitude"]
            lon = point["longitude"]
            coords.append((lat, lon))
            markers_to_add.append((lat, lon, f"Point {index}"))

    elif mission_type == "event":
        points = data.get("waypoints", [])
        if isinstance(points, list):
            for i, pt in enumerate(points):
                if isinstance(pt, dict) and "latitude" in pt and "longitude" in pt:
                    try:
                        lat = float(pt["latitude"])
                        lon = float(pt["longitude"])
                        coords.append((lat, lon))
                        markers_to_add.append((lat, lon, f"WP {i+1}"))
                    except (TypeError, ValueError):
                        continue

    if not coords:
        return

    map_widget.delete_all_marker()
    map_widget.delete_all_path()

    if len(markers_to_add) > 256:
        first, last = markers_to_add[0], markers_to_add[-1]
        label = '點' if current_lang == 'zh' else 'Point'
        markers_to_add = [(first[0], first[1], f'{label} 1/{len(coords)}'),
                          (last[0], last[1], f'{label} {len(coords)}/{len(coords)}')]
    for lat, lon, text in markers_to_add:
        map_widget.set_marker(lat, lon, text=text)

    if len(coords) >= 2:
        map_widget.set_path(coords)

    avg_lat = sum(c[0] for c in coords) / len(coords)
    avg_lon = sum(c[1] for c in coords) / len(coords)
    map_widget.set_position(avg_lat, avg_lon)
    map_widget.set_zoom(17 if len(coords) == 1 else 14)


def create_mission_card(parent, title, subtitle="", status="normal", extra_info=""):
    card = ctk.CTkFrame(parent, fg_color="#1f2a44", corner_radius=10, border_width=1, border_color="#3a4a6b")
    card.pack(fill="x", pady=6, padx=5, ipady=8)

    title_frame = ctk.CTkFrame(card, fg_color="transparent")
    title_frame.pack(fill="x", padx=12, pady=(8, 4))

    title_label = ctk.CTkLabel(
        title_frame,
        text=title,
        font=("Segoe UI", 15, "bold"),
        anchor="w",
        justify="left",
        wraplength=285,
    )
    title_label.pack(side="left")

    dot_color = "#00ff9d" if status == "active" else \
                "#ffcc00" if status == "warning" else \
                "#ff4444" if status == "critical" else "#888888"

    status_dot = ctk.CTkLabel(title_frame, text="●", text_color=dot_color, font=("Arial", 16))
    status_dot.pack(side="right")

    if subtitle:
        sub_label = ctk.CTkLabel(
            card,
            text=subtitle,
            font=("Segoe UI", 12),
            text_color="#b6c2d2",
            anchor="w",
            justify="left",
            wraplength=320,
        )
        sub_label.pack(anchor="w", padx=12, pady=(0, 4))

    if extra_info:
        info_label = ctk.CTkLabel(
            card,
            text=extra_info,
            font=("Segoe UI", 11),
            text_color="#d1d5db",
            anchor="w",
            justify="left",
            wraplength=320,
        )
        info_label.pack(anchor="w", padx=12)

    def on_enter(e):
        card.configure(fg_color="#2c3e50")
    def on_leave(e):
        card.configure(fg_color="#1f2a44")
    card.bind("<Enter>", on_enter)
    card.bind("<Leave>", on_leave)

    return card


def show_login_page():
    login_window = ctk.CTk()
    login_window.title(LANGUAGES[current_lang]["login_title"])
    login_window.geometry("400x450")
    login_window.resizable(False, False)
    login_window.configure(fg_color="#0d1b2a")

    lang_btn = ctk.CTkButton(
        login_window,
        text=LANGUAGES[current_lang]["lang_btn"],
        width=140,
        height=32,
        fg_color="transparent",
        hover_color="#3a3a3a",
        border_width=1,
        border_color="#555555"
    )
    lang_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-20, y=15)

    title_label = ctk.CTkLabel(login_window, text=LANGUAGES[current_lang]["login_title"], font=("Segoe UI", 24, "bold"))
    title_label.pack(pady=50)

    identity_label = ctk.CTkLabel(login_window, text=LANGUAGES[current_lang]["identity"])
    identity_label.pack(pady=5)

    identity_var = ctk.StringVar(value=LANGUAGES[current_lang]["user"])
    identity_menu = ctk.CTkOptionMenu(
        login_window,
        values=[LANGUAGES[current_lang]["user"], LANGUAGES[current_lang]["admin"]],
        variable=identity_var
    )
    identity_menu.pack(pady=5)

    username_entry = ctk.CTkEntry(login_window, placeholder_text=LANGUAGES[current_lang]["username"], width=250)
    username_entry.pack(pady=10)

    password_entry = ctk.CTkEntry(login_window, placeholder_text=LANGUAGES[current_lang]["password"], show="*", width=250)
    password_entry.pack(pady=10)

    login_btn = ctk.CTkButton(
        login_window,
        text=LANGUAGES[current_lang]["login_btn"],
        command=lambda: attempt_login(
            login_window,
            identity_var,
            username_entry,
            password_entry,
            error_label,
            login_btn,
        ),
        width=200
    )
    login_btn.pack(pady=20)

    error_label = ctk.CTkLabel(login_window, text="", font=("Segoe UI", 12))
    error_label.pack(pady=10)

    def on_enter_key(event):
        attempt_login(
            login_window,
            identity_var,
            username_entry,
            password_entry,
            error_label,
            login_btn,
        )

    username_entry.bind("<Return>", on_enter_key)
    password_entry.bind("<Return>", on_enter_key)
    login_btn.bind("<Return>", on_enter_key)

    def switch_lang():
        toggle_language()
        login_window.title(LANGUAGES[current_lang]["login_title"])
        title_label.configure(text=LANGUAGES[current_lang]["login_title"])
        identity_label.configure(text=LANGUAGES[current_lang]["identity"])
        identity_menu.configure(values=[LANGUAGES[current_lang]["user"], LANGUAGES[current_lang]["admin"]])
        identity_menu.set(LANGUAGES[current_lang]["user"])
        username_entry.configure(placeholder_text=LANGUAGES[current_lang]["username"])
        password_entry.configure(placeholder_text=LANGUAGES[current_lang]["password"])
        if not getattr(login_window, "_login_in_progress", False):
            login_btn.configure(text=LANGUAGES[current_lang]["login_btn"])
        lang_btn.configure(text=LANGUAGES[current_lang]["lang_btn"])

    lang_btn.configure(command=switch_lang)

    login_window.mainloop()

def attempt_login(
    login_window,
    identity_var,
    username_entry,
    password_entry,
    error_label,
    login_button,
):
    if getattr(login_window, "_login_in_progress", False):
        return

    identity = identity_var.get()
    username = username_entry.get().strip()

    password = password_entry.get()

    if not username or not password:
        error_label.configure(
            text=LANGUAGES[current_lang]["login_error"],
            text_color="red",
        )
        return

    if INVALID_FIREBASE_KEY_CHARS.search(username):
        error_label.configure(
            text=LANGUAGES[current_lang]["login_invalid_username"],
            text_color="red",
        )
        return

    is_admin = identity == LANGUAGES[current_lang]["admin"]
    path = f"ground_station/{'admin' if is_admin else 'user'}/{username}"
    result_queue = Queue(maxsize=1)

    login_window._login_in_progress = True
    login_button.configure(
        state="disabled",
        text=LANGUAGES[current_lang]["login_connecting"],
    )
    error_label.configure(
        text=LANGUAGES[current_lang]["login_connecting"],
        text_color="#FFD166",
    )

    def login_worker():
        try:
            database_root = initialize_firebase()
            account_data = database_root.child(path).get()
            authenticated = (
                isinstance(account_data, dict)
                and account_data.get("password") == password
            )
            result_queue.put(("success" if authenticated else "invalid", None))
        except Exception as exc:
            result_queue.put(("database_error", type(exc).__name__))

    def poll_login_result():
        try:
            window_exists = bool(login_window.winfo_exists())
        except tk.TclError:
            return

        if not window_exists:
            return

        try:
            status, error_type = result_queue.get_nowait()
        except Empty:
            login_window.after(50, poll_login_result)
            return

        login_window._login_in_progress = False
        login_button.configure(
            state="normal",
            text=LANGUAGES[current_lang]["login_btn"],
        )

        if status == "success":
            login_window.destroy()
            if is_admin:
                show_admin_panel()
            else:
                show_main_window()
        elif status == "invalid":
            error_label.configure(
                text=LANGUAGES[current_lang]["login_error"],
                text_color="red",
            )
        else:
            print(f"[ERROR] Firebase login failed: {error_type}")
            error_label.configure(
                text=LANGUAGES[current_lang]["login_db_error"],
                text_color="red",
            )

    threading.Thread(target=login_worker, daemon=True).start()
    login_window.after(50, poll_login_result)


def show_admin_panel():
    admin_window = ctk.CTk()
    admin_window.title(LANGUAGES[current_lang]["admin_panel_title"])
    admin_window.geometry("600x600")
    admin_window.configure(fg_color="#0d1b2a")

    tools_frame = ctk.CTkFrame(admin_window, fg_color="transparent")
    tools_frame.place(relx=1.0, rely=0.0, anchor="ne", x=-20, y=15)

    lang_btn = ctk.CTkButton(
        tools_frame,
        text=LANGUAGES[current_lang]["lang_btn"],
        width=140,
        height=32,
        fg_color="transparent",
        hover_color="#3a3a3a",
        border_width=1,
        border_color="#555555"
    )
    lang_btn.pack(anchor="e")

    logout_btn = ctk.CTkButton(
        tools_frame,
        text=LANGUAGES[current_lang]["logout_btn"],
        width=120,
        height=32,
        command=lambda: logout(admin_window),
        fg_color="red",
        hover_color="#c62828"
    )
    logout_btn.pack(anchor="e", pady=5)

    title_label = ctk.CTkLabel(admin_window, text=LANGUAGES[current_lang]["manage_users"], font=("Segoe UI", 20, "bold"))
    title_label.pack(pady=(60, 20))

    user_listbox = tk.Listbox(admin_window, height=15, width=50, font=("Segoe UI", 12))
    user_listbox.pack(pady=10)

    def load_users():
        user_listbox.delete(0, tk.END)
        try:
            database_root = initialize_firebase()
            users = database_root.child("ground_station/user").get() or {}
        except Exception as exc:
            print(f"[ERROR] Admin user list failed: {type(exc).__name__}")
            user_listbox.insert(
                tk.END,
                LANGUAGES[current_lang]["login_db_error"],
            )
            return
        for uid in users.keys():
            user_listbox.insert(tk.END, uid)

    load_users()

    add_frame = ctk.CTkFrame(admin_window)
    add_frame.pack(pady=10)

    new_username_entry = ctk.CTkEntry(add_frame, placeholder_text=LANGUAGES[current_lang]["new_username"])
    new_username_entry.pack(side="left", padx=5)

    new_password_entry = ctk.CTkEntry(add_frame, placeholder_text=LANGUAGES[current_lang]["new_password"], show="*")
    new_password_entry.pack(side="left", padx=5)

    def add_user():
        username = new_username_entry.get().strip()
        password = new_password_entry.get().strip()
        if username and password:
            rt_db.child("ground_station/user").child(username).set({
                "username": username,
                "password": password,
                "role": "user"
            })
            messagebox.showinfo("成功", f"已新增使用者 {username}")
            load_users()
            new_username_entry.delete(0, tk.END)
            new_password_entry.delete(0, tk.END)
        else:
            messagebox.showerror("錯誤", "請輸入完整資訊")

    add_btn = ctk.CTkButton(add_frame, text=LANGUAGES[current_lang]["add_user"], command=add_user)
    add_btn.pack(side="left", padx=5)

    def delete_user():
        selected = user_listbox.curselection()
        if selected:
            username = user_listbox.get(selected[0])
            if messagebox.askyesno("確認", LANGUAGES[current_lang]["confirm_delete"].format(username)):
                rt_db.child("ground_station/user").child(username).delete()
                messagebox.showinfo("成功", f"已刪除 {username}")
                load_users()

    delete_btn = ctk.CTkButton(admin_window, text=LANGUAGES[current_lang]["delete_user"], command=delete_user, fg_color="red")
    delete_btn.pack(pady=20)

    def switch_lang_admin():
        toggle_language()
        admin_window.title(LANGUAGES[current_lang]["admin_panel_title"])
        title_label.configure(text=LANGUAGES[current_lang]["manage_users"])
        new_username_entry.configure(placeholder_text=LANGUAGES[current_lang]["new_username"])
        new_password_entry.configure(placeholder_text=LANGUAGES[current_lang]["new_password"])
        add_btn.configure(text=LANGUAGES[current_lang]["add_user"])
        delete_btn.configure(text=LANGUAGES[current_lang]["delete_user"])
        lang_btn.configure(text=LANGUAGES[current_lang]["lang_btn"])
        logout_btn.configure(text=LANGUAGES[current_lang]["logout_btn"])

    lang_btn.configure(command=switch_lang_admin)

    admin_window.mainloop()


def show_main_window():
    global root, left_title, scroll_frame, map_widget
    global drone_status_label, fb_status_label, reload_button, lang_button
    global drone_title_label, fb_title_label, logout_btn
    global mqtt_status_label, mqtt_title_label, mqtt_connect_btn_widget
    global onboard_rescue_title_label, onboard_rescue_status_label

    root = ctk.CTk()
    root.title(
        f"[LOCAL DEMO] {LANGUAGES[current_lang]['title']}"
        if DEMO_SCREENSHOT_MODE
        else LANGUAGES[current_lang]["title"]
    )
    root.geometry("1500x900" if DEMO_SCREENSHOT_MODE else "1400x850")
    root.minsize(1200, 700)
    root.configure(fg_color="#0d1b2a")

    if DEMO_SCREENSHOT_MODE:
        demo_banner = ctk.CTkFrame(
            root,
            height=58,
            corner_radius=0,
            fg_color="#7f1d1d",
        )
        demo_banner.pack(side="top", fill="x")
        demo_banner.pack_propagate(False)
        ctk.CTkLabel(
            demo_banner,
            text=f"{DEMO_BANNER}    |    {DEMO_DATA_NOTICE}",
            font=("Segoe UI", 16, "bold"),
            text_color="#ffffff",
        ).pack(expand=True)


    left_frame = ctk.CTkFrame(
        root,
        width=430 if DEMO_SCREENSHOT_MODE else 380,
        corner_radius=0,
        fg_color="#1b263b",
    )
    left_frame.pack(side="left", fill="y")
    left_frame.pack_propagate(False)

    left_title = ctk.CTkLabel(left_frame, text="", font=("Segoe UI", 18, "bold"))
    left_title.pack(pady=(20, 10))

    scroll_frame = ctk.CTkScrollableFrame(left_frame, fg_color="transparent")
    scroll_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    global mission_cards
    mission_cards = []


    right_frame = ctk.CTkFrame(root, width=350, corner_radius=0, fg_color="#1b263b")
    right_frame.pack(side="right", fill="y")
    right_frame.pack_propagate(False)


    center_frame = ctk.CTkFrame(root, fg_color="#0d1b2a")
    center_frame.pack(side="left", fill="both", expand=True)

    if DEMO_SCREENSHOT_MODE:
        map_widget = OfflineDemoMap(center_frame)
    else:
        map_widget = TkinterMapView(
            center_frame,
            width=800,
            height=800,
            corner_radius=0,
        )
    map_widget.pack(fill="both", expand=True)
    if not DEMO_SCREENSHOT_MODE:
        map_widget.set_position(22.3193, 114.1694)
        map_widget.set_zoom(12)


    tools_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
    tools_frame.pack(anchor="ne", padx=15, pady=15)

    lang_button = ctk.CTkButton(
        tools_frame,
        text=LANGUAGES[current_lang]["lang_btn"],
        width=140,
        height=32,
        fg_color="transparent",
        hover_color="#3a3a3a",
        border_width=1,
        border_color="#555555"
    )
    lang_button.pack(anchor="e")

    logout_btn = ctk.CTkButton(
        tools_frame,
        text=LANGUAGES[current_lang]["logout_btn"],
        width=140,
        height=32,
        fg_color="red",
        hover_color="#c62828"
    )
    logout_btn.pack(anchor="e", pady=5)


    mqtt_panel = ctk.CTkFrame(right_frame)
    mqtt_panel.pack(fill="x", padx=15, pady=10)

    mqtt_title_label = ctk.CTkLabel(mqtt_panel, text="", font=("Segoe UI", 16, "bold"))
    mqtt_title_label.pack(anchor="w", pady=5)

    mqtt_status_label = ctk.CTkLabel(mqtt_panel, text="", font=("Segoe UI", 14), text_color="orange")
    mqtt_status_label.pack(anchor="w", pady=5)

    mqtt_broker_label = ctk.CTkLabel(
        mqtt_panel,
        text=mqtt_endpoint_label(),
        font=("Segoe UI", 11),
        text_color="#888"
    )
    mqtt_broker_label.pack(anchor="w", pady=(0, 5))

    mqtt_connect_btn_widget = ctk.CTkButton(
        mqtt_panel,
        text=LANGUAGES[current_lang]["mqtt_connect_btn"],
        command=connect_mqtt
    )
    mqtt_connect_btn_widget.pack(pady=10, fill="x")


    drone_panel = ctk.CTkFrame(right_frame)
    drone_panel.pack(fill="x", padx=15, pady=10)

    drone_title_label = ctk.CTkLabel(drone_panel, text="", font=("Segoe UI", 16, "bold"))
    drone_title_label.pack(anchor="w", pady=5)

    drone_status_label = ctk.CTkLabel(
        drone_panel,
        text="",
        font=("Segoe UI", 12),
        text_color="orange",
        justify="left",
        wraplength=310,
    )
    drone_status_label.pack(anchor="w", pady=8)


    onboard_panel = ctk.CTkFrame(right_frame, border_width=1, border_color="#1976D2")
    onboard_panel.pack(fill="x", padx=15, pady=10)

    onboard_rescue_title_label = ctk.CTkLabel(
        onboard_panel,
        text=LANGUAGES[current_lang]["onboard_rescue_title"],
        font=("Segoe UI", 16, "bold"),
        text_color="#64B5F6",
    )
    onboard_rescue_title_label.pack(anchor="w", padx=10, pady=(8, 4))

    onboard_rescue_status_label = ctk.CTkLabel(
        onboard_panel,
        text=LANGUAGES[current_lang]["onboard_rescue_waiting"],
        font=("Consolas", 10),
        text_color="#bbbbbb",
        justify="left",
        wraplength=300,
    )
    onboard_rescue_status_label.pack(anchor="w", padx=10, pady=(0, 8))


    fb_panel = ctk.CTkFrame(right_frame)
    fb_panel.pack(fill="x", padx=15, pady=10)

    fb_title_label = ctk.CTkLabel(fb_panel, text="", font=("Segoe UI", 16, "bold"))
    fb_title_label.pack(anchor="w", pady=5)

    fb_status_label = ctk.CTkLabel(fb_panel, text="", font=("Segoe UI", 14), text_color="spring green")
    fb_status_label.pack(anchor="w", pady=5)

    reload_button = ctk.CTkButton(fb_panel, text=LANGUAGES[current_lang]["reload_btn"], command=refresh_data)
    reload_button.pack(pady=12, fill="x")


    global sos_title_label, sos_subtitle_label
    global sos_lat_label_widget, sos_lon_label_widget, sos_radius_label_widget
    global sos_status_label, sos_spiral_btn_widget, sos_grid_btn_widget

    lang = LANGUAGES[current_lang]

    sos_panel = ctk.CTkFrame(right_frame, fg_color="#3a1f1f", border_width=1, border_color="#E53935")
    sos_panel.pack(fill="x", padx=15, pady=10)

    sos_title_label = ctk.CTkLabel(
        sos_panel, text=lang["sos_title"], font=("Segoe UI", 16, "bold"),
        text_color="#FF6B6B",
    )
    sos_title_label.pack(anchor="w", padx=10, pady=(8, 4))

    sos_subtitle_label = ctk.CTkLabel(
        sos_panel, text=lang["sos_subtitle"],
        font=("Segoe UI", 11), text_color="#cccccc",
    )
    sos_subtitle_label.pack(anchor="w", padx=10)

    sos_input_frame = ctk.CTkFrame(sos_panel, fg_color="transparent")
    sos_input_frame.pack(fill="x", padx=10, pady=(6, 4))

    sos_lat_label_widget = ctk.CTkLabel(sos_input_frame, text=lang["sos_lat_label"], width=50)
    sos_lat_label_widget.grid(row=0, column=0, padx=2)
    sos_lat_entry = ctk.CTkEntry(sos_input_frame, width=110)
    sos_lat_entry.insert(0, "HIDDEN" if DEMO_SCREENSHOT_MODE else "22.352")
    sos_lat_entry.grid(row=0, column=1, padx=2)

    sos_lon_label_widget = ctk.CTkLabel(sos_input_frame, text=lang["sos_lon_label"], width=50)
    sos_lon_label_widget.grid(row=0, column=2, padx=2)
    sos_lon_entry = ctk.CTkEntry(sos_input_frame, width=110)
    sos_lon_entry.insert(0, "HIDDEN" if DEMO_SCREENSHOT_MODE else "114.183")
    sos_lon_entry.grid(row=0, column=3, padx=2)

    sos_radius_label_widget = ctk.CTkLabel(
        sos_input_frame, text=lang["sos_radius_label"], width=90,
    )
    sos_radius_label_widget.grid(row=1, column=0, columnspan=2, pady=(6, 0))
    sos_radius_entry = ctk.CTkEntry(sos_input_frame, width=110)
    sos_radius_entry.insert(0, "N/A" if DEMO_SCREENSHOT_MODE else "200")
    sos_radius_entry.grid(row=1, column=2, columnspan=2, pady=(6, 0))

    sos_status_label = ctk.CTkLabel(
        sos_panel, text=lang["sos_status_idle"],
        font=("Segoe UI", 11), text_color="#bbbbbb",
    )
    sos_status_label.pack(anchor="w", padx=10, pady=(4, 0))

    def _run_pattern(pattern_kind):
        global sos_current_pattern
        lang_now = LANGUAGES[current_lang]
        if not _SOS_PATTERN_OK:
            messagebox.showerror("Error", lang_now["sos_module_err"])
            return
        try:
            lat_v = float(sos_lat_entry.get())
            lon_v = float(sos_lon_entry.get())
            radius = float(sos_radius_entry.get())
        except ValueError:
            messagebox.showerror("Error", lang_now["sos_invalid_input"])
            return
        try:
            if pattern_kind == "spiral":
                wps_dicts = generate_spiral(lat_v, lon_v, radius_m=radius, spacing_m=30.0)
            else:
                wps_dicts = generate_grid(lat_v, lon_v, width_m=2 * radius, spacing_m=40.0)
        except Exception as e:
            messagebox.showerror("Error", f"{lang_now['sos_generation_err']}: {e}")
            return
        wps_tuples = [(w["latitude"], w["longitude"]) for w in wps_dicts]
        sos_current_pattern = (pattern_kind, len(wps_tuples))
        sos_status_label.configure(
            text=(
                lang_now["sos_status_fmt"].format(pattern_kind, len(wps_tuples))
                + f"\n{lang_now['preview_only']}"
            ),
            text_color="#FFD166",
        )
        _show_waypoint_route(wps_tuples, label=f"{pattern_kind} preview")

    sos_btn_frame = ctk.CTkFrame(sos_panel, fg_color="transparent")
    sos_btn_frame.pack(fill="x", padx=10, pady=(6, 10))

    sos_spiral_btn_widget = ctk.CTkButton(
        sos_btn_frame, text=lang["sos_spiral_btn"], fg_color="#E53935",
        hover_color="#C62828", command=lambda: _run_pattern("spiral"),
    )
    sos_spiral_btn_widget.pack(side="left", expand=True, fill="x", padx=(0, 4))

    sos_grid_btn_widget = ctk.CTkButton(
        sos_btn_frame, text=lang["sos_grid_btn"], fg_color="#E53935",
        hover_color="#C62828", command=lambda: _run_pattern("grid"),
    )
    sos_grid_btn_widget.pack(side="left", expand=True, fill="x", padx=(4, 0))

    if DEMO_SCREENSHOT_MODE:


        sos_input_frame.pack_forget()
        sos_btn_frame.pack_forget()


    def switch_lang_main():
        toggle_language()
        update_language()
        refresh_data()
        logout_btn.configure(text=LANGUAGES[current_lang]["logout_btn"])

    lang_button.configure(command=switch_lang_main)
    logout_btn.configure(command=lambda: logout(root))


    def on_closing():
        _cancel_periodic_refresh()
        disconnect_mqtt()
        if root and root.winfo_exists():
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)


    update_language()
    if DEMO_SCREENSHOT_MODE:
        root.title(f"[LOCAL DEMO] Rescue Drone Ground Station - v{APP_VERSION}")
        safe_configure(
            mqtt_status_label,
            text="Disabled — no MQTT connection",
            text_color="#fbbf24",
        )
        safe_configure(
            mqtt_connect_btn_widget,
            text="MQTT disabled in local demo",
            state="disabled",
        )
        safe_configure(
            drone_status_label,
            text=build_demo_drone_status(),
            text_color="#fbbf24",
        )
        safe_configure(
            onboard_rescue_status_label,
            text=build_demo_onboard_summary(),
            text_color="#7dd3fc",
        )
        safe_configure(
            reload_button,
            text="Reload local demo data",
        )
        safe_configure(
            lang_button,
            text="English demo",
            state="disabled",
        )
        safe_configure(
            logout_btn,
            text="Offline demo",
            state="disabled",
        )
        safe_configure(
            sos_status_label,
            text=f"{DEMO_LOCATION_LABEL}\n19 synthetic search points",
            text_color="#fbbf24",
        )
        for entry in (sos_lat_entry, sos_lon_entry, sos_radius_entry):
            safe_configure(entry, state="disabled")
        safe_configure(
            sos_spiral_btn_widget,
            text="Pattern execution disabled",
            state="disabled",
        )
        safe_configure(
            sos_grid_btn_widget,
            text="No physical dispatch",
            state="disabled",
        )
    refresh_data()
    if not DEMO_SCREENSHOT_MODE:
        _schedule_periodic_refresh()

    print("主介面已載入")
    root.mainloop()

def main():

    global current_lang
    if DEMO_SCREENSHOT_MODE:
        current_lang = "en"
        show_main_window()
    elif (
        not getattr(sys, "frozen", False)
        and os.environ.get("GS_BYPASS_LOGIN", "").strip() == "1"
    ):
        show_main_window()
    else:
        show_login_page()


if __name__ == "__main__":
    main()
