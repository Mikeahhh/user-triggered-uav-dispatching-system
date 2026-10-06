package com.fypproject.tracking

import android.content.Context
import com.fypproject.auth.AuthOwner
import com.fypproject.auth.MobileIdentity
import org.json.JSONObject
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.HttpsURLConnection

data class RestResult(val code: Int, val body: String, val etag: String?)

open class FirebaseRestTransport(private val context: Context) {
    private fun verify(owner: AuthOwner, path: String) {
        TrackingCore.requireTarget(owner.target)
        TrackingCore.requirePath(path)
        require(path.startsWith("users/${owner.phone}/")) { "The record account does not match" }
        require(MobileIdentity.requireOwner(context, owner.phone, owner.target) == owner) {
            "Account changed; synchronization is paused"
        }
    }

    fun request(owner: AuthOwner, path: String, method: String, payload: String? = null, etag: String? = null): RestResult {
        try {
            require(method in setOf("GET", "PUT", "PATCH")) { "Unsupported database operation" }
            for (attempt in 0..1) {
                verify(owner, path)
                val token = MobileIdentity.idTokenFor(context, owner, forceRefresh = attempt == 1)
                verify(owner, path)
                val response = exchange(owner.target, path, method, payload, etag, token)
                verify(owner, path)
                if (response.code != 401 || attempt == 1) return response
            }
            error("Synchronization is unavailable")
        } catch (_: Exception) {
            // Network exceptions may contain the URL and ID token: never retain their cause or message.
            throw IllegalStateException("Synchronization paused; check your account and connection.")
        }
    }

    protected open fun exchange(target: String, path: String, method: String, payload: String?, etag: String?, token: String): RestResult {
        val encoded = path.split('/').joinToString("/") { URLEncoder.encode(it, "UTF-8").replace("+", "%20") }
        val connection = URL("$target/$encoded.json?auth=${URLEncoder.encode(token, "UTF-8")}").openConnection() as HttpsURLConnection
        val deadline = deadlines.schedule({ connection.disconnect() }, 12000, TimeUnit.MILLISECONDS)
        try {
            connection.connectTimeout = 8000
            connection.readTimeout = 8000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-store")
            connection.requestMethod = if (method == "PATCH") "POST" else method
            if (method == "PATCH") connection.setRequestProperty("X-HTTP-Method-Override", "PATCH")
            if (method == "GET") connection.setRequestProperty("X-Firebase-ETag", "true")
            if (etag != null) connection.setRequestProperty("if-match", etag)
            if (payload != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(payload.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
            return RestResult(status, body, connection.getHeaderField("ETag"))
        } finally { deadline.cancel(false); connection.disconnect() }
    }

    fun send(write: PendingWrite) {
        require(write.target == write.owner.target) { "The saved database target does not match the account" }
        verify(write.owner, write.path)
        val result = when (write.kind) {
            "START" -> {
                val attempt = request(write.owner, write.path, "PUT", write.payload, "null_etag")
                if (attempt.code != 412) attempt else {
                    val stored = JSONObject(attempt.body)
                    val expected = JSONObject(write.payload)
                    require(stored.optString("startTime") == expected.getString("startTime") &&
                        sameObject(stored.optJSONObject("points")?.optJSONObject("point_1"), expected.getJSONObject("points").getJSONObject("point_1"))) {
                        "The remote session identity conflicts with the saved session"
                    }
                    RestResult(200, attempt.body, attempt.etag)
                }
            }
            "END" -> request(write.owner, write.path, "PATCH", write.payload)
            "RECORD", "DELETE" -> {
                val previous = request(write.owner, write.path, "GET")
                require(previous.code in 200..299) { "Record lookup returned HTTP ${previous.code}" }
                val remote = if (previous.body == "null") null else JSONObject(previous.body)
                val revision = JSONObject(write.payload).getLong("_client_revision")
                if (remote != null && remote.optLong("_client_revision", 0) >= revision) {
                    RestResult(200, previous.body, previous.etag)
                } else {
                    request(write.owner, write.path, "PUT", write.payload, previous.etag ?: error("Missing database ETag"))
                }
            }
            "POINT" -> {
                val attempt = request(write.owner, write.path, "PUT", write.payload, "null_etag")
                if (attempt.code != 412) attempt else {
                    require(sameObject(JSONObject(attempt.body), JSONObject(write.payload))) {
                        "The remote point conflicts with the saved original sample"
                    }
                    RestResult(200, attempt.body, attempt.etag)
                }
            }
            else -> error("Unknown queued operation")
        }
        verify(write.owner, write.path)
        require(result.code in 200..299) { "Synchronization returned HTTP ${result.code}" }
    }

    companion object {
        private fun sameObject(left: JSONObject?, right: JSONObject): Boolean {
            if (left == null) return false
            val keys = left.keys().asSequence().toSet() + right.keys().asSequence().toSet()
            for (key in keys) {
                val a = left.opt(key) ?: JSONObject.NULL
                val b = right.opt(key) ?: JSONObject.NULL
                if (a is Number && b is Number) {
                    if (a.toDouble() != b.toDouble()) return false
                } else if (a != b) return false
            }
            return true
        }
        private val deadlines = Executors.newScheduledThreadPool(2) { action ->
            Thread(action, "tracking-http-deadline").apply { isDaemon = true }
        }
    }
}
