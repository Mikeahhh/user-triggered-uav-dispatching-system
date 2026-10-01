import json
import pathlib
import re
import sqlite3
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
NATIVE = ROOT / "android/app/src/main/java/com/fypproject/tracking"
SCHEMA = json.loads((ROOT / "android/app/src/main/assets/tracking_schema.json").read_text())


def migrate(db, fail_at=None):
    with db:
        for statement in SCHEMA["create"]:
            db.execute(statement)
        assert db.execute("SELECT version FROM db_version WHERE id=1").fetchone()[0] <= 4
        for table, columns in SCHEMA["columns"].items():
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for column, kind in columns.items():
                if column not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
                if fail_at == column:
                    raise RuntimeError("injected migration failure")
        for statement in SCHEMA["finish"]:
            db.execute(statement)


def check_database():
    checks = 0
    for version in (0, 1, 2, 3):
        db = sqlite3.connect(":memory:")
        db.executescript("""
          CREATE TABLE db_version(id INTEGER PRIMARY KEY,version INTEGER);
          CREATE TABLE routes(id INTEGER PRIMARY KEY,name TEXT,start_time TEXT);
          CREATE TABLE locations(id INTEGER PRIMARY KEY,latitude REAL,longitude REAL,timestamp TEXT,route_id INTEGER);
          INSERT INTO routes VALUES(7,'original route','original time');
          INSERT INTO locations VALUES(9,22.4,114.3,'original capture',7);
        """)
        db.execute("INSERT INTO db_version VALUES(1,?)", (version,))
        db.commit()
        migrate(db)
        migrate(db)
        assert db.execute("SELECT id,name,start_time,owner_phone,binding_state FROM routes").fetchone() == (7, "original route", "original time", None, "LEGACY_UNBOUND")
        assert db.execute("SELECT id,latitude,longitude,timestamp,route_id FROM locations").fetchone() == (9,22.4,114.3,"original capture",7)
        assert db.execute("SELECT version FROM db_version").fetchone() == (4,)
        checks += 3
        db.close()
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE db_version(id INTEGER PRIMARY KEY,version INTEGER); INSERT INTO db_version VALUES(1,3); CREATE TABLE routes(id INTEGER PRIMARY KEY,name TEXT,start_time TEXT); INSERT INTO routes VALUES(1,'keep','time');")
    try:
        migrate(db, "owner_phone")
    except RuntimeError:
        pass
    assert db.execute("SELECT version FROM db_version").fetchone() == (3,)
    assert db.execute("SELECT * FROM routes").fetchone() == (1, "keep", "time")
    assert "owner_phone" not in {row[1] for row in db.execute("PRAGMA table_info(routes)")}
    checks += 3
    migrate(db)
    source = (NATIVE / "TrackingStore.kt").read_text()
    query = re.search(r'"(SELECT q.id,q.group_key,q.target,q.path,q.kind,q.payload,q.attempts FROM tracking_outbox q[^"\n]+)"', source).group(1)
    insert = re.search(r'"(INSERT OR IGNORE INTO tracking_outbox\(operation_key[^"\n]+)"', source).group(1)
    for key, group, kind in (("a/start","a","START"),("a/p1","a","POINT"),("a/p2","a","POINT"),("a/end","a","END"),("b/sos","b","RECORD")):
        db.execute(insert,(key,group,"https://synthetic-project.firebaseio.com","users/123/record",kind,"{}"))
    db.commit()
    assert db.execute(query,("0",)).fetchone()[4] == "START"
    db.execute("UPDATE tracking_outbox SET attempts=1,next_attempt_ms=999 WHERE id=1")
    assert db.execute(query,("0",)).fetchone()[4] == "RECORD"
    db.execute("UPDATE tracking_outbox SET acknowledged_ms=1 WHERE group_key='b'")
    assert db.execute(query,("0",)).fetchone() is None
    assert db.execute(query,("999",)).fetchone()[4] == "START"
    db.execute("UPDATE tracking_outbox SET acknowledged_ms=1 WHERE id=1")
    assert db.execute(query,("999",)).fetchone()[4] == "POINT"
    db.execute("UPDATE tracking_outbox SET acknowledged_ms=1 WHERE id=2")
    assert db.execute(query,("999",)).fetchone()[0] == 3
    db.execute("UPDATE tracking_outbox SET acknowledged_ms=1 WHERE id=3")
    assert db.execute(query,("999",)).fetchone()[4] == "END"
    db.commit()
    checks += 7
    claim = re.search(r'"(INSERT OR REPLACE INTO tracking_sync_groups[^"\n]+)"', source).group(1)
    db.execute("UPDATE tracking_outbox SET acknowledged_ms=NULL,next_attempt_ms=0")
    db.execute(claim, ("a",))
    assert db.execute(query, ("0",)).fetchone()[1] == "b"
    db.execute(claim, ("b",))
    assert db.execute(query, ("0",)).fetchone()[1] == "a"
    checks += 2
    db.execute("UPDATE tracking_outbox SET acknowledged_ms=1 WHERE kind!='END'")
    db.commit()
    db.execute("INSERT INTO tracking_sessions(session_id,phone,target,route_id,state,started_ms) VALUES('s1','123','synthetic',1,'ACTIVE',1)")
    try:
        db.execute("INSERT INTO tracking_sessions(session_id,phone,target,route_id,state,started_ms) VALUES('s2','456','synthetic',1,'STARTING',2)")
        raise AssertionError("two simultaneous sessions accepted")
    except sqlite3.IntegrityError:
        checks += 1
    with tempfile.TemporaryDirectory() as temporary:
        saved = pathlib.Path(temporary) / "persistent.db"
        disk = sqlite3.connect(saved)
        db.commit()
        db.backup(disk)
        disk.close()
        reopened = sqlite3.connect(saved)
        assert reopened.execute(query,("999",)).fetchone()[4] == "END"
        checks += 1
    query_points = re.search(r'"(SELECT sequence,latitude,longitude,timestamp,accuracy,altitude,speed,heading FROM locations[^"\n]+)"', source).group(1)
    for sequence in range(1, 10002):
        db.execute("INSERT INTO locations(route_id,session_id,sequence,latitude,longitude,timestamp) VALUES(1,'s1',?,22.4,114.3,?)", (sequence, str(1800000000000 + sequence*5000)))
    last = 0
    reconstructed = []
    while True:
        page = db.execute(query_points, ("s1",str(last),"1000")).fetchall()
        if not page:
            break
        reconstructed.extend(page)
        last = page[-1][0]
    assert [point[0] for point in reconstructed] == list(range(1,10002))
    assert all(point[1:3] == (22.4,114.3) for point in reconstructed)
    checks += 2
    print(f"Tracking SQLite checks: {checks} passed")


def check_kotlin():
    cache = pathlib.Path.home() / ".gradle/caches/modules-2/files-2.1"
    required = [
        ("org.jetbrains.kotlin", "kotlin-compiler-embeddable", "2.1.20"),
        ("org.jetbrains.kotlin", "kotlin-stdlib", "2.1.20"),
        ("org.jetbrains.kotlin", "kotlin-script-runtime", "2.1.20"),
        ("org.jetbrains.kotlin", "kotlin-reflect", "1.6.10"),
        ("org.jetbrains.intellij.deps", "trove4j", "1.0.20200330"),
        ("org.jetbrains.kotlinx", "kotlinx-coroutines-core-jvm", "1.8.0"),
        ("org.jetbrains", "annotations", "23.0.0"),
        ("org.json", "json", "20180813"),
    ]
    jars = []
    for group, artifact, version in required:
        candidates = list((cache / group / artifact / version).glob("*/*.jar"))
        if len(candidates) != 1:
            raise RuntimeError(f"Existing dependency unavailable: {artifact}:{version}; no downloads attempted")
        jars.append(str(candidates[0]))
    java = "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java"
    classpath = ":".join(jars)
    with tempfile.TemporaryDirectory(prefix="tracking-kotlin-") as temporary:
        subprocess.run([java,"-cp",classpath,"org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
                        "-no-stdlib","-no-reflect","-classpath",classpath,"-d",temporary,
                        str(NATIVE/"TrackingCore.kt"),str(NATIVE/"FirebaseRestTransport.kt"),
                        str(ROOT/"__tests__/native/tracking/TrackingCoreCheck.kt")],check=True)
        subprocess.run([java,"-cp",temporary+":"+classpath,"com.fypproject.tracking.TrackingCoreCheckKt"],check=True)


if __name__ == "__main__":
    check_database()
    check_kotlin()
