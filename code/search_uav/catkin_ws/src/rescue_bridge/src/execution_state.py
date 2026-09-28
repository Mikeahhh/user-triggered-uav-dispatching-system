import copy
import errno
import fcntl
import json
import math
import os
import tempfile
import threading
import time
import uuid

from collection_context import build_collection_context


class ExecutionError(ValueError):
    pass


class ExecutionManager:
    def __init__(self, journal_path="", clock=time.monotonic):
        self.path = os.path.expanduser(journal_path) if journal_path else ""
        self.clock = clock
        self.lock = threading.RLock()
        self.records = {}
        self._lock_fd = None
        self._storage_failed = False
        self._closed = False
        self.owner = {'pid': os.getpid(), 'instance_id': uuid.uuid4().hex}
        self.active_id = ""
        self.queue = []
        self.hover_deadline = None
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), mode=0o700, exist_ok=True)
            self._lock_fd = os.open(self.path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(self._lock_fd)
                self._lock_fd = None
                raise ExecutionError("execution journal already owned by another bridge") from exc
            try:
                owner_bytes = json.dumps(self.owner, sort_keys=True).encode('utf-8')
                os.fchmod(self._lock_fd, 0o600)
                os.ftruncate(self._lock_fd, 0)
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
                while owner_bytes:
                    written = os.write(self._lock_fd, owner_bytes)
                    if written <= 0:
                        raise OSError('could not persist execution owner')
                    owner_bytes = owner_bytes[written:]
                os.fsync(self._lock_fd)
            except Exception:
                self.close()
                raise
        if self.path and os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as handle:
                saved = json.load(handle)
            if saved.get("journal_version") != 1 or not isinstance(saved.get("executions"), dict):
                raise ExecutionError("invalid execution journal; operator recovery required")
            self.records = saved["executions"]
            self.active_id = saved.get("active_execution_id", "")
            if self.active_id:
                if self.active_id not in self.records:
                    raise ExecutionError("journal active execution missing")
                self._commit(self.active_id, phase="RECOVERY_REQUIRED",
                             reason="process restarted; no automatic goal replay")

    def _write(self, records, active_id):
        if self._storage_failed or self._closed:
            raise ExecutionError("execution storage failed; operator recovery required")
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        temporary = None
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".execution.", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"journal_version": 1, "active_execution_id": active_id,
                           "owner": self.owner,
                           "collection_context": build_collection_context(
                               records.get(active_id), active_id, self.owner['instance_id']),
                           "executions": records}, handle, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                try:
                    os.fsync(directory_fd)
                except OSError as exc:
                    if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                        raise
            finally:
                os.close(directory_fd)
        except Exception:
            self._storage_failed = True


            self.close()
            raise
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def _commit(self, execution_id, **updates):
        changed = copy.deepcopy(self.records)
        changed.setdefault(execution_id, {}).update(updates)
        self._write(changed, self.active_id)
        self.records = changed
        return self.snapshot(execution_id)

    def snapshot(self, execution_id=None):
        with self.lock:
            return copy.deepcopy(self.records.get(execution_id or self.active_id, {}))

    def collection_context(self):
        with self.lock:
            context = build_collection_context(self.records.get(self.active_id), self.active_id,
                                               self.owner['instance_id'])
            if self._closed or self._storage_failed:
                context['collection_ready'] = False
            return context

    def invalidate_collection_controller(self):

        with self.lock:
            current = self.snapshot()
            if current and current.get('collection_controller_valid') is True:
                return self._commit(self.active_id, collection_controller_valid=False)
            return None

    def admit(self, mission, launch_fix=None, *, hover_seconds=5.0):
        with self.lock:
            if self._storage_failed or self._closed:
                raise ExecutionError("execution storage failed; operator recovery required")
            eid = mission["execution_id"]
            if eid in self.records:
                known = self.records[eid]
                if known["content_fingerprint"] != mission["content_fingerprint"]:
                    raise ExecutionError("EXECUTION_CONFLICT")
                return "DUPLICATE", self.snapshot(eid)
            if self.active_id:
                raise ExecutionError("MISSION_BUSY")
            queue = list(mission["waypoints"])
            if mission["return_to_launch"]:
                if launch_fix is None:
                    raise ExecutionError("RTL_FIX_UNAVAILABLE")
                lat, lon = launch_fix
                if not (not isinstance(lat, bool) and not isinstance(lon, bool)
                        and isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                        and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ExecutionError("RTL_FIX_INVALID")
                queue.append((lat, lon))
            hover = mission["hover_seconds"] if mission["hover_seconds"] is not None else hover_seconds
            if isinstance(hover, bool) or not isinstance(hover, (int, float)) or not math.isfinite(hover) or not 0 <= hover <= 600:
                raise ExecutionError("invalid effective hover_seconds")
            record = {
                "schema_version": 2, "mission_id": mission["mission_id"], "execution_id": eid,
                "mission_type": mission["mission_type"], "content_fingerprint": mission["content_fingerprint"],
                "legacy": mission["legacy"], "phase": "WAITING_TARGET_ACCEPTANCE", "reason": "",
                "waypoint_index": 0, "source_waypoint_total": len(mission["waypoints"]),
                "waypoint_total": len(queue), "queue_remaining": len(queue),
                "rtl_requested": mission["return_to_launch"], "rtl_appended": mission["return_to_launch"],
                "altitude": mission["altitude"], "hover_seconds": float(hover),
                "land_command_requested": False, "touchdown_confirmed": False,
                "search_area_arrival_observed": False, "collection_controller_valid": True,
            }
            records = copy.deepcopy(self.records)
            records[eid] = record
            self._write(records, eid)
            self.records, self.active_id, self.queue = records, eid, queue
            return "NEW", self.snapshot()

    def command(self, *, include_navigating=False):
        with self.lock:
            if self._storage_failed or self._closed:
                return None
            record = self.snapshot()
            if not record or record["phase"] not in (("WAITING_TARGET_ACCEPTANCE", "NAVIGATING") if include_navigating else ("WAITING_TARGET_ACCEPTANCE",)) or not self.queue:
                return None
            index = record["waypoint_index"]
            lat, lon = self.queue[index]
            return dict(mission_id=record["mission_id"], execution_id=self.active_id,
                        waypoint_index=index, latitude=lat, longitude=lon,
                        altitude=record["altitude"])

    def feedback(self, mission_id, execution_id, waypoint_index, status, reason="", *, runtime_profile=None):
        with self.lock:
            current = self.snapshot()
            if (not current or not execution_id or execution_id != self.active_id
                    or mission_id != current["mission_id"] or type(waypoint_index) is not int
                    or waypoint_index != current["waypoint_index"]):
                return None
            if status == "ACCEPTED" and current["phase"] == "WAITING_TARGET_ACCEPTANCE":
                details = {}
                if runtime_profile is not None:
                    details["runtime_profile"] = copy.deepcopy(runtime_profile)
                return self._commit(execution_id, phase="NAVIGATING", reason="", **details)
            if status == "REJECTED" and current["phase"] == "WAITING_TARGET_ACCEPTANCE":
                return self._commit(execution_id, phase="TARGET_REJECTED", reason=reason)
            if status == "ARRIVED" and current["phase"] == "NAVIGATING":
                result = self._commit(execution_id, phase="HOVERING", reason="",
                                      search_area_arrival_observed=(
                                          current.get('search_area_arrival_observed') is True
                                          or waypoint_index < current['source_waypoint_total']),
                                      queue_remaining=current["waypoint_total"] - waypoint_index - 1)
                self.hover_deadline = self.clock() + current["hover_seconds"]
                return result
            return None

    def acceptance_timeout(self, execution_id, waypoint_index):
        with self.lock:
            current = self.snapshot()
            if (current.get("execution_id") == execution_id
                    and current.get("waypoint_index") == waypoint_index
                    and current.get("phase") == "WAITING_TARGET_ACCEPTANCE"):
                return self._commit(execution_id, phase="TARGET_ACCEPTANCE_TIMEOUT",
                                    reason="target acceptance not confirmed; operator review required")
            return None

    def navigation_feedback_timeout(self, execution_id, waypoint_index):
        with self.lock:
            current = self.snapshot()
            if (current.get("execution_id") == execution_id
                    and current.get("waypoint_index") == waypoint_index
                    and current.get("phase") == "NAVIGATING"):
                return self._commit(execution_id, phase="NAVIGATION_FEEDBACK_TIMEOUT",
                                    reason="target tracking feedback unavailable; operator review required")
            return None

    def prepare_operator_reset(self, execution_id, reason):
        with self.lock:
            current = self.snapshot()
            if (execution_id != self.active_id or not isinstance(reason, str) or not reason.strip()
                    or current.get("phase") in ("NAVIGATING", "HOVERING", "WAITING_TARGET_ACCEPTANCE", "LAND_REQUEST_PENDING")):
                raise ExecutionError("abort or reconcile execution before reset")
            return self._commit(execution_id, phase="OPERATOR_RESET_PENDING",
                                release_previous_phase=current.get("release_previous_phase", current["phase"]),
                                reason="waiting for commander execution-lock release")

    def tick(self):
        with self.lock:
            current = self.snapshot()
            if not current or current["phase"] != "HOVERING" or self.hover_deadline is None:
                return None
            if self.clock() < self.hover_deadline:
                return None
            self.hover_deadline = None
            index = current["waypoint_index"] + 1
            if index == current["waypoint_total"]:
                return self._commit(self.active_id, phase="LAND_REQUEST_PENDING", reason="",
                                    queue_remaining=0)
            return self._commit(self.active_id, phase="WAITING_TARGET_ACCEPTANCE",
                                waypoint_index=index, queue_remaining=current["waypoint_total"] - index)

    def land_result(self, published, *, aborted=False, reason=""):
        with self.lock:
            current = self.snapshot()
            if not current:
                return None
            self.hover_deadline = None
            phase = ("ABORT_LAND_REQUESTED" if aborted else "LAND_REQUESTED") if published else "LAND_REQUEST_FAILED"
            return self._commit(self.active_id, phase=phase, reason=reason,
                                land_command_requested=bool(published), queue_remaining=0)

    def operator_reset(self, execution_id, *, confirmed=False, reason=""):
        with self.lock:
            if not confirmed or not isinstance(reason, str) or not reason.strip():
                raise ExecutionError("explicit operator confirmation and reason required")
            if not self.active_id or execution_id != self.active_id:
                raise ExecutionError("reset execution_id does not match active execution")
            current = self.snapshot()
            if current["phase"] in ("NAVIGATING", "HOVERING", "WAITING_TARGET_ACCEPTANCE", "LAND_REQUEST_PENDING"):
                raise ExecutionError("abort or reconcile execution before release")
            changed = copy.deepcopy(self.records)
            changed[execution_id].update(phase="OPERATOR_RELEASED", reason=reason.strip()[:256],
                                        release_basis="OPERATOR_CONFIRMATION", released_at=time.time(),
                                        released_from_phase=current.get("release_previous_phase", current["phase"]))
            self._write(changed, "")
            self.records, self.active_id, self.queue = changed, "", []
            self.hover_deadline = None
            return self.snapshot(execution_id)

    def close(self):
        self._closed = True
        if getattr(self, "_lock_fd", None) is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def __del__(self):
        self.close()
