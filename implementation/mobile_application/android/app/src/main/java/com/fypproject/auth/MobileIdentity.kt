package com.fypproject.auth

import android.content.Context
import android.os.Looper
import com.google.android.gms.tasks.Tasks
import com.google.firebase.FirebaseApp
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.FirebaseUser
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/** Firebase owns the login session. This cache contains binding metadata, never ID tokens. */
object MobileIdentity {
    private const val CACHE = "mobile_identity_binding_v1"
    private val identityLock = Any()
    private var accountChangeInProgress = false

    internal fun app(context: Context): FirebaseApp = try {
        FirebaseApp.getInstance()
    } catch (_: IllegalStateException) {
        FirebaseApp.initializeApp(context.applicationContext) ?: throw AuthException(
            "AUTH_CONFIGURATION", "Add your Android Firebase configuration before signing in."
        )
    }

    internal fun auth(context: Context): FirebaseAuth = FirebaseAuth.getInstance(app(context))

    private fun configuration(context: Context): Pair<String, String> {
        val options = app(context).options
        return AuthPolicy.project(options.projectId) to AuthPolicy.target(options.databaseUrl ?: "")
    }

    private fun preferences(context: Context) =
        context.applicationContext.getSharedPreferences(CACHE, Context.MODE_PRIVATE)

    fun currentOwner(context: Context): AuthOwner? = synchronized(identityLock) {
        if (accountChangeInProgress) return@synchronized null
        val user = runCatching { auth(context).currentUser }.getOrNull() ?: return@synchronized null
        if (user.providerData.none { it.providerId == "google.com" }) return@synchronized null
        val config = runCatching { configuration(context) }.getOrNull() ?: return@synchronized null
        val cache = preferences(context)
        val owner = AuthOwner(cache.getString("uid", "") ?: "", cache.getString("projectId", "") ?: "",
            cache.getString("phone", "") ?: "", cache.getString("target", "") ?: "")
        owner.takeIf { cache.getLong("verifiedAt", 0L) > 0L &&
            AuthPolicy.matches(it, user.uid, config.first, config.second) }
    }

    fun requireOwner(context: Context, phone: String, target: String): AuthOwner {
        val owner = currentOwner(context) ?: throw AuthException(
            "AUTH_UNBOUND", "Sign in with a Google account and refresh its administrator binding."
        )
        if (owner.phone != AuthPolicy.phone(phone) || owner.target != AuthPolicy.target(target)) {
            throw AuthException("OWNER_MISMATCH", "This record belongs to a different account or database.")
        }
        return owner
    }

    fun idTokenFor(context: Context, owner: AuthOwner, forceRefresh: Boolean = false): String {
        backgroundOnly()
        if (currentOwner(context) != owner) ownerChanged()
        val user = auth(context).currentUser ?: ownerChanged()
        val token = tokenForUser(context, user, forceRefresh)
        if (currentOwner(context) != owner) ownerChanged()
        return token
    }

    private fun tokenForUser(context: Context, user: FirebaseUser, forceRefresh: Boolean): String {
        backgroundOnly()
        if (auth(context).currentUser?.uid != user.uid) ownerChanged()
        val result = try {
            Tasks.await(user.getIdToken(forceRefresh), 15, TimeUnit.SECONDS)
        } catch (_: Exception) {
            throw AuthException("AUTH_TOKEN_UNAVAILABLE", "Reconnect and refresh your sign-in before synchronizing.")
        }
        if (auth(context).currentUser?.uid != user.uid) ownerChanged()
        if (result.signInProvider != "google.com") throw AuthException(
            "AUTH_GOOGLE_REQUIRED", "Sign in with Google before synchronizing."
        )
        return result.token?.takeIf { it.isNotBlank() } ?: throw AuthException(
            "AUTH_TOKEN_UNAVAILABLE", "Refresh your sign-in before synchronizing."
        )
    }

    /** Always reads the server; Firebase offline snapshots cannot establish a new binding. */
    internal fun refreshBinding(context: Context) {
        backgroundOnly()
        val user = auth(context).currentUser ?: run { clearBinding(context); return }
        val (project, target) = configuration(context)
        val token = tokenForUser(context, user, true)
        val encodedUid = URLEncoder.encode(user.uid, "UTF-8")
        val encodedToken = URLEncoder.encode(token, "UTF-8")
        var connection: HttpURLConnection? = null
        val payload: String
        try {
            connection = URL("$target/mobile_account_bindings/$encodedUid.json?auth=$encodedToken")
                .openConnection() as HttpURLConnection
            connection.requestMethod = "GET"
            connection.connectTimeout = 10000
            connection.readTimeout = 10000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-store")
            val status = connection.responseCode
            if (status == 401 || status == 403) {
                clearBinding(context)
                throw AuthException("AUTH_BINDING_DENIED", "This account cannot read its administrator binding.")
            }
            if (status != 200) throw AuthException("AUTH_BINDING_NETWORK", "The administrator binding could not be refreshed.")
            val bytes = connection.inputStream.use { it.readBytesLimited(16384) }
            payload = bytes.toString(Charsets.UTF_8)
        } catch (error: AuthException) {
            throw error
        } catch (_: Exception) {
            throw AuthException("AUTH_BINDING_NETWORK", "Connect to the internet to refresh the administrator binding.")
        } finally { connection?.disconnect() }
        if (auth(context).currentUser?.uid != user.uid || configuration(context) != (project to target)) ownerChanged()
        val binding = runCatching { if (payload.trim() == "null") null else JSONObject(payload) }.getOrNull()
        val phone = binding?.opt("phone") as? String
        if (binding?.opt("enabled") != true || phone == null || runCatching { AuthPolicy.phone(phone) }.isFailure) {
            clearBinding(context)
            return
        }
        synchronized(identityLock) {
            if (auth(context).currentUser?.uid != user.uid) ownerChanged()
            if (!preferences(context).edit().clear().putString("uid", user.uid).putString("projectId", project)
                    .putString("phone", phone).putString("target", target)
                    .putLong("verifiedAt", System.currentTimeMillis()).commit()) {
                throw AuthException("AUTH_CACHE", "The account binding could not be saved on this device.")
            }
        }
    }

    internal fun clearBinding(context: Context) = synchronized(identityLock) {
        if (!preferences(context).edit().clear().commit()) {
            throw AuthException("AUTH_CACHE", "The account binding could not be cleared on this device.")
        }
    }

    /** Caller holds TrackingStore's monitor so no recording can start between the checks. */
    internal fun beginAccountChange() = synchronized(identityLock) {
        if (accountChangeInProgress) throw AuthException("AUTH_BUSY", "Finish the current sign-in operation first.")
        accountChangeInProgress = true
    }

    internal fun finishAccountChange() = synchronized(identityLock) { accountChangeInProgress = false }

    internal fun state(context: Context): JSONObject {
        val user = runCatching { auth(context).currentUser }.getOrNull()
        if (user == null) return JSONObject().put("status", "SIGNED_OUT")
        val owner = currentOwner(context)
        val result = JSONObject().put("status", if (owner == null) "UNBOUND" else "BOUND")
            .put("uid", user.uid).put("email", user.email ?: JSONObject.NULL)
            .put("displayName", user.displayName ?: JSONObject.NULL)
        if (owner != null) result.put("phone", owner.phone).put("projectId", owner.projectId).put("target", owner.target)
        return result
    }

    private fun backgroundOnly() {
        check(Looper.myLooper() != Looper.getMainLooper()) { "Authentication network work must run in the background." }
    }

    private fun ownerChanged(): Nothing = throw AuthException("OWNER_CHANGED", "The signed-in account has changed. Retry with its own records.")

    private fun java.io.InputStream.readBytesLimited(limit: Int): ByteArray {
        val output = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(2048)
        while (true) {
            val count = read(buffer)
            if (count == -1) break
            if (output.size() + count > limit) throw AuthException("INVALID_BINDING", "The administrator binding is invalid.")
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }
}
