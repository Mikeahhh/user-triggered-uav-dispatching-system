package com.fypproject.tracking

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
            catch (error: Exception) { promise.reject("TRACKING_ERROR", error.message, error) }
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
    @ReactMethod fun start(phone: String, target: String, promise: Promise) = perform(promise) {
        val store = TrackingStore.get(context)
        require(store.currentSession() == null) { "Finish or resume the existing session first" }
        TrackingService.stopAndWait(context)
        val id = store.start(phone, target, System.currentTimeMillis())
        try { TrackingService.start(context) }
        catch (error: Exception) { store.interrupt(error.message ?: "Background recording could not start", id); throw error }
        id
    }
    @ReactMethod fun resume(promise: Promise) = perform(promise) {
        require(TrackingStore.get(context).currentSession() != null) { "No session to resume" }
        TrackingService.start(context)
        true
    }
    @ReactMethod fun stop(promise: Promise) = perform(promise) {
        TrackingStore.get(context).stop(System.currentTimeMillis())
        TrackingService.stopAndWait(context)
        TrackingSync.kick(context)
        true
    }
    @ReactMethod fun retrySync(promise: Promise) = perform(promise) {
        TrackingStore.get(context).retry()
        TrackingSync.kick(context)
        true
    }
    @ReactMethod fun queueRecord(phone: String, target: String, category: String, id: String, payload: String, deleted: Boolean, promise: Promise) = perform(promise) {
        val result = TrackingStore.get(context).queueRecord(phone, target, category, id, payload, deleted)
        TrackingSync.kick(context)
        result.toString()
    }
    @ReactMethod fun records(phone: String, category: String, promise: Promise) = perform(promise) {
        TrackingStore.get(context).records(phone, category).toString()
    }
    @ReactMethod fun refreshRecords(phone: String, target: String, category: String, promise: Promise) {
      networkWorker.execute {
       try {
        TrackingCore.requirePhone(phone)
        require(category in setOf("booked_events", "rescue_requests"))
        val response = FirebaseRestTransport().request(target, "users/$phone/$category", "GET")
        require(response.code in 200..299) { "Record refresh is unavailable" }
        TrackingStore.get(context).mergeRecords(phone, category, response.body)
        promise.resolve(TrackingStore.get(context).records(phone, category).toString())
       } catch (error: Exception) { promise.reject("TRACKING_REFRESH_ERROR", error.message, error) }
      }
    }
    @ReactMethod fun listPoints(sessionId: String, afterSequence: Double, limit: Int, promise: Promise) = perform(promise) {
        require(afterSequence.isFinite() && afterSequence >= 0 && afterSequence == afterSequence.toLong().toDouble())
        TrackingStore.get(context).listPoints(sessionId, afterSequence.toLong(), limit).toString()
    }
    @ReactMethod fun clearHistory(promise: Promise) = perform(promise) {
        TrackingStore.get(context).clearTrackingData()
        true
    }
}

class TrackingPackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(TrackingModule(context))
    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}
