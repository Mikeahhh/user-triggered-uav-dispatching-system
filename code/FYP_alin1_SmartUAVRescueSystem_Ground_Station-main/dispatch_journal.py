import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from mission_execution_protocol import task_fingerprint


class JournalConflict(ValueError):
    pass


class DispatchJournal:
    def __init__(self, path, scope):
        if not isinstance(scope, str) or not scope:
            raise ValueError("journal database scope is required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


        self._restrict_file(self.path, create=True)
        for suffix in ("-wal", "-shm"):
            companion = Path(str(self.path) + suffix)
            if companion.exists():
                self._restrict_file(companion)
        self.scope = scope
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(str(self.path), timeout=5, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS execution_intents ("
                                "scope TEXT NOT NULL,event_key TEXT NOT NULL,execution_id TEXT NOT NULL,"
                                "payload TEXT NOT NULL,fingerprint TEXT NOT NULL,state TEXT NOT NULL,"
                                "detail TEXT NOT NULL DEFAULT '',broker_confirmed_at_ms INTEGER,PRIMARY KEY(scope,event_key),"
                                "UNIQUE(scope,execution_id))")
        self.connection.execute("CREATE TABLE IF NOT EXISTS journal_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.connection.commit()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(execution_intents)")}
            if "broker_confirmed_at_ms" not in columns:
                self.connection.execute("ALTER TABLE execution_intents ADD COLUMN broker_confirmed_at_ms INTEGER")
        for suffix in ("", "-wal", "-shm"):
            companion = Path(str(self.path) + suffix)
            if companion.exists():
                self._restrict_file(companion)

    @staticmethod
    def _restrict_file(path, create=False):
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT
        descriptor = os.open(path, flags, 0o600)
        try:
            if callable(getattr(os, "fchmod", None)):
                os.fchmod(descriptor, 0o600)
            else:


                os.chmod(path, 0o600)
        finally:
            os.close(descriptor)

    def workstation_id(self):

        with self.lock, self.connection:
            self.connection.execute("INSERT OR IGNORE INTO journal_metadata (key,value) VALUES ('workstation_id',?)",
                                    (uuid.uuid4().hex,))
            return self.connection.execute("SELECT value FROM journal_metadata WHERE key='workstation_id'").fetchone()[0]

    def close(self):
        with self.lock:
            self.connection.close()

    def get(self, event_key):
        key = json.dumps(list(event_key), separators=(",", ":"))
        with self.lock:
            row = self.connection.execute(
                "SELECT execution_id,payload,fingerprint,state,detail,broker_confirmed_at_ms FROM execution_intents "
                "WHERE scope=? AND event_key=?", (self.scope, key)).fetchone()
        if row is None:
            return None
        return self._decode(row)

    @staticmethod
    def _decode(row):
        payload = json.loads(row[1])
        if row[0] != payload.get("execution_id") or row[2] != task_fingerprint(payload):
            raise JournalConflict("saved execution content is inconsistent; manual reconciliation required")
        return {"execution_id": row[0], "payload": payload,
                "fingerprint": row[2], "state": row[3], "detail": row[4],
                "broker_confirmed_at_ms": row[5]}

    def get_by_execution_id(self, execution_id):
        with self.lock:
            row = self.connection.execute(
                "SELECT execution_id,payload,fingerprint,state,detail,broker_confirmed_at_ms FROM execution_intents "
                "WHERE scope=? AND execution_id=?", (self.scope, execution_id)).fetchone()
        return None if row is None else self._decode(row)

    def prepare(self, event_key, payload):
        fingerprint = task_fingerprint(payload)
        key = json.dumps(list(event_key), separators=(",", ":"))
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO execution_intents "
                "(scope,event_key,execution_id,payload,fingerprint,state) VALUES (?,?,?,?,?,'PREPARED')",
                (self.scope, key, payload["execution_id"], encoded, fingerprint))
            current = self.get(event_key)
            if current is None or current["execution_id"] != payload["execution_id"] or current["fingerprint"] != fingerprint:
                raise JournalConflict("event already has an execution; reconcile the existing intent")
        return current

    def set_state(self, event_key, execution_id, state, detail=""):
        if state not in {"PREPARED", "CLAIMED", "PUBLISHING", "BROKER_CONFIRMED", "COMMITTED", "UNKNOWN", "CANCELLED"}:
            raise ValueError("invalid journal state")
        with self.lock, self.connection:


            self.connection.execute("BEGIN IMMEDIATE")
            current = self.get(event_key)
            if current is None or current["execution_id"] != execution_id:
                raise JournalConflict("execution identity mismatch")
            if current["state"] == "COMMITTED" and state != "COMMITTED":
                raise JournalConflict("cannot downgrade a committed execution")
            if current["state"] == "BROKER_CONFIRMED" and state not in {"BROKER_CONFIRMED", "COMMITTED"}:
                raise JournalConflict("cannot downgrade a broker-confirmed execution")
            confirmed_at = current["broker_confirmed_at_ms"]
            if state == "BROKER_CONFIRMED" and confirmed_at is None:
                confirmed_at = int(time.time() * 1000)
            self.connection.execute(
                "UPDATE execution_intents SET state=?,detail=?,broker_confirmed_at_ms=? WHERE scope=? AND execution_id=?",
                (state, detail, confirmed_at, self.scope, execution_id))
        return self.get(event_key)
