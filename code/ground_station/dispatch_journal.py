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
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(execution_intents)")}
            for name, kind in (("attempt", "INTEGER NOT NULL DEFAULT 1"), ("admission", "TEXT"),
                               ("protocol_version", "INTEGER NOT NULL DEFAULT 0"), ("legacy_state", "TEXT"), ("legacy_detail", "TEXT")):
                if name not in columns:
                    self.connection.execute("ALTER TABLE execution_intents ADD COLUMN " + name + " " + kind)
            self.connection.execute("UPDATE execution_intents SET legacy_state=COALESCE(legacy_state,state),legacy_detail=COALESCE(legacy_detail,detail),state='UNKNOWN',detail='Legacy broker confirmation requires UAV evidence' "
                                    "WHERE protocol_version=0 AND state IN ('BROKER_CONFIRMED','COMMITTED')")
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
        self.expire_admissions()
        key = json.dumps(list(event_key), separators=(",", ":"))
        with self.lock:
            row = self.connection.execute(
                "SELECT execution_id,payload,fingerprint,state,detail,broker_confirmed_at_ms,attempt,admission,protocol_version,legacy_state,legacy_detail FROM execution_intents "
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
                "broker_confirmed_at_ms": row[5], "attempt": row[6],
                "admission": json.loads(row[7]) if row[7] else None, "protocol_version": row[8],
                "legacy_state": row[9], "legacy_detail": row[10]}

    def get_by_execution_id(self, execution_id):
        self.expire_admissions()
        with self.lock:
            row = self.connection.execute(
                "SELECT execution_id,payload,fingerprint,state,detail,broker_confirmed_at_ms,attempt,admission,protocol_version,legacy_state,legacy_detail FROM execution_intents "
                "WHERE scope=? AND execution_id=?", (self.scope, execution_id)).fetchone()
        return None if row is None else self._decode(row)

    def prepare(self, event_key, payload):
        fingerprint = task_fingerprint(payload)
        key = json.dumps(list(event_key), separators=(",", ":"))
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO execution_intents "
                "(scope,event_key,execution_id,payload,fingerprint,state,protocol_version) VALUES (?,?,?,?,?,'PREPARED',1)",
                (self.scope, key, payload["execution_id"], encoded, fingerprint))
            current = self.get(event_key)
            if current is None or current["execution_id"] != payload["execution_id"] or current["fingerprint"] != fingerprint:
                raise JournalConflict("event already has an execution; reconcile the existing intent")
        return current

    def set_state(self, event_key, execution_id, state, detail=""):
        if state not in {"PREPARED", "CLAIMED", "PUBLISHING", "AWAITING_ACCEPTANCE", "ACCEPTED", "REJECTED", "BROKER_CONFIRMED", "COMMITTED", "UNKNOWN", "CANCELLED"}:
            raise ValueError("invalid journal state")
        with self.lock, self.connection:


            self.connection.execute("BEGIN IMMEDIATE")
            current = self.get(event_key)
            if current is None or current["execution_id"] != execution_id:
                raise JournalConflict("execution identity mismatch")
            if current["state"] == "ACCEPTED":
                return current
            if current["state"] == "REJECTED" and state in {"CLAIMED", "PUBLISHING", "AWAITING_ACCEPTANCE", "UNKNOWN"}:
                return current
            if state in {"COMMITTED", "ACCEPTED"} and not (current.get("admission") or {}).get("accepted"):
                raise JournalConflict("UAV admission evidence required")
            if current["state"] == "COMMITTED" and state != "COMMITTED":
                raise JournalConflict("cannot downgrade a committed execution")
            if current["state"] == "BROKER_CONFIRMED" and state not in {"BROKER_CONFIRMED", "COMMITTED"}:
                raise JournalConflict("cannot downgrade a broker-confirmed execution")
            confirmed_at = current["broker_confirmed_at_ms"]
            if state in {"BROKER_CONFIRMED", "AWAITING_ACCEPTANCE"} and confirmed_at is None:
                confirmed_at = int(time.time() * 1000)
            self.connection.execute(
                "UPDATE execution_intents SET state=?,detail=?,broker_confirmed_at_ms=? WHERE scope=? AND execution_id=?",
                (state, detail, confirmed_at, self.scope, execution_id))
        return self.get(event_key)


    def key_for_execution(self, execution_id):
        with self.lock:
            row = self.connection.execute("SELECT event_key FROM execution_intents WHERE scope=? AND execution_id=?",
                                          (self.scope, execution_id)).fetchone()
        return tuple(json.loads(row[0])) if row else None

    def outstanding(self):
        with self.lock:
            ids = [row[0] for row in self.connection.execute("SELECT execution_id FROM execution_intents WHERE scope=? AND state NOT IN ('CANCELLED')", (self.scope,))]
        return [self.get_by_execution_id(eid) for eid in ids]

    def next_attempt(self, event_key, execution_id):
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.get(event_key)
            if not current or current['execution_id'] != execution_id or current['state'] != 'REJECTED':
                raise JournalConflict('only a confirmed rejection authorizes a new admission attempt')
            self.connection.execute("UPDATE execution_intents SET attempt=attempt+1,state='CLAIMED',broker_confirmed_at_ms=NULL WHERE scope=? AND execution_id=?", (self.scope, execution_id))
        return self.get(event_key)

    def record_admission(self, data):
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.get_by_execution_id(data.get('execution_id'))
            if not current: return None
            if (data.get('schema_version') != 2 or data.get('message_type') != 'ADMISSION'
                    or data.get('mission_id') != current['payload']['mission_id']
                    or data.get('content_fingerprint') != current['fingerprint']
                    or type(data.get('accepted')) is not bool
                    or type(data.get('attempt')) is not int or data['attempt'] < 1
                    or type(data.get('decision_seq')) is not int or data['decision_seq'] < 1):
                raise JournalConflict('invalid admission identity or evidence')
            prior = current.get('admission')
            if prior and prior.get('accepted') is True: return current
            if data['attempt'] < current['attempt'] or (prior and data['decision_seq'] <= prior['decision_seq']): return current
            if data['attempt'] > current['attempt']: raise JournalConflict('unrecognized admission attempt')
            state = 'ACCEPTED' if data['accepted'] else 'REJECTED'
            self.connection.execute("UPDATE execution_intents SET state=?,admission=?,detail=?,protocol_version=1 WHERE scope=? AND execution_id=?",
                (state,json.dumps(data,sort_keys=True,allow_nan=False),str(data.get('reason',''))[:512],self.scope,current['execution_id']))
        return self.get_by_execution_id(current['execution_id'])

    def expire_admissions(self, now_ms=None):
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self.lock:
            outer = self.connection.in_transaction
            self.connection.execute("UPDATE execution_intents SET state='UNKNOWN',detail='UAV admission timed out; query the saved execution' "
                "WHERE scope=? AND state='AWAITING_ACCEPTANCE' AND broker_confirmed_at_ms IS NOT NULL AND broker_confirmed_at_ms+15000<=?", (self.scope,now_ms))
            if not outer: self.connection.commit()

    def restore_remote(self, event_key, remote):
        payload = remote['payload']
        current = self.prepare(event_key, payload)
        attempt = remote.get('attempt', 1)
        if type(attempt) is not int or not 1 <= attempt <= 2147483647:
            raise JournalConflict('invalid cloud admission attempt')
        protocol = 1 if remote.get('transport_version') == 1 else 0
        with self.lock, self.connection:
            self.connection.execute("UPDATE execution_intents SET attempt=?,protocol_version=?,state='UNKNOWN' WHERE scope=? AND execution_id=?",
                                    (attempt, protocol, self.scope, current['execution_id']))
        return self.get(event_key)
