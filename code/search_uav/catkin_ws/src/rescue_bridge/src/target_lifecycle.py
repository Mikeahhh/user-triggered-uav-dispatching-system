import math
from execution_protocol import EXECUTION_ID_RE
from mission_protocol import MISSION_ID_RE


class TargetLifecycle:
    def __init__(self):
        self.history = {}
        self.released = set()
        self.locked = None
        self.current = None
        self.active = False

    def offer(self, target, ready, reason=""):
        try:
            valid = (isinstance(target["execution_id"], str) and EXECUTION_ID_RE.fullmatch(target["execution_id"])
                     and isinstance(target["mission_id"], str) and MISSION_ID_RE.fullmatch(target["mission_id"])
                     and type(target["waypoint_index"]) is int and 0 <= target["waypoint_index"] <= 100000
                     and all(not isinstance(target[key], bool) and math.isfinite(target[key])
                             for key in ("latitude", "longitude", "altitude"))
                     and -90 <= target["latitude"] <= 90 and -180 <= target["longitude"] <= 180
                     and target["altitude"] > 0)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            return "REJECTED", "INVALID_TARGET", False
        eid = target["execution_id"]
        owner = (target["mission_id"], eid)
        key = (eid, target["waypoint_index"])
        if eid in self.released:
            return "REJECTED", "EXECUTION_RELEASED", False
        if self.locked and self.locked != owner:
            return "REJECTED", "EXECUTION_LOCKED", False
        if key in self.history:
            old, arrived = self.history[key]
            if old != target:
                return "REJECTED", "TARGET_CONTENT_CONFLICT", False
            if not ready:
                return "REJECTED", reason, False
            return ("ARRIVED" if arrived else "ACCEPTED"), "DUPLICATE", False
        if self.active:
            return "REJECTED", "TARGET_BUSY", False
        expected = 0 if self.locked is None else self.current[1] + 1
        if target["waypoint_index"] != expected:
            return "REJECTED", "WAYPOINT_SEQUENCE_MISMATCH", False
        if not ready:
            return "REJECTED", reason, False
        self.locked = owner
        self.current = key
        self.history[key] = (dict(target), False)
        self.active = True
        return "ACCEPTED", "", True

    def arrived(self, distance_xy, threshold):
        if not self.active or not math.isfinite(distance_xy) or distance_xy < 0 or not math.isfinite(threshold) or not 0 < threshold or distance_xy >= threshold:
            return False
        target, _ = self.history[self.current]
        self.history[self.current] = (target, True)
        self.active = False
        return True

    def arrived3d(self, dx, dy, dz, horizontal, vertical):
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in (dx, dy, dz, horizontal, vertical)) or vertical <= 0 or abs(dz) >= vertical:
            return False
        return self.arrived(math.hypot(dx, dy), horizontal)

    def control(self, mission_id, execution_id, release=False):
        if not execution_id or (self.locked and self.locked != (mission_id, execution_id)):
            return False
        self.active = False
        if not release:
            self.released.add(execution_id)
        if release:
            self.released.add(execution_id)
            self.locked = None
        return True

    def current_target(self):
        return dict(self.history[self.current][0])
