package com.fypproject.auth

import java.net.URI
import java.util.Locale

data class AuthOwner(val uid: String, val projectId: String, val phone: String, val target: String)

class AuthException(val code: String, message: String) : IllegalStateException(message)

/** Validation shared by the login cache and every authenticated database operation. */
object AuthPolicy {
    fun target(value: String): String {
        val uri = try { URI(value.trim()) } catch (_: Exception) { invalidTarget() }
        val host = uri.host?.lowercase(Locale.ROOT) ?: invalidTarget()
        if (uri.scheme != "https" || uri.rawUserInfo != null || uri.rawQuery != null ||
            uri.rawFragment != null || uri.port !in setOf(-1, 443) ||
            uri.rawPath !in listOf("", "/") ||
            !(host.matches(Regex("[a-z0-9-]+\\.firebaseio\\.com")) ||
                host.matches(Regex("[a-z0-9-]+\\.[a-z0-9-]+\\.firebasedatabase\\.app")))) {
            invalidTarget()
        }
        if (listOf("external", "placeholder", "your-project").any { host.contains(it) }) invalidTarget()
        return "https://$host"
    }

    fun phone(value: String): String {
        if (!value.matches(Regex("[0-9]{3,20}"))) {
            throw AuthException("INVALID_BINDING", "The administrator must bind a valid phone number.")
        }
        return value
    }

    fun project(value: String?): String {
        if (value.isNullOrBlank() || !value.matches(Regex("[a-z][a-z0-9-]{4,62}")) ||
            listOf("external", "placeholder", "your-project").any { value.contains(it) }) {
            throw AuthException("AUTH_CONFIGURATION", "Configure the Android app with your Firebase project.")
        }
        return value
    }

    fun matches(owner: AuthOwner, uid: String?, project: String, target: String): Boolean =
        owner.uid.isNotBlank() && owner.uid == uid && owner.projectId == project &&
            owner.target == target && runCatching { phone(owner.phone) }.isSuccess

    private fun invalidTarget(): Nothing = throw AuthException(
        "AUTH_CONFIGURATION", "Use the database origin configured for this Android app."
    )
}
