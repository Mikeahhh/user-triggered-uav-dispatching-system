package com.fypproject.tracking

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import com.fypproject.auth.AuthOwner
import com.fypproject.auth.MobileIdentity
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

class TrackingStore private constructor(private val context: Context) {
    private val database: SQLiteDatabase
    val path: String

    init {
        val file = context.getDatabasePath(TrackingCore.DATABASE_NAME)
        file.parentFile?.mkdirs()
        path = file.absolutePath
        database = SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.CREATE_IF_NECESSARY)
        database.enableWriteAheadLogging()
        try {
            database.rawQuery("PRAGMA busy_timeout=5000", null).use { it.moveToFirst() }
            migrate()
        } catch (error: Exception) { database.close(); throw error }
    }

    @Synchronized private fun migrate() {
        val schema = JSONObject(context.assets.open("tracking_schema.json").bufferedReader().use { it.readText() })
        transaction {
            val create = schema.getJSONArray("create")
            for (index in 0 until create.length()) database.execSQL(create.getString(index))
            val version = scalar("SELECT version FROM db_version WHERE id=1")
            require(version <= TrackingCore.SCHEMA_VERSION) { "This database requires a newer application" }
            val tables = schema.getJSONObject("columns")
            for (table in tables.keys()) {
                val existing = mutableSetOf<String>()
                database.rawQuery("PRAGMA table_info($table)", null).use { cursor ->
                    while (cursor.moveToNext()) existing.add(cursor.getString(cursor.getColumnIndexOrThrow("name")))
                }
                val columns = tables.getJSONObject(table)
                for (column in columns.keys()) {
                    if (column !in existing) database.execSQL("ALTER TABLE $table ADD COLUMN $column ${columns.getString(column)}")
                }
            }
            if (version < 5) {
                val migration = schema.getJSONArray("migrate_owner_v5")
                for (index in 0 until migration.length()) database.execSQL(migration.getString(index))
            }
            val finish = schema.getJSONArray("finish")
            for (index in 0 until finish.length()) database.execSQL(finish.getString(index))
        }
    }

    private fun <T> transaction(action: () -> T): T {
        database.beginTransaction()
        try {
            val result = action()
            database.setTransactionSuccessful()
            return result
        } finally { database.endTransaction() }
    }

    private fun scalar(sql: String, args: Array<String>? = null): Long =
        database.rawQuery(sql, args).use { if (it.moveToFirst()) it.getLong(0) else 0L }

    private val ownerWhere = "owner_uid=? AND owner_project=? AND target=?"
    private fun ownerArgs(owner: AuthOwner) = arrayOf(owner.uid, owner.projectId, owner.target)
    private fun requireCurrent(owner: AuthOwner): AuthOwner {
        require(MobileIdentity.requireOwner(context, owner.phone, owner.target) == owner) { "Account changed; synchronization is paused" }
        return owner
    }
    private fun currentOwner(): AuthOwner = MobileIdentity.currentOwner(context)
        ?: error("Sign in with a verified account to access these records")
    private fun ownerOf(session: JSONObject) = AuthOwner(session.getString("owner_uid"),
        session.getString("owner_project"), session.getString("phone"), session.getString("target"))

    private fun reconcileOwner(owner: AuthOwner?) {
        val active = "state IN ('STARTING','ACTIVE','INTERRUPTED') AND owner_uid!=''"
        val reason = "Recording paused because its verified account is unavailable. Sign in with the original account to resume."
        if (owner == null) database.execSQL("UPDATE tracking_sessions SET state='AUTH_PAUSED',last_error=? WHERE $active", arrayOf(reason))
        else database.execSQL("UPDATE tracking_sessions SET state='AUTH_PAUSED',last_error=? WHERE $active AND NOT ($ownerWhere AND phone=?)", arrayOf(reason) + ownerArgs(owner) + arrayOf(owner.phone))
    }

    // Authentication changes use this inside the same store monitor as start().
    @Synchronized fun hasActiveSessionForAccountChange(): Boolean {
        reconcileOwner(MobileIdentity.currentOwner(context))
        return scalar("SELECT COUNT(*) FROM tracking_sessions WHERE owner_uid!='' AND state IN ('STARTING','ACTIVE','INTERRUPTED')") > 0
    }

    @Synchronized fun start(phone: String, target: String, now: Long): String = transaction {
        TrackingCore.requirePhone(phone)
        val owner = MobileIdentity.requireOwner(context, phone, TrackingCore.requireTarget(target))
        require(!hasActiveSessionForAccountChange() && sessionFor(owner) == null) { "Finish or resume the existing session first" }
        val session = "session_${now}_${UUID.randomUUID()}"
        val route = ContentValues().apply {
            put("name", "Session-$session"); put("start_time", TrackingCore.iso(now))
            put("session_id", session); put("owner_phone", phone); put("binding_state", "BOUND")
            put("owner_uid", owner.uid); put("owner_project", owner.projectId); put("target", owner.target)
            put("status", "active")
        }
        val routeId = database.insertOrThrow("routes", null, route)
        database.execSQL(
            "INSERT INTO tracking_sessions(session_id,phone,target,route_id,state,started_ms,owner_uid,owner_project) VALUES(?,?,?,?,'STARTING',?,?,?)",
            arrayOf(session, phone, owner.target, routeId, now, owner.uid, owner.projectId)
        )
        session
    }

    private fun sessionFor(owner: AuthOwner, sessionId: String? = null): JSONObject? {
        val condition = if (sessionId == null) "state IN ('STARTING','ACTIVE','INTERRUPTED','AUTH_PAUSED')" else "session_id=?"
        val args = ownerArgs(owner) + arrayOf(owner.phone) + if (sessionId == null) emptyArray() else arrayOf(sessionId)
        return database.rawQuery("SELECT session_id,phone,target,route_id,state,started_ms,last_sample_ms,next_sequence,last_error,owner_uid,owner_project FROM tracking_sessions WHERE $ownerWhere AND phone=? AND $condition LIMIT 1", args).use { cursor ->
            if (!cursor.moveToFirst()) null else JSONObject().apply {
                for (column in listOf("session_id","phone","target","state","last_error","owner_uid","owner_project")) put(column, cursor.getString(cursor.getColumnIndexOrThrow(column)))
                for (column in listOf("route_id","started_ms","last_sample_ms","next_sequence")) put(column, cursor.getLong(cursor.getColumnIndexOrThrow(column)))
            }
        }
    }

    @Synchronized fun currentSession(): JSONObject? {
        val owner = MobileIdentity.currentOwner(context)
        reconcileOwner(owner)
        return owner?.let { sessionFor(it) }
    }

    @Synchronized fun resume(): Boolean {
        val owner = requireCurrent(currentOwner())
        val session = sessionFor(owner) ?: return false
        if (session.getString("state") !in setOf("INTERRUPTED", "AUTH_PAUSED")) return true
        require(scalar("SELECT COUNT(*) FROM tracking_sessions WHERE session_id!=? AND state IN ('STARTING','ACTIVE','INTERRUPTED')", arrayOf(session.getString("session_id"))) == 0L) { "Finish the other active recording before resuming" }
        val state = if (session.getLong("next_sequence") == 1L) "STARTING" else "ACTIVE"
        database.execSQL("UPDATE tracking_sessions SET state=?,last_error=? WHERE session_id=? AND $ownerWhere",
            arrayOf(state, "Recording resumed; any interruption remains a gap in the original history.", session.getString("session_id")) + ownerArgs(owner))
        return true
    }

    @Synchronized fun interrupt(reason: String, sessionId: String? = null) {
        // A service may mark only the session ID it captured before authentication changed.
        val owned = sessionId ?: currentSession()?.getString("session_id") ?: return
        database.execSQL("UPDATE tracking_sessions SET state='INTERRUPTED',last_error=? WHERE session_id=? AND state IN ('STARTING','ACTIVE')", arrayOf(reason, owned))
    }

    @Synchronized fun append(fix: RecordedFix, now: Long, expectedSessionId: String? = null): Boolean = transaction {
        val owner = MobileIdentity.currentOwner(context) ?: return@transaction false
        val session = sessionFor(owner) ?: return@transaction false
        if (expectedSessionId == null || session.getString("session_id") != expectedSessionId) return@transaction false
        requireCurrent(ownerOf(session))
        if (!TrackingCore.canSample(session.getString("state"))) return@transaction false
        if (!TrackingCore.validPoint(fix.latitude, fix.longitude, fix.capturedMs, session.getLong("last_sample_ms"), session.getLong("started_ms"), now)) return@transaction false
        val sessionId = session.getString("session_id")
        val sequence = session.getLong("next_sequence")
        val timestamp = TrackingCore.iso(fix.capturedMs)
        val values = ContentValues().apply {
            put("route_id", session.getLong("route_id")); put("session_id", sessionId); put("sequence", sequence)
            put("latitude", fix.latitude); put("longitude", fix.longitude); put("timestamp", timestamp)
            put("accuracy", fix.accuracy); put("altitude", fix.altitude); put("speed", fix.speed); put("heading", fix.heading)
        }
        database.insertOrThrow("locations", null, values)
        val point = JSONObject().put("latitude", fix.latitude).put("longitude", fix.longitude)
            .put("accuracy", fix.accuracy ?: JSONObject.NULL).put("altitude", fix.altitude ?: JSONObject.NULL)
            .put("speed", fix.speed ?: JSONObject.NULL).put("heading", fix.heading ?: JSONObject.NULL)
            .put("timestamp", fix.capturedMs).put("timestampISO", timestamp)
        val remote = "users/${owner.phone}/QuickStartSessions/$sessionId"
        if (sequence == 1L) enqueue("$sessionId/start", sessionId, owner, remote, "START",
            JSONObject().put("startTime", timestamp).put("status", "ACTIVE")
                .put("points", JSONObject().put("point_1", point)).toString())
        enqueue("$sessionId/point/$sequence", sessionId, owner, "$remote/points/point_$sequence", "POINT", point.toString())
        database.execSQL("UPDATE tracking_sessions SET state='ACTIVE',next_sequence=?,last_sample_ms=? WHERE session_id=?", arrayOf(sequence + 1, fix.capturedMs, sessionId))
        true
    }

    @Synchronized fun stop(now: Long, expectedSessionId: String? = null) = transaction {
        val owner = requireCurrent(currentOwner())
        val session = sessionFor(owner) ?: return@transaction
        val id = session.getString("session_id")
        require(expectedSessionId == null || expectedSessionId == id) { "The recording account changed" }
        if (session.getLong("next_sequence") > 1L) enqueue("$id/end", id, owner,
            "users/${owner.phone}/QuickStartSessions/$id", "END",
            JSONObject().put("endTime", TrackingCore.iso(now)).put("status", "COMPLETED").toString())
        database.execSQL("UPDATE tracking_sessions SET state='STOPPED',ended_ms=? WHERE session_id=?", arrayOf(now, id))
        database.execSQL("UPDATE routes SET status='completed' WHERE id=?", arrayOf(session.getLong("route_id")))
    }

    private fun enqueue(key: String, group: String, owner: AuthOwner, path: String, kind: String, payload: String) {
        TrackingCore.requirePath(path)
        require(path.startsWith("users/${owner.phone}/")) { "Record owner does not match the account" }
        database.execSQL("INSERT OR IGNORE INTO tracking_outbox(operation_key,group_key,target,path,kind,payload,owner_uid,owner_project,owner_phone) VALUES(?,?,?,?,?,?,?,?,?)",
            arrayOf(key, group, owner.target, path, kind, payload, owner.uid, owner.projectId, owner.phone))
    }

    @Synchronized fun queueRecord(phone: String, target: String, category: String, id: String, json: String, deleted: Boolean): JSONObject = transaction {
        TrackingCore.requirePhone(phone)
        val owner = MobileIdentity.requireOwner(context, phone, TrackingCore.requireTarget(target))
        require(category in setOf("booked_events", "rescue_requests"))
        require(id.matches(Regex("[A-Za-z0-9_-]+"))) { "Invalid record identifier" }
        val path = TrackingCore.requirePath("users/$phone/$category/$id")
        val args = ownerArgs(owner) + arrayOf(path)
        val revision = maxOf(System.currentTimeMillis(), scalar("SELECT revision FROM mobile_records WHERE $ownerWhere AND path=?", args) + 1)
        val payload = JSONObject(json).put("_client_revision", revision).put("_deleted", deleted)
        saveRecord(owner, path, category, id, payload, revision)
        val group = JSONArray(listOf(owner.uid, owner.projectId, owner.target, path)).toString()
        enqueue("$group/$revision", group, owner, path, if (deleted) "DELETE" else "RECORD", payload.toString())
        JSONObject().put("stored", true).put("synchronized", false)
    }

    private fun saveRecord(owner: AuthOwner, path: String, category: String, id: String, payload: JSONObject, revision: Long) {
        database.execSQL("INSERT OR REPLACE INTO mobile_records(path,phone,category,record_id,payload,deleted,revision,owner_uid,owner_project,target) VALUES(?,?,?,?,?,?,?,?,?,?)",
            arrayOf(path, owner.phone, category, id, payload.toString(), if (payload.optBoolean("_deleted", false)) 1 else 0, revision, owner.uid, owner.projectId, owner.target))
    }

    @Synchronized fun records(phone: String, category: String): JSONArray {
        val owner = currentOwner()
        require(phone == owner.phone && category in setOf("booked_events", "rescue_requests")) { "Record account does not match" }
        requireCurrent(owner)
        val result = JSONArray()
        database.rawQuery("SELECT record_id,payload FROM mobile_records WHERE $ownerWhere AND phone=? AND category=? AND deleted=0 ORDER BY revision DESC", ownerArgs(owner) + arrayOf(phone, category)).use {
            while (it.moveToNext()) result.put(JSONObject(it.getString(1)).put("id", it.getString(0)))
        }
        return result
    }

    @Synchronized fun mergeRecords(owner: AuthOwner, category: String, json: String) = transaction {
        requireCurrent(owner)
        require(category in setOf("booked_events", "rescue_requests"))
        val records = if (json == "null") JSONObject() else JSONObject(json)
        for (id in records.keys()) {
            val path = TrackingCore.requirePath("users/${owner.phone}/$category/$id")
            val args = ownerArgs(owner) + arrayOf(path)
            val pending = scalar("SELECT COUNT(*) FROM tracking_outbox WHERE $ownerWhere AND path=? AND acknowledged_ms IS NULL", args)
            if (pending == 0L) {
                val payload = records.optJSONObject(id) ?: continue
                val revision = payload.optLong("_client_revision", 0)
                val localRevision = scalar("SELECT revision FROM mobile_records WHERE $ownerWhere AND path=?", args)
                val deleted = scalar("SELECT deleted FROM mobile_records WHERE $ownerWhere AND path=?", args) != 0L
                if (localRevision > revision || (deleted && localRevision == revision)) continue
                saveRecord(owner, path, category, id, payload, revision)
            }
        }
    }

    @Synchronized fun nextWrite(now: Long): PendingWrite? = transaction {
        val owner = MobileIdentity.currentOwner(context) ?: return@transaction null
        requireCurrent(owner)
        val write = database.rawQuery(
            "SELECT q.id,q.group_key,q.target,q.path,q.kind,q.payload,q.attempts FROM tracking_outbox q LEFT JOIN tracking_sync_groups g ON g.group_key=q.group_key WHERE q.owner_uid=? AND q.owner_project=? AND q.target=? AND q.owner_phone=? AND q.acknowledged_ms IS NULL AND q.next_attempt_ms<=? AND NOT EXISTS(SELECT 1 FROM tracking_outbox p WHERE p.owner_uid=q.owner_uid AND p.owner_project=q.owner_project AND p.target=q.target AND p.group_key=q.group_key AND p.acknowledged_ms IS NULL AND p.id<q.id) ORDER BY COALESCE(g.last_served_order,0),q.id LIMIT 1",
            ownerArgs(owner) + arrayOf(owner.phone, now.toString())
        ).use { if (!it.moveToFirst()) null else PendingWrite(it.getLong(0),it.getString(1),it.getString(2),it.getString(3),it.getString(4),it.getString(5),it.getInt(6),owner) }
        if (write != null) database.execSQL(
            "INSERT OR REPLACE INTO tracking_sync_groups(group_key,last_served_order) VALUES(?,(SELECT COALESCE(MAX(last_served_order),0)+1 FROM tracking_sync_groups))", arrayOf(write.group))
        write
    }

    @Synchronized fun acknowledge(write: PendingWrite, now: Long) = transaction {
        requireCurrent(write.owner)
        database.execSQL("UPDATE tracking_outbox SET acknowledged_ms=?,last_error='' WHERE id=? AND $ownerWhere", arrayOf<Any>(now, write.id, *ownerArgs(write.owner)))
        if (write.kind == "POINT") {
            val sequence = write.path.substringAfterLast("point_").toLong()
            database.execSQL("UPDATE locations SET synced=1 WHERE session_id=? AND sequence=?", arrayOf(write.group, sequence))
        }
    }

    @Synchronized fun failed(write: PendingWrite, now: Long) {
        database.execSQL("UPDATE tracking_outbox SET attempts=attempts+1,next_attempt_ms=?,last_error=?,last_error_at_ms=? WHERE id=? AND $ownerWhere",
            arrayOf(now + TrackingCore.retryDelay(write.attempts), "Synchronization paused; check your account and connection.", now, write.id) + ownerArgs(write.owner))
    }

    @Synchronized fun retry() {
        val owner = requireCurrent(currentOwner())
        database.execSQL("UPDATE tracking_outbox SET next_attempt_ms=0 WHERE $ownerWhere AND acknowledged_ms IS NULL", ownerArgs(owner))
    }
    @Synchronized fun hasPending(): Boolean {
        val owner = MobileIdentity.currentOwner(context) ?: return false
        return scalar("SELECT COUNT(*) FROM tracking_outbox WHERE $ownerWhere AND owner_phone=? AND acknowledged_ms IS NULL", ownerArgs(owner) + arrayOf(owner.phone)) > 0
    }

    @Synchronized fun listPoints(sessionId: String, afterSequence: Long, limit: Int): JSONObject {
        require(afterSequence >= 0 && limit in 1..1000)
        val owner = requireCurrent(currentOwner())
        require(sessionFor(owner, sessionId) != null) { "This session is not available to the signed-in account" }
        val points = JSONArray()
        var last = afterSequence
        database.rawQuery("SELECT sequence,latitude,longitude,timestamp,accuracy,altitude,speed,heading FROM locations WHERE session_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            arrayOf(sessionId, afterSequence.toString(), limit.toString())).use {
            while (it.moveToNext()) {
                last = it.getLong(0)
                val point = JSONObject().put("sequence",last).put("latitude",it.getDouble(1))
                    .put("longitude",it.getDouble(2)).put("timestamp",it.getString(3))
                for (index in 4..7) point.put(it.getColumnName(index), if (it.isNull(index)) JSONObject.NULL else it.getDouble(index))
                points.put(point)
            }
        }
        val more = scalar("SELECT COUNT(*) FROM locations WHERE session_id=? AND sequence>?", arrayOf(sessionId,last.toString())) > 0
        return JSONObject().put("points",points).put("nextSequence",last).put("hasMore",more)
    }

    @Synchronized fun snapshot(): JSONObject {
        val owner = MobileIdentity.currentOwner(context)
        reconcileOwner(owner)
        val current = owner?.let { sessionFor(it) }
        val id = current?.getString("session_id") ?: if (owner == null) "" else database.rawQuery(
            "SELECT session_id FROM tracking_sessions WHERE $ownerWhere AND phone=? ORDER BY started_ms DESC LIMIT 1", ownerArgs(owner) + arrayOf(owner.phone)).use { if (it.moveToFirst()) it.getString(0) else "" }
        val pending = if (owner == null) 0L else scalar("SELECT COUNT(*) FROM tracking_outbox WHERE $ownerWhere AND owner_phone=? AND acknowledged_ms IS NULL", ownerArgs(owner) + arrayOf(owner.phone))
        val unbound = scalar("SELECT COUNT(*) FROM tracking_outbox WHERE (owner_uid='' OR owner_project='') AND acknowledged_ms IS NULL")
        val result = JSONObject().put("databasePath", path).put("session", current ?: JSONObject.NULL)
            .put("displaySessionId", id).put("pendingCount", pending).put("authPendingCount", unbound)
            .put("legacyUnboundRoutes", scalar("SELECT COUNT(*) FROM routes WHERE binding_state='LEGACY_UNBOUND'"))
        val syncError = if (owner == null) "Sign in with a verified account to synchronize." else database.rawQuery(
            "SELECT last_error FROM tracking_outbox WHERE $ownerWhere AND owner_phone=? AND acknowledged_ms IS NULL AND last_error!='' ORDER BY last_error_at_ms DESC,id DESC LIMIT 1", ownerArgs(owner) + arrayOf(owner.phone)).use { if (it.moveToFirst()) it.getString(0) else "" }
        result.put("latestSyncError", syncError)
        result.put("syncState", if (owner == null) "AUTH_REQUIRED" else if (syncError.isNotEmpty()) "ERROR" else if (pending > 0) "PENDING" else if (unbound > 0) "AUTH_REQUIRED" else "SYNCED")
        val stops = JSONArray()
        if (owner != null) database.rawQuery("SELECT group_key,last_error FROM tracking_outbox WHERE $ownerWhere AND owner_phone=? AND kind='END' AND acknowledged_ms IS NULL ORDER BY id", ownerArgs(owner) + arrayOf(owner.phone)).use {
            while (it.moveToNext()) stops.put(JSONObject().put("sessionId", it.getString(0)).put("error", it.getString(1)))
        }
        result.put("pendingStops", stops)
        result.put("totalPoints", scalar("SELECT COUNT(*) FROM locations WHERE session_id=?", arrayOf(id)))
        result.put("uploadedPoints", scalar("SELECT COUNT(*) FROM locations WHERE session_id=? AND synced=1", arrayOf(id)))
        val points = JSONArray()
        database.rawQuery("SELECT latitude,longitude,timestamp FROM (SELECT latitude,longitude,timestamp,sequence FROM locations WHERE session_id=? ORDER BY sequence DESC LIMIT 1000) ORDER BY sequence", arrayOf(id)).use {
            while (it.moveToNext()) points.put(JSONObject().put("latitude",it.getDouble(0)).put("longitude",it.getDouble(1)).put("timestamp",it.getString(2)))
        }
        return result.put("points", points)
    }

    @Synchronized fun clearTrackingData() = transaction {
        val owner = requireCurrent(currentOwner())
        require(sessionFor(owner) == null && !hasPending()) { "Finish recording and synchronize pending records before clearing history" }
        database.execSQL("DELETE FROM locations WHERE session_id IN (SELECT session_id FROM tracking_sessions WHERE $ownerWhere AND phone=?)", ownerArgs(owner) + arrayOf(owner.phone))
        database.execSQL("DELETE FROM routes WHERE $ownerWhere AND owner_phone=?", ownerArgs(owner) + arrayOf(owner.phone))
        database.execSQL("DELETE FROM tracking_sessions WHERE $ownerWhere AND phone=?", ownerArgs(owner) + arrayOf(owner.phone))
        database.execSQL("DELETE FROM tracking_sync_groups WHERE group_key IN (SELECT group_key FROM tracking_outbox WHERE $ownerWhere AND owner_phone=?)", ownerArgs(owner) + arrayOf(owner.phone))
        database.execSQL("DELETE FROM tracking_outbox WHERE $ownerWhere AND owner_phone=?", ownerArgs(owner) + arrayOf(owner.phone))
    }

    companion object {
        @Volatile private var instance: TrackingStore? = null
        fun get(context: Context): TrackingStore = instance ?: synchronized(this) {
            instance ?: TrackingStore(context.applicationContext).also { instance = it }
        }
    }
}
