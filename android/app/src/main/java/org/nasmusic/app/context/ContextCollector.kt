package org.nasmusic.app.context

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Looper
import android.os.PowerManager
import androidx.core.content.ContextCompat
import java.time.LocalDateTime
import java.util.ArrayDeque
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.nasmusic.app.data.api.NasMusicApi
import org.nasmusic.app.eq.OutputDetector

/**
 * Gathers the enabled context signals without Google Play Services (docs/v2.md §10). Switches are
 * stored locally and mirrored to `/me/settings` as `context.<signal>` booleans.
 */
class ContextCollector(private val context: Context, private val scope: CoroutineScope) {
    private val prefs = context.getSharedPreferences("context_signals", Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true }

    private val _enabled = MutableStateFlow(load())
    val enabled: StateFlow<Set<ContextSignal>> = _enabled.asStateFlow()

    private val clusterer = PlaceClusterer(
        runCatching { json.decodeFromString(ListSerializer(PlaceCluster.serializer()), prefs.getString("places", null) ?: "[]") }.getOrDefault(emptyList()),
    )
    private val _places = MutableStateFlow(clusterer.all)
    val places: StateFlow<List<PlaceCluster>> = _places.asStateFlow()

    private var lastLocation: Location? = null
    private var currentPlace: PlaceCluster? = null
    private val steps = ArrayDeque<Long>()
    private var stepListener: SensorEventListener? = null
    private var locationListener: LocationListener? = null

    private fun load(): Set<ContextSignal> =
        ContextSignal.entries.filter { prefs.getBoolean(it.settingsKey, false) }.toSet()

    fun start() = applySensors()

    fun set(signal: ContextSignal, on: Boolean, api: NasMusicApi? = null) = setAll(mapOf(signal to on), api)

    fun setAll(changes: Map<ContextSignal, Boolean>, api: NasMusicApi? = null) {
        val e = prefs.edit()
        changes.forEach { (s, on) -> e.putBoolean(s.settingsKey, on) }
        e.apply()
        _enabled.value = load()
        applySensors()
        if (api != null) scope.launch {
            runCatching { api.putMySettings(org.nasmusic.app.data.api.UserSettings(changes.entries.associate { (s, on) -> s.settingsKey to JsonPrimitive(on) })) }
        }
    }

    /** Pull switches set on another of the user's devices. */
    suspend fun syncFrom(api: NasMusicApi) {
        val remote = runCatching { api.mySettings().settings }.getOrNull() ?: return
        val e = prefs.edit()
        ContextSignal.entries.forEach { s ->
            (remote[s.settingsKey] as? JsonPrimitive)?.content?.toBooleanStrictOrNull()?.let { e.putBoolean(s.settingsKey, it) }
        }
        e.apply()
        _enabled.value = load()
        applySensors()
    }

    fun hasPermissions(s: ContextSignal) = s.permissions().all {
        ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
    }

    fun renamePlace(id: Int, label: String) { clusterer.rename(id, label); savePlaces() }
    fun forgetPlace(id: Int) { clusterer.forget(id); savePlaces() }
    private fun savePlaces() {
        prefs.edit().putString("places", json.encodeToString(ListSerializer(PlaceCluster.serializer()), clusterer.all)).apply()
        _places.value = clusterer.all
    }

    @SuppressLint("MissingPermission")
    private fun applySensors() {
        val on = _enabled.value
        val sm = context.getSystemService(SensorManager::class.java)
        if (ContextSignal.MOTION in on && hasPermissions(ContextSignal.MOTION)) {
            if (stepListener == null) {
                sm?.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)?.let { sensor ->
                    stepListener = object : SensorEventListener {
                        override fun onSensorChanged(event: SensorEvent) = synchronized(steps) {
                            steps.addLast(System.currentTimeMillis()); while (steps.size > 400) steps.removeFirst()
                        }
                        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
                    }
                    sm.registerListener(stepListener, sensor, SensorManager.SENSOR_DELAY_NORMAL)
                }
            }
        } else stepListener?.let { sm?.unregisterListener(it); stepListener = null }

        val lm = context.getSystemService(LocationManager::class.java)
        if (ContextSignal.LOCATION in on && hasPermissions(ContextSignal.LOCATION) && lm != null) {
            if (locationListener == null) {
                val l = LocationListener { loc -> onLocation(loc) }
                locationListener = l
                for (p in listOf(LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER, LocationManager.PASSIVE_PROVIDER)) {
                    if (runCatching { lm.isProviderEnabled(p) }.getOrDefault(false)) {
                        runCatching { lm.requestLocationUpdates(p, 5 * 60_000L, 100f, l, Looper.getMainLooper()) }
                        runCatching { lm.getLastKnownLocation(p) }.getOrNull()?.let(::onLocation)
                    }
                }
            }
        } else locationListener?.let { lm?.removeUpdates(it); locationListener = null; lastLocation = null; currentPlace = null }
    }

    private fun onLocation(loc: Location) {
        lastLocation = loc
        currentPlace = clusterer.assign(loc.latitude, loc.longitude)
        savePlaces()
    }

    private fun cadence(): Int? = synchronized(steps) {
        val now = System.currentTimeMillis()
        while (steps.isNotEmpty() && now - steps.first() > 60_000) steps.removeFirst()
        if (stepListener == null) null else steps.size
    }

    /** Context object for an event; null when no signal is enabled. */
    fun snapshot(listening: ListeningDetails? = null, searchQuery: String? = null): JsonObject? {
        val on = _enabled.value
        if (on.isEmpty()) return null
        val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val plugged = (battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        val cad = if (ContextSignal.MOTION in on) cadence() else null
        val loc = lastLocation
        val now = LocalDateTime.now()
        return ContextBuilder.build(
            on,
            ContextInputs(
                listening = listening,
                output = if (ContextSignal.OUTPUT in on) OutputDetector.contextLabel(
                    OutputDetector.current(context.getSystemService(android.media.AudioManager::class.java)),
                ) else null,
                network = if (ContextSignal.DEVICE in on) network() else null,
                charging = plugged,
                batteryPct = if (level >= 0 && scale > 0) level * 100 / scale else null,
                screenOn = context.getSystemService(PowerManager::class.java)?.isInteractive,
                motion = if (ContextSignal.MOTION in on) MotionClassifier.classify(cad, loc?.takeIf { it.hasSpeed() }?.speed) else null,
                cadenceSpm = cad,
                place = currentPlace?.label,
                geohash5 = loc?.let { Geohash.encode(it.latitude, it.longitude, 5) },
                searchQuery = searchQuery,
                localHour = now.hour,
                weekday = now.dayOfWeek.value,
            ),
        )
    }

    private fun network(): String? {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return null
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return null
        return when {
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            else -> "other"
        }
    }
}
