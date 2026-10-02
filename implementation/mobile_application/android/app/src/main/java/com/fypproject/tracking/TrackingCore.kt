package com.fypproject.tracking

import java.net.URI
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

object TrackingCore {
    const val DATABASE_NAME = "location_tracker.db"
    const val SCHEMA_VERSION = 4
    const val INTERVAL_MS = 5000L
    const val MAX_FIX_AGE_MS = 15000L

    fun iso(milliseconds: Long): String =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.format(Date(milliseconds))

    fun validPoint(latitude: Double, longitude: Double, captured: Long, previous: Long, started: Long, now: Long): Boolean =
        latitude.isFinite() && longitude.isFinite() &&
            latitude in -90.0..90.0 && longitude in -180.0..180.0 &&
            captured >= started && captured > previous && captured <= now &&
            now - captured <= MAX_FIX_AGE_MS

    fun requirePhone(phone: String): String {
        require(phone.matches(Regex("[0-9]{3,20}"))) { "A valid phone number is required" }
        return phone
    }

    fun requireTarget(target: String): String {
        val uri = URI(target)
        require(uri.scheme == "https" && uri.userInfo == null && uri.port == -1 &&
            uri.query == null && uri.fragment == null && (uri.path.isNullOrEmpty() || uri.path == "/")) {
            "The database must be a clean HTTPS origin"
        }
        val host = uri.host ?: error("Invalid database host")
        require(host.matches(Regex("[a-z0-9-]+\\.firebaseio\\.com")) ||
            host.matches(Regex("[a-z0-9-]+\\.[a-z0-9-]+\\.firebasedatabase\\.app"))) {
            "Unsupported database host"
        }
        return "https://${host.lowercase(Locale.US)}"
    }

    fun retryDelay(attempt: Int): Long = (1000L shl attempt.coerceIn(0, 8)).coerceAtMost(300000L)

    fun requirePath(path: String): String {
        require(path.startsWith("users/") && path.split('/').all { segment ->
            segment.isNotEmpty() && segment.none { it in ".#$[]" || it.code <= 31 || it.code == 127 }
        }) { "Invalid database record path" }
        return path
    }

    fun canSample(state: String): Boolean = state == "STARTING" || state == "ACTIVE"
}

data class RecordedFix(
    val latitude: Double, val longitude: Double, val capturedMs: Long,
    val accuracy: Double?, val altitude: Double?, val speed: Double?, val heading: Double?
)

data class PendingWrite(
    val id: Long, val group: String, val target: String, val path: String,
    val kind: String, val payload: String, val attempts: Int
)
