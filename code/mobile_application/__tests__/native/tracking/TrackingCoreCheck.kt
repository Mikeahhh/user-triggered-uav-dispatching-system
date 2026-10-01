package com.fypproject.tracking

import org.json.JSONObject

fun main() {
    var checks = 0
    fun verify(value: Boolean) { check(value); checks++ }
    fun rejected(action: () -> Unit) { verify(runCatching(action).isFailure) }
    val now = 1800000000000L
    verify(TrackingCore.iso(now) == "2027-01-15T08:00:00.000Z")
    verify(TrackingCore.validPoint(22.4, 114.3, now, now - 5000, now - 10000, now))
    verify(!TrackingCore.validPoint(22.4, 114.3, now, now, now - 10000, now))
    verify(!TrackingCore.validPoint(22.4, 114.3, now - 16000, now - 20000, now - 30000, now))
    verify(!TrackingCore.validPoint(22.4, 114.3, now + 1, now - 5000, now - 10000, now))
    verify(!TrackingCore.validPoint(22.4, 114.3, now, now - 5000, now + 1, now))
    verify(!TrackingCore.validPoint(Double.NaN, 114.3, now, 0, 0, now))
    verify(!TrackingCore.validPoint(91.0, 114.3, now, 0, 0, now))
    verify(!TrackingCore.validPoint(22.4, -181.0, now, 0, 0, now))
    verify(TrackingCore.canSample("STARTING") && TrackingCore.canSample("ACTIVE"))
    verify(!TrackingCore.canSample("STOPPED") && !TrackingCore.canSample("INTERRUPTED"))
    verify(TrackingCore.retryDelay(0) == 1000L && TrackingCore.retryDelay(50) <= 300000L)
    verify(TrackingCore.requireTarget("https://synthetic-project-default-rtdb.firebaseio.com/") == "https://synthetic-project-default-rtdb.firebaseio.com")
    for (bad in listOf("http://synthetic-project.firebaseio.com", "https://user@synthetic-project.firebaseio.com", "https://synthetic-project.firebaseio.com/path", "https://example.org", "https://synthetic-project.firebaseio.com?x=1")) rejected { TrackingCore.requireTarget(bad) }
    for (bad in listOf("", "123/456", "abc", "12")) rejected { TrackingCore.requirePhone(bad) }
    rejected { TrackingCore.requirePath("users/123/../record") }
    verify(TrackingCore.requirePath("users/123/rescue_requests/42").endsWith("/42"))

    data class Call(val method: String, val payload: String?, val etag: String?)
    class Fake(private val responses: MutableList<RestResult>) : FirebaseRestTransport() {
        val calls = mutableListOf<Call>()
        override fun request(target: String, path: String, method: String, payload: String?, etag: String?): RestResult {
            calls.add(Call(method, payload, etag))
            return responses.removeAt(0)
        }
    }
    val start = PendingWrite(1, "session", "https://synthetic-project.firebaseio.com", "users/123/QuickStartSessions/session", "START",
        """{"startTime":"2026-09-30T00:00:00Z","status":"ACTIVE","points":{"point_1":{"latitude":22.4}}}""", 0)
    val lostAck = Fake(mutableListOf(RestResult(412, """{"startTime":"2026-09-30T00:00:00Z","status":"COMPLETED","points":{"point_1":{"latitude":22.4},"point_2":{}}}""", "etag")))
    lostAck.send(start)
    verify(lostAck.calls.single().etag == "null_etag")
    verify(lostAck.calls.size == 1)
    val conflict = Fake(mutableListOf(RestResult(412, """{"startTime":"2026-09-29T00:00:00Z"}""", "etag")))
    rejected { conflict.send(start) }
    val point = Fake(mutableListOf(RestResult(200, "{}", null)))
    point.send(start.copy(kind="POINT", path=start.path+"/points/point_2", payload="""{"timestamp":1800000000000}"""))
    verify(point.calls.single().method == "PUT")
    verify(point.calls.single().etag == "null_etag")
    val duplicatePoint = Fake(mutableListOf(RestResult(412, """{"latitude":22.4,"longitude":114,"timestamp":1800000000000}""", "etag")))
    duplicatePoint.send(start.copy(kind="POINT", payload="""{"latitude":22.4,"longitude":114.0,"timestamp":1800000000000,"accuracy":null}"""))
    verify(duplicatePoint.calls.size == 1)
    val differentPoint = Fake(mutableListOf(RestResult(412, """{"latitude":22.5,"timestamp":1800000000000}""", "etag")))
    rejected { differentPoint.send(start.copy(kind="POINT", payload="""{"latitude":22.4,"timestamp":1800000000000}""")) }
    val differentFirstPoint = Fake(mutableListOf(RestResult(412, """{"startTime":"2026-09-30T00:00:00Z","points":{"point_1":{"latitude":22.5}}}""", "etag")))
    rejected { differentFirstPoint.send(start) }
    val end = Fake(mutableListOf(RestResult(200, "{}", null)))
    end.send(start.copy(kind="END", payload="""{"status":"COMPLETED","endTime":"saved-time"}"""))
    verify(end.calls.single().method == "PATCH")
    verify(!JSONObject(end.calls.single().payload!!).has("points"))
    val failed = Fake(mutableListOf(RestResult(503, "unavailable", null)))
    rejected { failed.send(start.copy(kind="POINT")) }
    val newer = Fake(mutableListOf(RestResult(200, """{"_client_revision":20}""", "remote20")))
    newer.send(start.copy(kind="RECORD", payload="""{"_client_revision":19}"""))
    verify(newer.calls.size == 1)
    val older = Fake(mutableListOf(RestResult(200, """{"_client_revision":18}""", "remote18"), RestResult(200, "{}", null)))
    older.send(start.copy(kind="RECORD", payload="""{"_client_revision":19}"""))
    verify(older.calls.last().etag == "remote18" && older.calls.last().method == "PUT")
    val concurrent = Fake(mutableListOf(RestResult(200, "null", "null_etag"), RestResult(412, "{}", "changed")))
    rejected { concurrent.send(start.copy(kind="RECORD", payload="""{"_client_revision":19}""")) }
    val deletion = Fake(mutableListOf(RestResult(200, """{"_client_revision":19,"title":"old"}""", "r19"), RestResult(200, "{}", null)))
    deletion.send(start.copy(kind="DELETE", payload="""{"_client_revision":20,"_deleted":true}"""))
    verify(deletion.calls.last().method == "PUT" && JSONObject(deletion.calls.last().payload!!).getBoolean("_deleted"))
    val lateCreate = Fake(mutableListOf(RestResult(200, """{"_client_revision":20,"_deleted":true}""", "tombstone20")))
    lateCreate.send(start.copy(kind="RECORD", payload="""{"_client_revision":19,"title":"late create"}"""))
    verify(lateCreate.calls.size == 1)
    val lateUpdate = Fake(mutableListOf(RestResult(200, """{"_client_revision":20,"_deleted":true}""", "tombstone20")))
    lateUpdate.send(start.copy(kind="RECORD", payload="""{"_client_revision":18,"title":"late update"}"""))
    verify(lateUpdate.calls.size == 1)
    val lostDeleteAck = Fake(mutableListOf(RestResult(200, """{"_client_revision":20,"_deleted":true}""", "tombstone20")))
    lostDeleteAck.send(start.copy(kind="DELETE", payload="""{"_client_revision":20,"_deleted":true}"""))
    verify(lostDeleteAck.calls.size == 1)
    val staleDelete = Fake(mutableListOf(RestResult(200, """{"_client_revision":21,"title":"newer"}""", "r21")))
    staleDelete.send(start.copy(kind="DELETE", payload="""{"_client_revision":20,"_deleted":true}"""))
    verify(staleDelete.calls.size == 1)
    println("Tracking Kotlin checks: $checks passed")
}
