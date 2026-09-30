package com.fypproject.tracking

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

object TrackingSync {
    private val running = AtomicBoolean(false)
    private val executor = Executors.newSingleThreadExecutor()
    private const val JOB_ID = 260930

    fun kick(context: Context, onDone: ((Boolean) -> Unit)? = null) {
        if (!running.compareAndSet(false, true)) {
            onDone?.invoke(true)
            return
        }
        executor.execute {
            var pending = true
            try {
                val store = TrackingStore.get(context)
                val transport = FirebaseRestTransport()
                val deadline = System.currentTimeMillis() + 20000
                while (System.currentTimeMillis() < deadline) {
                    val write = store.nextWrite(System.currentTimeMillis()) ?: break
                    try {
                        transport.send(write)
                        store.acknowledge(write, System.currentTimeMillis())
                    } catch (error: Exception) {
                        store.failed(write, error.message ?: "Synchronization unavailable", System.currentTimeMillis())
                    }
                }
                pending = store.hasPending()
            } finally {
                running.set(false)
                if (pending && onDone == null) schedule(context)
                onDone?.invoke(pending)
            }
        }
    }

    fun schedule(context: Context) {
        val job = JobInfo.Builder(JOB_ID, ComponentName(context, TrackingSyncJob::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setMinimumLatency(15000)
            .setBackoffCriteria(30000, JobInfo.BACKOFF_POLICY_EXPONENTIAL)
            .build()
        context.getSystemService(JobScheduler::class.java).schedule(job)
    }
}

class TrackingSyncJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        TrackingSync.kick(applicationContext) { pending -> jobFinished(params, pending) }
        return true
    }
    override fun onStopJob(params: JobParameters): Boolean = true
}
