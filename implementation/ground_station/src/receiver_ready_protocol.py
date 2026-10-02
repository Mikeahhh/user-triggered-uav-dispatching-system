import json
import re
import threading
from collections import OrderedDict

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
READY_ID = re.compile(r"^[0-9a-f]{32}$")


def validate_receiver_ready(payload, retained=False):
    if retained or not isinstance(payload, bytes) or not 0 < len(payload) <= 512:
        raise ValueError("invalid or retained receiver readiness message")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate readiness field")
            result[key] = value
        return result
    data = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    if not isinstance(data, dict) or set(data) != {"schema_version", "message_type", "receiver_id", "ready_id"}:
        raise ValueError("invalid readiness fields")
    if (type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["message_type"] != "RECEIVER_READY"
            or not isinstance(data["receiver_id"], str) or not TOKEN.fullmatch(data["receiver_id"])
            or not isinstance(data["ready_id"], str) or not READY_ID.fullmatch(data["ready_id"])):
        raise ValueError("invalid readiness values")
    return data["receiver_id"], data["ready_id"]


class ReceiverReadyGate:
    def __init__(self, limit=256, in_flight_limit=32):
        self.limit = limit
        self.in_flight_limit = in_flight_limit
        self.successes = OrderedDict()
        self.in_flight = set()
        self.lock = threading.Lock()

    def begin(self, payload, retained=False):
        key = validate_receiver_ready(payload, retained)
        with self.lock:
            if key in self.successes or key in self.in_flight or len(self.in_flight) >= self.in_flight_limit:
                return None
            self.in_flight.add(key)
        return key

    def finish(self, key, success):
        with self.lock:
            self.in_flight.discard(key)
            if success:
                self.successes[key] = True
                self.successes.move_to_end(key)
                while len(self.successes) > self.limit:
                    self.successes.popitem(last=False)
