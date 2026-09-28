import copy
import hashlib
import json
import re

from rescue_repository import StateConflict, user_transaction
from rescue_event_manager import require_sos_verification

SELECTED = "SELECTED"
PREPARED = "PREPARED"
CONFIRMING = "CONFIRMING"
AUTHORIZED = "AUTHORIZED"
RECOVERY_ONLY = "RECOVERY_ONLY"


def current_event(user, user_id, event_id):
    event = user.get("rescue_events", {}).get(event_id)
    if (not isinstance(event, dict) or event.get("user_id") != user_id
            or event.get("event_id") != event_id or event.get("status") != "PENDING"):
        raise StateConflict("event changed or is no longer PENDING")
    require_sos_verification(event)
    return event


def source_fingerprint(user, event):
    kind, record_id = event.get("primary_record_type"), event.get("primary_record_id")
    group = user.get(kind) if isinstance(kind, str) else None
    record = group.get(record_id) if isinstance(group, dict) and isinstance(record_id, str) else None
    if not isinstance(record, dict):
        raise StateConflict("primary record is unavailable")
    semantics = {"user_id": event.get("user_id"), "event_id": event.get("event_id"),
                 "trigger_type": event.get("trigger_type"), "primary_record_type": kind,
                 "primary_record_id": record_id}
    if kind == "booked_events":
        semantics["booking_episode_end_ms"] = event.get("primary_expected_end_at_ms", event.get("abnormal_since_ms"))
    return hashlib.sha256(json.dumps([semantics, record], sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_quick_start_quarantine(user, event):


    if event.get("primary_record_type") != "QuickStartSessions":
        return
    monitors = user.get("quick_start_monitoring", {})
    if not isinstance(monitors, dict):
        raise StateConflict("Quick Start monitoring must be an object")
    session_id = event.get("primary_record_id")
    monitor = monitors.get(session_id, {})
    if not isinstance(monitor, dict):
        raise StateConflict("Quick Start session monitoring must be an object")
    rejected = monitor.get("rejected_future_samples", [])
    if (not isinstance(rejected, list)
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in rejected)):
        raise StateConflict("Quick Start future-sample quarantine is invalid")
    if not rejected:
        return
    sessions = user.get("QuickStartSessions", {})
    session = sessions.get(session_id) if isinstance(sessions, dict) else None
    if not isinstance(session, dict):
        raise StateConflict("Quick Start primary record is unavailable")
    from quick_start_freshness import sample_identity
    from rescue_event_manager import valid_gps_points


    points, _ = valid_gps_points(session.get("points"))
    rejected = set(rejected)
    if any(sample_identity(point) in rejected for point in points):
        raise StateConflict("Quick Start primary record contains quarantined future GPS points; review the source record")


def active_entry(user, user_id, event_id, owner_id=None, selection_id=None):
    current_event(user, user_id, event_id)
    entries = user.get("active_events", {})
    entry = entries.get(event_id) if isinstance(entries, dict) else None
    if (not isinstance(entry, dict) or entry.get("user_id") != user_id
            or entry.get("event_id") != event_id):
        raise StateConflict("select this event into the active-event queue first")
    if owner_id is not None and entry.get("owner_id") != owner_id:
        raise StateConflict("this event is being processed by another workstation")
    if selection_id is not None and entry.get("selection_id") != selection_id:
        raise StateConflict("selection changed; reload the active-event queue")
    return entry


def _without_execution(user, event_id, entry):
    if user["rescue_events"][event_id].get("execution") or entry.get("execution_id"):
        raise StateConflict("an authorized execution cannot be cancelled or replaced")


def select_event(database_root, user_id, event_id, owner_id, selection_id, now_ms):
    def select(user):
        event = current_event(user, user_id, event_id)
        if event.get("execution"):
            raise StateConflict("existing execution requires recovery, not a new selection")
        entries = user.setdefault("active_events", {})
        if not isinstance(entries, dict):
            raise StateConflict("active_events must be an object")
        if event_id in entries:
            existing = active_entry(user, user_id, event_id, owner_id)
            if existing.get("selection_id") == selection_id:
                return user
            raise StateConflict("event is already selected; use its active queue entry")
        entries[event_id] = {"user_id": user_id, "event_id": event_id,
                             "selection_id": selection_id, "owner_id": owner_id,
                             "selected_at_ms": now_ms, "phase": SELECTED, "version": 1}
        return user
    return user_transaction(database_root, user_id, select)["active_events"][event_id]


def save_preparation(database_root, user_id, event_id, owner_id, selection_id,
                     version, prepared, source_hash, now_ms):
    def prepare(user):
        entry = active_entry(user, user_id, event_id, owner_id, selection_id)
        _without_execution(user, event_id, entry)
        if entry.get("phase") not in {SELECTED, PREPARED} or entry.get("version") != version:
            raise StateConflict("processing version changed; prepare the current selection again")
        if source_fingerprint(user, user["rescue_events"][event_id]) != source_hash:
            raise StateConflict("primary record changed while preparing; prepare it again")
        entry.update({"phase": PREPARED, "version": version + 1,
                      "prepared": copy.deepcopy(prepared), "source_hash": source_hash,
                      "prepared_at_ms": now_ms})
        return user
    return user_transaction(database_root, user_id, prepare)["active_events"][event_id]


def begin_confirmation(database_root, user_id, event_id, owner_id, selection_id,
                       version, confirmation_id):
    def begin(user):
        entry = active_entry(user, user_id, event_id, owner_id, selection_id)
        _without_execution(user, event_id, entry)
        if entry.get("phase") != PREPARED or entry.get("version") != version:
            raise StateConflict("mission is not the current prepared version")
        if source_fingerprint(user, user["rescue_events"][event_id]) != entry.get("source_hash"):
            raise StateConflict("primary record changed; prepare and review it again")
        validate_quick_start_quarantine(user, user["rescue_events"][event_id])
        entry.update({"phase": CONFIRMING, "confirmation_id": confirmation_id, "version": version + 1})
        return user
    return user_transaction(database_root, user_id, begin)["active_events"][event_id]


def return_to_pending(database_root, user_id, event_id, owner_id, selection_id,
                      version, *, confirmation_id=None):
    def release(user):
        entry = active_entry(user, user_id, event_id, owner_id, selection_id)
        _without_execution(user, event_id, entry)
        if entry.get("version") != version:
            raise StateConflict("processing version changed; cancellation is stale")
        allowed = entry.get("phase") in {SELECTED, PREPARED}
        if confirmation_id is not None:
            allowed = (entry.get("phase") == CONFIRMING
                       and entry.get("confirmation_id") == confirmation_id)
        if not allowed:
            raise StateConflict("confirmation or execution is in progress; use its recovery action")
        del user["active_events"][event_id]

        return user
    return user_transaction(database_root, user_id, release)


def recover_confirmation(database_root, user_id, event_id, owner_id, selection_id, version):

    def recover(user):
        entry = active_entry(user, user_id, event_id, owner_id, selection_id)
        _without_execution(user, event_id, entry)
        if entry.get("phase") != CONFIRMING or entry.get("version") != version:
            raise StateConflict("confirmation changed; reload before recovery")
        entry.update({"phase": PREPARED, "version": version + 1})
        entry.pop("confirmation_id", None)
        return user
    return user_transaction(database_root, user_id, recover)["active_events"][event_id]


def migrate_execution_to_active(database_root, user_id, event_id, owner_id, intent, now_ms):

    def migrate(user):
        event = current_event(user, user_id, event_id)
        entries = user.setdefault("active_events", {})
        if not isinstance(entries, dict):
            raise StateConflict("active_events must be an object")
        if event_id in entries:
            return user
        remote = event.get("execution")
        execution_id = remote.get("execution_id") if isinstance(remote, dict) else intent.get("execution_id") if intent else None
        if not execution_id:
            raise StateConflict("no existing execution to recover")
        if remote and intent and remote.get("execution_id") != intent.get("execution_id"):
            raise StateConflict("local/cloud execution conflict")
        entries[event_id] = {"user_id": user_id, "event_id": event_id,
                             "selection_id": "recovery-" + execution_id, "owner_id": owner_id,
                             "selected_at_ms": now_ms, "phase": RECOVERY_ONLY, "version": 1,
                             "execution_id": execution_id}
        return user
    return user_transaction(database_root, user_id, migrate)["active_events"][event_id]
