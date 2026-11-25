package org.homebeat.app.alarm

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.time.DayOfWeek
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.homebeat.app.MainActivity
import org.homebeat.app.R
import org.homebeat.app.container
import org.homebeat.app.playback.PlaybackService

/**
 * Wake-up alarm: at a set time the phone starts a mix, a radio or an internet radio station and fades it in.
 * It lives entirely on the phone (AlarmManager), so it works without the server being told anything; the
 * playback service resolves the source when it fires and falls back to downloads, then to the system alarm tone.
 */
@Serializable
data class WakeAlarm(
    val enabled: Boolean = false,
    val hour: Int = 7,
    val minute: Int = 0,
    /** ISO days of the week (1 = Monday … 7 = Sunday). Empty = once, at the next [hour]:[minute]. */
    val days: Set<Int> = emptySet(),
    val source: AlarmSource = AlarmSource.MORNING_MIX,
    /** Seconds from silent to full volume. */
    val fadeInS: Int = 60,
    /** Raise the phone's media volume to at least this share of maximum when the alarm starts (0 = leave it). */
    val minVolumePct: Int = 40,
    val snoozeMin: Int = 9,
)

/**
 * What to play.
 * - `mix`: today's mix of kind [id] (`morning`, `daily`, `evening`, …; mix ids change every night, kinds don't)
 * - `moment`: "Play something for now" (smart shuffle for the time of day)
 * - `random`: "Your radio"
 * - `playlist`: playlist [id]
 * - `station`: internet radio station [id]
 */
@Serializable
data class AlarmSource(val type: String, val id: String? = null, val label: String) {
    companion object {
        val MORNING_MIX = AlarmSource("mix", "morning", "Morning Mix")
        val MOMENT = AlarmSource("moment", null, "Play something for now")
        val RANDOM = AlarmSource("random", null, "Your radio")
    }
}

/** Pure time arithmetic (unit-tested). */
object AlarmTimes {
    /** The next time [a] rings strictly after [now], or null when it is off. */
    fun next(a: WakeAlarm, now: ZonedDateTime): ZonedDateTime? {
        if (!a.enabled) return null
        val at = LocalTime.of(a.hour.coerceIn(0, 23), a.minute.coerceIn(0, 59))
        for (i in 0L..7L) {
            // atZone handles DST gaps (07:30 that doesn't exist becomes 08:30) and overlaps (earlier offset).
            val t = now.toLocalDate().plusDays(i).atTime(at).atZone(now.zone)
            if (t.isAfter(now) && (a.days.isEmpty() || t.dayOfWeek.value in a.days)) return t
        }
        return null
    }

    /** "Weekdays", "Every day", "Sat, Sun", or "Once". */
    fun daysLabel(days: Set<Int>, locale: Locale = Locale.getDefault()): String = when {
        days.isEmpty() -> "Once"
        days.size == 7 -> "Every day"
        days == setOf(1, 2, 3, 4, 5) -> "Weekdays"
        days == setOf(6, 7) -> "Weekends"
        else -> days.sorted().joinToString(", ") { DayOfWeek.of(it).getDisplayName(TextStyle.SHORT, locale) }
    }

    /** "Rings in 7 h 5 min" (or "in 1 min" when under a minute). */
    fun inLabel(next: ZonedDateTime, now: ZonedDateTime): String {
        val mins = ((java.time.Duration.between(now, next).toMillis() + 59_999) / 60_000).coerceAtLeast(1)
        val d = mins / (24 * 60)
        val h = (mins % (24 * 60)) / 60
        val m = mins % 60
        return "Rings in " + listOfNotNull(d.takeIf { it > 0 }?.let { "$it d" }, h.takeIf { it > 0 }?.let { "$it h" },
            m.takeIf { it > 0 || (d == 0L && h == 0L) }?.let { "$it min" }).joinToString(" ")
    }

    /** Fade-in level after [elapsedMs] of playing: eased, so the first seconds are a whisper. */
    fun fadeLevel(elapsedMs: Long, fadeMs: Long): Float {
        if (fadeMs <= 0 || elapsedMs >= fadeMs) return 1f
        val x = (elapsedMs.coerceAtLeast(0).toFloat() / fadeMs)
        return (0.02f + 0.98f * x * x).coerceIn(0f, 1f)
    }

    /** The volume index to raise the stream to, or null to leave it as it is. */
    fun volumeFloor(current: Int, max: Int, minPct: Int): Int? {
        if (minPct <= 0 || max <= 0) return null
        val want = (max * minPct.coerceAtMost(100) + 99) / 100
        return want.takeIf { it > current }
    }
}

/** The alarm settings plus a pending snooze, in SharedPreferences. */
class AlarmStore(context: Context) {
    private val prefs = context.getSharedPreferences("wake_alarm", Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private val _alarm = MutableStateFlow(
        prefs.getString(KEY, null)?.let { runCatching { json.decodeFromString(WakeAlarm.serializer(), it) }.getOrNull() } ?: WakeAlarm(),
    )
    val alarm: StateFlow<WakeAlarm> = _alarm.asStateFlow()

    private val _snoozeUntil = MutableStateFlow(prefs.getLong(KEY_SNOOZE, 0L).takeIf { it > System.currentTimeMillis() })
    /** Epoch ms of a pending snooze, or null. */
    val snoozeUntil: StateFlow<Long?> = _snoozeUntil.asStateFlow()

    fun set(a: WakeAlarm) {
        _alarm.value = a
        prefs.edit().putString(KEY, json.encodeToString(WakeAlarm.serializer(), a)).apply()
    }

    fun setSnooze(atMs: Long?) {
        _snoozeUntil.value = atMs
        prefs.edit().putLong(KEY_SNOOZE, atMs ?: 0L).apply()
    }

    private companion object {
        const val KEY = "alarm"
        const val KEY_SNOOZE = "snoozeUntil"
    }
}

object AlarmScheduler {
    private const val REQ_MAIN = 8101
    private const val REQ_SNOOZE = 8102
    private const val REQ_SHOW = 8103

    /** Exact alarms allowed? (Always below Android 12; USE_EXACT_ALARM covers 13+, the 12/12L grant can be revoked.) */
    fun canExact(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 31 || ctx.getSystemService(AlarmManager::class.java)?.canScheduleExactAlarms() == true

    /** (Re)arm the alarm from the saved settings: call after every change, boot, time or time-zone change. */
    fun apply(ctx: Context) {
        val c = ctx.container
        val am = ctx.getSystemService(AlarmManager::class.java) ?: return
        val pi = fireIntent(ctx, REQ_MAIN)
        val next = AlarmTimes.next(c.alarm.alarm.value, ZonedDateTime.now())
        if (next == null) am.cancel(pi) else setAt(ctx, am, next.toInstant().toEpochMilli(), pi)
        // A snooze survives a reboot too.
        c.alarm.snoozeUntil.value?.let { at ->
            if (at > System.currentTimeMillis()) setAt(ctx, am, at, fireIntent(ctx, REQ_SNOOZE)) else c.alarm.setSnooze(null)
        }
    }

    fun snooze(ctx: Context, minutes: Int): Long {
        val at = System.currentTimeMillis() + minutes.coerceAtLeast(1) * 60_000L
        ctx.container.alarm.setSnooze(at)
        ctx.getSystemService(AlarmManager::class.java)?.let { setAt(ctx, it, at, fireIntent(ctx, REQ_SNOOZE)) }
        return at
    }

    fun cancelSnooze(ctx: Context) {
        ctx.container.alarm.setSnooze(null)
        ctx.getSystemService(AlarmManager::class.java)?.cancel(fireIntent(ctx, REQ_SNOOZE))
    }

    private fun setAt(ctx: Context, am: AlarmManager, atMs: Long, pi: PendingIntent) {
        try {
            if (canExact(ctx)) {
                // An alarm clock: exact even in Doze, shown in the status bar / lock screen, and lets us start playback.
                am.setAlarmClock(AlarmManager.AlarmClockInfo(atMs, showIntent(ctx)), pi)
            } else {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
            }
        } catch (_: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
        }
    }

    private fun fireIntent(ctx: Context, req: Int): PendingIntent = PendingIntent.getBroadcast(
        ctx, req, Intent(ctx, AlarmReceiver::class.java).setAction(AlarmReceiver.ACTION_FIRE).putExtra(AlarmReceiver.EXTRA_SNOOZE, req == REQ_SNOOZE),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    /** Tapping the alarm icon in the status bar / clock opens the alarm screen. */
    fun showIntent(ctx: Context): PendingIntent = PendingIntent.getActivity(
        ctx, REQ_SHOW, Intent(ctx, MainActivity::class.java).putExtra(MainActivity.EXTRA_ROUTE, ROUTE).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    const val ROUTE = "alarm"
}

/** Fires the alarm, and re-arms it after a reboot, an app update or a clock / time-zone change. */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val app = ctx.applicationContext
        if (intent.action != ACTION_FIRE) { AlarmScheduler.apply(app); return }
        val c = app.container
        val snooze = intent.getBooleanExtra(EXTRA_SNOOZE, false)
        if (snooze) c.alarm.setSnooze(null)
        else {
            val a = c.alarm.alarm.value
            if (!a.enabled) return
            if (a.days.isEmpty()) c.alarm.set(a.copy(enabled = false)) // a one-off alarm turns itself off
        }
        AlarmScheduler.apply(app) // the next repeat
        val start = Intent(app, PlaybackService::class.java).setAction(AlarmRun.ACTION_START)
        try {
            ContextCompat.startForegroundService(app, start)
        } catch (e: Exception) {
            // Not allowed to start playback from here (inexact alarm without the exact-alarm grant): ring instead.
            android.util.Log.w("WakeAlarm", "couldn't start playback", e)
            AlarmNotifications.fallback(app)
        }
    }

    companion object {
        const val ACTION_FIRE = "org.homebeat.alarm.FIRE"
        const val EXTRA_SNOOZE = "snooze"
    }
}

object AlarmNotifications {
    const val CHANNEL = "wake_alarm"
    private const val CHANNEL_RING = "wake_alarm_ring"
    const val ID = 7401
    private const val FALLBACK_ID = 7402

    fun ensureChannels(ctx: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Wake-up alarm", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Stop and snooze while the wake-up alarm plays"
                    setSound(null, null)
                },
            )
        }
        if (nm.getNotificationChannel(CHANNEL_RING) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_RING, "Wake-up alarm (when music can't start)", NotificationManager.IMPORTANCE_HIGH).apply {
                    setSound(
                        RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                        AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).build(),
                    )
                    enableVibration(true)
                },
            )
        }
    }

    private fun clock(ctx: Context, atMs: Long): String =
        java.time.Instant.ofEpochMilli(atMs).atZone(java.time.ZoneId.systemDefault()).toLocalTime()
            .format(DateTimeFormatter.ofPattern(if (android.text.format.DateFormat.is24HourFormat(ctx)) "HH:mm" else "h:mm a"))

    private fun serviceIntent(ctx: Context, action: String, req: Int): PendingIntent = PendingIntent.getService(
        ctx, req, Intent(ctx, PlaybackService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun base(ctx: Context, channel: String) = NotificationCompat.Builder(ctx, channel)
        .setSmallIcon(R.drawable.ic_alarm)
        .setCategory(NotificationCompat.CATEGORY_ALARM)
        .setPriority(NotificationCompat.PRIORITY_HIGH)
        .setContentIntent(AlarmScheduler.showIntent(ctx))

    /** While it plays: Stop / Snooze. */
    fun ringing(ctx: Context, what: String, snoozeMin: Int) = base(ctx, CHANNEL).also { ensureChannels(ctx) }
        .setContentTitle("Wake-up alarm")
        .setContentText(what)
        .setOngoing(true)
        .addAction(0, "Stop", serviceIntent(ctx, AlarmRun.ACTION_STOP, 1))
        .addAction(0, "Snooze $snoozeMin min", serviceIntent(ctx, AlarmRun.ACTION_SNOOZE, 2))
        .build()

    fun snoozed(ctx: Context, atMs: Long) = base(ctx, CHANNEL).also { ensureChannels(ctx) }
        .setContentTitle("Alarm snoozed")
        .setContentText("Rings again at ${clock(ctx, atMs)}")
        .setOngoing(true)
        .addAction(0, "Dismiss", serviceIntent(ctx, AlarmRun.ACTION_STOP, 1))
        .build()

    /** Playback couldn't even be started: at least ring the system alarm tone. */
    fun fallback(ctx: Context) {
        ensureChannels(ctx)
        val n = base(ctx, CHANNEL_RING)
            .setContentTitle("Wake-up alarm")
            .setContentText("Tap to open Homebeat")
            .setAutoCancel(true)
            .build()
        runCatching { ctx.getSystemService(NotificationManager::class.java)?.notify(FALLBACK_ID, n) }
    }
}
