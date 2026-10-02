import sqlite3
import uuid
from pathlib import Path

from dispatch_journal import DispatchJournal


class ObservationStore:


    def __init__(self, path, scope):
        if not isinstance(scope, str) or not scope:
            raise ValueError("observation database scope is required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        DispatchJournal._restrict_file(self.path, create=True)
        self.scope = scope
        self.connection = sqlite3.connect(str(self.path), timeout=5)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        with self.connection:
            self.connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('workstation',?)", (uuid.uuid4().hex,))
            self.connection.execute("CREATE TABLE IF NOT EXISTS observations ("
                                    "scope TEXT NOT NULL,user_id TEXT NOT NULL,session_id TEXT NOT NULL,"
                                    "sample_ms INTEGER NOT NULL,first_seen_ms INTEGER NOT NULL,"
                                    "PRIMARY KEY(scope,user_id,session_id,sample_ms))")
        self.workstation_id = self.connection.execute(
            "SELECT value FROM metadata WHERE key='workstation'").fetchone()[0]
        for suffix in ("", "-wal", "-shm"):
            companion = Path(str(self.path) + suffix)
            if companion.exists():
                DispatchJournal._restrict_file(companion)

    def observe(self, user_id, session_id, sample_ms, observed_at_ms):
        if any(type(value) is not int or not 0 <= value <= 2 ** 53 - 1
               for value in (sample_ms, observed_at_ms)) or sample_ms > observed_at_ms:
            raise ValueError("sample and observation times must be valid epoch milliseconds")
        if any(not isinstance(value, str) or not value for value in (user_id, session_id)):
            raise ValueError("user and session identity are required")
        key = (self.scope, user_id, session_id, sample_ms)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)",
                                    (*key, observed_at_ms))
        return self.connection.execute(
            "SELECT first_seen_ms FROM observations WHERE scope=? AND user_id=? AND session_id=? AND sample_ms=?",
            key).fetchone()[0]

    def close(self):
        self.connection.close()
