package com.fypproject.tracking

import com.fypproject.auth.AuthOwner
import com.fypproject.auth.MobileIdentity
import org.json.JSONObject
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.uimanager.ViewManager
import java.util.concurrent.Executors
import android.system.Os
import java.io.File

class TrackingModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
    private val worker = Executors.newSingleThreadExecutor()
    private val networkWorker = Executors.newSingleThreadExecutor()
    override fun getName() = "PersistentTracking"
    private fun perform(promise: Promise, operation: () -> Any?) {
        worker.execute {
            try { promise.resolve(operation()) }
            catch (error: Exception) { promise.reject("TRACKING_ERROR", "The operation could not complete. Check your signed-in account, pending session and connection.") }
        }
    }

    @ReactMethod fun initialize(promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        TrackingSync.kick(context)
        store.path
    }
    @ReactMethod fun snapshot(promise: Promise) = perform(promise) {
        TrackingStore.get(context).snapshot().put("serviceRunning", TrackingService.running).toString()
    }
    @ReactMethod fun matchesDatabase(candidate: String, promise: Promise) = perform(promise) {
        if (!File(candidate).isAbsolute) false else runCatching {
            val native = Os.stat(TrackingStore.get(context).path)
            val opened = Os.stat(candidate)
            native.st_dev == opened.st_dev && native.st_ino == opened.st_ino
        }.getOrDefault(false)
    }
    @ReactMethod fun start(phone: String, target: String, expectedUid: String, expectedProject: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        require(store.currentSession() == null) { "Finish or resume the existing session first" }
        TrackingService.stopAndWait(context)
        val id = synchronized(store) {
            requireExpected(phone, target, expectedUid, expectedProject)
            store.start(phone, target, System.currentTimeMillis())
        }
        try { TrackingService.start(context) }
        catch (error: Exception) { store.interrupt("Background recording could not start", id); throw error }
        id
    }
    @ReactMethod fun resume(expectedUid: String, expectedProject: String, expectedSessionId: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        synchronized(store) {
            requireSigned(expectedUid, expectedProject)
            require(store.currentSession()?.optString("session_id") == expectedSessionId) { "The recording session changed" }
            require(store.resume()) { "No session to resume" }
        }
        try { TrackingService.start(context) }
        catch (error: Exception) { store.interrupt("Background recording could not resume", expectedSessionId); throw error }
        true
    }
    @ReactMethod fun stop(expectedUid: String, expectedProject: String, expectedSessionId: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        synchronized(store) {
            requireSigned(expectedUid, expectedProject)
            require(store.currentSession()?.optString("session_id") == expectedSessionId) { "The recording session changed" }
            store.stop(System.currentTimeMillis(), expectedSessionId)
        }
        TrackingService.stopAndWait(context)
        TrackingSync.kick(context)
        true
    }
    @ReactMethod fun retrySync(expectedUid: String, expectedProject: String, expectedPhone: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        synchronized(store) {
            require(requireSigned(expectedUid, expectedProject).phone == expectedPhone) { "The account binding changed" }
            store.retry()
        }
        TrackingSync.kick(context)
        true
    }
    private fun requireSigned(uid: String, project: String): AuthOwner {
        val owner = MobileIdentity.currentOwner(context) ?: error("Sign in with a verified account")
        return requireExpected(owner.phone, owner.target, uid, project)
    }

    private fun requireExpected(phone: String, target: String, uid: String, project: String): AuthOwner {
        val owner = MobileIdentity.requireOwner(context, phone, TrackingCore.requireTarget(target))
        require(owner.uid == uid && owner.projectId == project) { "The signed-in account changed" }
        return owner
    }

    @ReactMethod fun queueRecord(phone: String, target: String, category: String, id: String, payload: String, deleted: Boolean, expectedUid: String, expectedProject: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        val result = synchronized(store) {
            requireExpected(phone, target, expectedUid, expectedProject)
            store.queueRecord(phone, target, category, id, payload, deleted)
        }
        TrackingSync.kick(context)
        result.toString()
    }
    @ReactMethod fun records(phone: String, category: String, expectedUid: String, expectedProject: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        synchronized(store) {
            val owner = MobileIdentity.currentOwner(context) ?: error("Sign in with a verified account")
            requireExpected(phone, owner.target, expectedUid, expectedProject)
            store.records(phone, category).toString()
        }
    }
    @ReactMethod fun refreshRecords(phone: String, target: String, category: String, expectedUid: String, expectedProject: String, promise: Promise) {
        networkWorker.execute {
            try {
                val owner = requireExpected(phone, target, expectedUid, expectedProject)
                require(category in setOf("booked_events", "rescue_requests"))
                val response = FirebaseRestTransport(context).request(owner, "users/$phone/$category", "GET")
                require(response.code in 200..299) { "Record refresh is unavailable" }
                val store = TrackingStore.get(context)
                val result = synchronized(store) {
                    requireExpected(phone, target, expectedUid, expectedProject)
                    store.mergeRecords(owner, category, response.body)
                    store.records(phone, category).toString()
                }
                promise.resolve(result)
            } catch (_: Exception) {
                promise.reject("TRACKING_REFRESH_ERROR", "Records could not refresh. Check your signed-in account and connection.")
            }
        }
    }
    @ReactMethod fun profile(phone: String, target: String, method: String, payload: String, expectedUid: String, expectedProject: String, promise: Promise) {
        networkWorker.execute {
            try {
                val owner = requireExpected(phone, target, expectedUid, expectedProject)
                require(method == "GET" || method == "PUT")
                val body = if (method == "PUT") JSONObject(payload).also {
                    require(it.optString("phone") == owner.phone) { "Profile phone does not match the account binding" }
                }.toString() else null
                val response = FirebaseRestTransport(context).request(owner, "users/$phone/profile", method, body)
                require(response.code in 200..299) { "Profile request is unavailable" }
                requireExpected(phone, target, expectedUid, expectedProject)
                promise.resolve(response.body)
            } catch (_: Exception) {
                promise.reject("TRACKING_PROFILE_ERROR", "Profile request could not complete. Check your signed-in account and connection.")
            }
        }
    }
    @ReactMethod fun listPoints(sessionId: String, afterSequence: Double, limit: Int, expectedUid: String, expectedProject: String, promise: Promise) = perform(promise) {
        require(afterSequence.isFinite() && afterSequence >= 0 && afterSequence == afterSequence.toLong().toDouble())
        val store = TrackingStore.get(context)
        synchronized(store) {
            requireSigned(expectedUid, expectedProject)
            store.listPoints(sessionId, afterSequence.toLong(), limit).toString()
        }
    }
    @ReactMethod fun clearHistory(expectedUid: String, expectedProject: String, expectedPhone: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        synchronized(store) {
            require(requireSigned(expectedUid, expectedProject).phone == expectedPhone) { "The account binding changed" }
            store.clearTrackingData()
        }
        true
    }
}

class TrackingPackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(TrackingModule(context))
    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}
