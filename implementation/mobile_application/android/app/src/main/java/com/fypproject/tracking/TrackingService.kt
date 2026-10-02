package com.fypproject.tracking

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.os.Build
import android.os.Handler
import android.os.IBinder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import android.os.Looper
import com.fypproject.MainActivity
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority

class TrackingService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private val client by lazy { LocationServices.getFusedLocationProviderClient(this) }
    private var subscribed = false
    private var foregroundReady = false
    private var ownedSessionId: String? = null
    private val callback = object : LocationCallback() {
        override fun onLocationResult(result: LocationResult) {
            for (location in result.locations) save(location)
        }
    }
    private val sync = object : Runnable {
        override fun run() {
            if (TrackingStore.get(this@TrackingService).currentSession() == null) {
                stopSelf()
                return
            }
            TrackingSync.kick(applicationContext)
            handler.postDelayed(this, 5000)
        }
    }

    override fun onCreate() {
        super.onCreate()
        synchronized(lifecycle) {
            if (!requested) { requested = true; stopped = CountDownLatch(1) }
        }
        ownedSessionId = TrackingStore.get(this).currentSession()?.getString("session_id")
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Hiking location recording", NotificationManager.IMPORTANCE_LOW)
        )
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, TrackingService::class.java).setAction(STOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
        val notification = builder.setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("Quick Start")
            .setContentText("Recording your hiking route. Open the app to view status.")
            .setContentIntent(open).setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Stop recording", stop).build()).build()
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(930, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
            else startForeground(930, notification)
            foregroundReady = true
            running = true
        } catch (error: Exception) {
            runCatching { TrackingStore.get(this).interrupt(error.message ?: "Background recording could not start", ownedSessionId) }
            stopSelf()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val store = TrackingStore.get(this)
        if (!foregroundReady) { stopSelf(); return START_NOT_STICKY }
        if (intent?.action == STOP) {
            store.stop(System.currentTimeMillis())
            TrackingSync.kick(applicationContext)
            stopSelf()
            return START_NOT_STICKY
        }
        if (store.currentSession() == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            store.interrupt("Location permission was withdrawn. Open the app to resume.", ownedSessionId)
            stopSelf()
            return START_NOT_STICKY
        }
        if (!subscribed) {
            ownedSessionId = store.currentSession()?.getString("session_id")
            store.resume()
            try {
                val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, TrackingCore.INTERVAL_MS)
                    .setMinUpdateIntervalMillis(TrackingCore.INTERVAL_MS).setMaxUpdateDelayMillis(TrackingCore.INTERVAL_MS)
                    .setWaitForAccurateLocation(true).build()
                client.requestLocationUpdates(request, callback, Looper.getMainLooper())
                    .addOnFailureListener { error ->
                        store.interrupt(error.message ?: "Location updates are unavailable", ownedSessionId)
                        stopSelf()
                    }
                subscribed = true
                handler.post(sync)
            } catch (error: Exception) {
                store.interrupt(error.message ?: "Location updates are unavailable", ownedSessionId)
                stopSelf()
            }
        }
        return START_STICKY
    }

    private fun save(location: Location) {
        try {
            val accepted = TrackingStore.get(this).append(RecordedFix(
                location.latitude, location.longitude, location.time,
                if (location.hasAccuracy()) location.accuracy.toDouble() else null,
                if (location.hasAltitude()) location.altitude else null,
                if (location.hasSpeed()) location.speed.toDouble() else null,
                if (location.hasBearing()) location.bearing.toDouble() else null
            ), System.currentTimeMillis(), ownedSessionId)
            if (accepted) TrackingSync.kick(applicationContext)
        } catch (error: Exception) {
            runCatching { TrackingStore.get(this).interrupt("Location could not be saved: ${error.message}", ownedSessionId) }
            stopSelf()
        }
    }

    override fun onDestroy() {
        try {
            client.removeLocationUpdates(callback)
            handler.removeCallbacksAndMessages(null)
            runCatching { TrackingStore.get(this).interrupt("Recording was interrupted. Open the app to resume.", ownedSessionId) }
            TrackingSync.schedule(applicationContext)
        } finally {
            super.onDestroy()
            synchronized(lifecycle) {
                running = false
                requested = false
                stopped.countDown()
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        const val STOP = "com.fypproject.tracking.STOP"
        private const val CHANNEL = "quick_start_tracking"
        private val lifecycle = Any()
        private var stopped = CountDownLatch(0)
        @Volatile var running = false
        @Volatile private var requested = false
        fun start(context: Context) {
            synchronized(lifecycle) {
                if (!running && !requested) {
                    requested = true
                    stopped = CountDownLatch(1)
                }
            }
            try {
                val intent = Intent(context, TrackingService::class.java)
                if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
            } catch (error: Exception) {
                synchronized(lifecycle) {
                    if (!running) { requested = false; stopped.countDown() }
                }
                throw error
            }
        }
        fun stopAndWait(context: Context) {
            val latch = synchronized(lifecycle) { if (!running && !requested) return else stopped }
            val existed = context.stopService(Intent(context, TrackingService::class.java))
            if (!existed && !running) synchronized(lifecycle) { requested = false; stopped.countDown() }
            require(latch.await(5, TimeUnit.SECONDS)) { "The previous recording service is still stopping. Retry shortly." }
        }
    }
}
