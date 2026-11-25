package org.homebeat.wear.sleep

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.text.DateFormat
import java.util.Date
import kotlin.math.abs
import kotlin.math.sqrt
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.homebeat.wear.R
import org.homebeat.wear.WearApp
import org.homebeat.wear.ui.MainActivity

/**
 * Sleep detection (opt-in, Settings → "Pause when I fall asleep"). While it runs it reads the
 * accelerometer and heart-rate sensor, feeds 30-second epochs to [SleepDetector], and when you fall
 * asleep with something eligible playing (here, or on the phone/speaker the watch is a remote for) it
 * fades out over 30 s, pauses, and moves a podcast/audiobook back to where it was when you last moved.
 * Moving your wrist during the fade cancels it.
 *
 * It is a foreground service (type "health") holding a partial wake lock, so it only runs while armed:
 * it stops after pausing, after [IDLE_STOP_MS] with nothing playing, after [MAX_RUN_MS], or from the
 * notification's Stop. Only raw Android sensors are used (no Health Services / Play Services).
 */
class SleepWatchService : Service(), SensorEventListener {
    private val c by lazy { (application as WearApp).container }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var loop: Job? = null
    private lateinit var sensorThread: HandlerThread
    private var wakeLock: PowerManager.WakeLock? = null
    private val builder = EpochBuilder()
    private val detector = SleepDetector()

    /** Last time the wrist clearly moved (a jolt well above the resting noise): cancels a fade. */
    @Volatile private var lastJoltMs = 0L
    private var ema = 0f
    @Volatile private var heartRateSeen = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { finish(); return START_NOT_STICKY }
        if (loop != null) return START_NOT_STICKY
        try {
            val n = watchingNotification()
            if (Build.VERSION.SDK_INT >= 34) startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH)
            else startForeground(NOTIF_ID, n)
        } catch (e: RuntimeException) {
            // Not allowed to start from the background, or missing the health prerequisites.
            Log.w(TAG, "cannot start sleep detection", e)
            c.ui.toast("Sleep detection couldn't start")
            c.sleep.set(SleepStatus.Off)
            stopSelf()
            return START_NOT_STICKY
        }
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "homebeat:sleep")
            .apply { setReferenceCounted(false); acquire(MAX_RUN_MS + 60_000) }
        c.connect.acquire(HOLDER) // to see and pause what the phone / speaker plays
        startSensors()
        val started = System.currentTimeMillis()
        c.sleep.set(SleepStatus.Watching(started))
        loop = scope.launch { watch(started) }
        return START_NOT_STICKY
    }

    private fun startSensors() {
        sensorThread = HandlerThread("sleep-sensors").also { it.start() }
        val handler = Handler(sensorThread.looper)
        val sm = getSystemService(SensorManager::class.java)
        // ~5 Hz is plenty to tell still from moving; batched to let the CPU idle between reports.
        sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sm.registerListener(this, it, 200_000, 10_000_000, handler) }
        if (checkSelfPermission(Manifest.permission.BODY_SENSORS) == PackageManager.PERMISSION_GRANTED) {
            sm.getDefaultSensor(Sensor.TYPE_HEART_RATE)?.let { sm.registerListener(this, it, SensorManager.SENSOR_DELAY_NORMAL, handler) }
        }
    }

    override fun onSensorChanged(e: SensorEvent) {
        when (e.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                val (x, y, z) = Triple(e.values[0], e.values[1], e.values[2])
                synchronized(builder) { builder.addMotion(x, y, z) }
                val mag = sqrt(x * x + y * y + z * z)
                ema = if (ema == 0f) mag else ema * 0.9f + mag * 0.1f
                if (abs(mag - ema) > JOLT_MS2) lastJoltMs = System.currentTimeMillis()
            }
            Sensor.TYPE_HEART_RATE -> {
                val ok = e.accuracy >= SensorManager.SENSOR_STATUS_ACCURACY_LOW && e.values[0] > 0f
                if (ok) heartRateSeen = true
                synchronized(builder) { builder.addHeartRate(e.values[0], ok) }
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    /** What is playing right now, wherever. */
    private data class Now(val playing: Boolean, val kind: String?, val key: String?, val positionMs: Long)

    private fun now(): Now {
        val t = System.currentTimeMillis()
        if (c.connect.isRemote) {
            val s = c.connect.state.value ?: return Now(false, null, null, 0)
            val key = s.media?.let { "${it.kind}:${it.id}" } ?: s.trackId
            return Now(s.playing, s.media?.kind, key, s.positionAt(t))
        }
        c.player.refreshLocal()
        val u = c.player.ui.value
        return Now(u.playing, null, u.track?.id, u.positionMs) // the watch itself only plays music
    }

    private suspend fun watch(started: Long) {
        var idleSince: Long? = null
        var key: String? = null
        val positions = ArrayList<Pair<Long, Long>>()
        while (true) {
            delay(EPOCH_MS)
            val t = System.currentTimeMillis()
            val v = detector.add(synchronized(builder) { builder.close(t) })
            val p = now()
            if (p.playing) {
                idleSince = null
                if (p.key != key) { key = p.key; positions.clear() }
                positions += t to p.positionMs
                if (positions.size > 400) positions.removeAt(0)
            } else {
                if (idleSince == null) idleSince = t
                if (t - idleSince >= IDLE_STOP_MS) { finish(); return }
            }
            if (t - started >= MAX_RUN_MS) { finish(); return }
            c.sleep.set(SleepStatus.Watching(started, v.stillSinceMs, heartRateSeen))

            val prefs = c.settings.current
            if (!prefs.sleepEnabled) { finish(); return }
            if (!v.asleep || !SleepWatch.eligible(p.playing, p.kind, prefs.sleepSpokenOnly)) continue

            Log.i(TAG, "asleep (${v.reason}), still since ${v.stillSinceMs}, bpm ${v.baselineBpm} -> ${v.recentBpm}")
            c.sleep.set(SleepStatus.Fading)
            val fadeStart = System.currentTimeMillis()
            val paused = c.player.fadeOutAndPause(FADE_MS) { lastJoltMs > fadeStart }
            if (!paused) {
                // You moved: you're awake. Start over (a fresh still stretch and baseline).
                detector.reset()
                c.sleep.set(SleepStatus.Watching(started, null, heartRateSeen))
                continue
            }
            var rewound: Long? = null
            if (prefs.sleepRewind && p.kind in SleepWatch.SPOKEN && v.stillSinceMs != null) {
                rewound = SleepWatch.rewindTarget(positions, v.stillSinceMs, now().positionMs)
                rewound?.let { c.player.seekTo(it) }
            }
            c.sleep.set(SleepStatus.Paused(System.currentTimeMillis(), rewound))
            postPausedNotification(rewound != null)
            delay(3_000) // let the seek / volume commands go out before the socket may close
            finish(keepStatus = true)
            return
        }
    }

    private fun finish(keepStatus: Boolean = false) {
        if (!keepStatus) c.sleep.set(SleepStatus.Off)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        loop?.cancel(); loop = null
        scope.cancel()
        runCatching { getSystemService(SensorManager::class.java).unregisterListener(this) }
        if (::sensorThread.isInitialized) sensorThread.quitSafely()
        wakeLock?.let { if (it.isHeld) it.release() }
        c.connect.release(HOLDER)
        if (c.sleep.watching) c.sleep.set(SleepStatus.Off)
        super.onDestroy()
    }

    // ---- notifications ----

    private fun channel(): NotificationManager {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Sleep detection", NotificationManager.IMPORTANCE_LOW))
        return nm
    }

    private fun openApp() = PendingIntent.getActivity(
        this, 3, Intent(this, MainActivity::class.java).putExtra(MainActivity.EXTRA_OPEN, "now_playing"),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun watchingNotification() = channel().let {
        val stop = PendingIntent.getService(
            this, 4, Intent(this, SleepWatchService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Watching for sleep")
            .setContentText("Pauses when you doze off")
            .setContentIntent(openApp())
            .addAction(0, "Stop", stop)
            .setOngoing(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    private fun postPausedNotification(rewound: Boolean) {
        val at = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date())
        val n = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Paused at $at")
            .setContentText(if (rewound) "You fell asleep. Moved back to where you dozed off." else "You fell asleep.")
            .setContentIntent(openApp())
            .setAutoCancel(true)
            .setSilent(true)
            .build()
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) {
            channel().notify(PAUSED_NOTIF_ID, n)
        }
    }

    companion object {
        private const val TAG = "SleepWatch"
        private const val CHANNEL = "sleep"
        private const val NOTIF_ID = 51
        private const val PAUSED_NOTIF_ID = 52
        private const val HOLDER = "sleep"
        private const val ACTION_STOP = "org.homebeat.wear.sleep.STOP"
        private const val EPOCH_MS = 30_000L
        private const val FADE_MS = 30_000L
        private const val JOLT_MS2 = 1.5f
        private const val IDLE_STOP_MS = 20 * 60_000L
        private const val MAX_RUN_MS = 8 * 60 * 60_000L

        /** Arm it. Call from the foreground (an open activity): Android won't start it from the background. */
        fun start(context: Context) {
            val sleep = (context.applicationContext as WearApp).container.sleep
            if (sleep.watching) return
            sleep.set(SleepStatus.Watching(System.currentTimeMillis()))
            ContextCompat.startForegroundService(context, Intent(context, SleepWatchService::class.java))
        }

        fun stop(context: Context) {
            val sleep = (context.applicationContext as WearApp).container.sleep
            if (!sleep.watching) { if (sleep.status.value is SleepStatus.Paused) sleep.set(SleepStatus.Off); return }
            context.startService(Intent(context, SleepWatchService::class.java).setAction(ACTION_STOP))
        }
    }
}
