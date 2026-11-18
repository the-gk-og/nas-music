package org.nasmusic.app.context

import android.Manifest
import android.os.Build
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** docs/v2.md §10: every opt-in signal, all off by default. */
enum class ContextSignal(val key: String, val title: String, val explanation: String) {
    LISTENING("listening", "Listening details",
        "Seeks, volume changes, repeats and listening sessions. Helps tell 'skipped the intro' from 'skipped the song'."),
    OUTPUT("output", "Audio output",
        "Speaker, wired, Bluetooth device name or car. Lets mixes differ for headphones vs the car."),
    DEVICE("device", "Device & network",
        "Wi-Fi/mobile data, charging, battery level and screen on/off when a song plays."),
    MOTION("motion", "Movement",
        "Still / walking / running / in a vehicle and step cadence, from the step sensor. Enables workout and commute mixes."),
    LOCATION("location", "Location",
        "Places you listen at (home, work, gym…). Coordinates stay on the phone; only the place name and a ~5 km area code are sent."),
    WEATHER("weather", "Weather",
        "The server looks up the weather for that ~5 km area (needs Location). Rainy-day picks."),
    SEARCH("search", "Search history",
        "What you searched for when a song was played from search results."),
    TIME("time", "Time & calendar",
        "Local hour and weekday from the phone clock (no calendar access). Morning vs late-night mixes.");

    /** Runtime permissions asked only when this switch is turned on. */
    fun permissions(): List<String> = when (this) {
        MOTION -> if (Build.VERSION.SDK_INT >= 29) listOf(Manifest.permission.ACTIVITY_RECOGNITION) else emptyList()
        LOCATION -> listOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
        else -> emptyList()
    }

    val settingsKey: String get() = "context.$key"
}

/** Player-derived details for one track play (the "Listening details" signal). */
data class ListeningDetails(
    val seeks: List<Pair<Long, Long>> = emptyList(),
    /** (position in the track ms, new volume 0..1) */
    val volumeChanges: List<Pair<Long, Float>> = emptyList(),
    val repeatOfPrevious: Boolean = false,
    val addedFrom: String? = null,
    val sessionId: String? = null,
    val sessionPosition: Int = 0,
)

/** Everything gathered at the moment of an event; null = not available. */
data class ContextInputs(
    val listening: ListeningDetails? = null,
    val output: String? = null,
    val network: String? = null,
    val charging: Boolean? = null,
    val batteryPct: Int? = null,
    val screenOn: Boolean? = null,
    val motion: String? = null,
    val cadenceSpm: Int? = null,
    val place: String? = null,
    val geohash5: String? = null,
    val searchQuery: String? = null,
    val localHour: Int? = null,
    val weekday: Int? = null,
)

object ContextBuilder {
    /** The `context` object for an event, containing only fields of enabled signals; null if nothing to send. */
    fun build(enabled: Set<ContextSignal>, i: ContextInputs): JsonObject? {
        if (enabled.isEmpty()) return null
        val o = buildJsonObject {
            if (ContextSignal.LISTENING in enabled) i.listening?.let { l ->
                put("seeks", buildJsonArray { l.seeks.forEach { (from, to) -> add(buildJsonObject { put("fromMs", from); put("toMs", to) }) } })
                put("volumeChanges", buildJsonArray { l.volumeChanges.forEach { (at, v) -> add(buildJsonObject { put("atMs", at); put("volume", v) }) } })
                put("repeatOfPrevious", l.repeatOfPrevious)
                l.addedFrom?.let { put("addedFrom", it) }
                l.sessionId?.let { put("sessionId", it) }
                put("sessionPosition", l.sessionPosition)
            }
            if (ContextSignal.OUTPUT in enabled) i.output?.let { put("output", it) }
            if (ContextSignal.DEVICE in enabled) {
                put("deviceKind", "android")
                i.network?.let { put("network", it) }
                i.charging?.let { put("charging", it) }
                i.batteryPct?.let { put("batteryPct", it) }
                i.screenOn?.let { put("screenOn", it) }
            }
            if (ContextSignal.MOTION in enabled) {
                i.motion?.let { put("motion", it) }
                i.cadenceSpm?.let { put("cadenceSpm", it) }
            }
            if (ContextSignal.LOCATION in enabled) {
                i.place?.let { put("place", it) }
                i.geohash5?.let { put("geohash5", it) }
            }
            if (ContextSignal.SEARCH in enabled) i.searchQuery?.let { put("searchQuery", it) }
            if (ContextSignal.TIME in enabled) {
                i.localHour?.let { put("localHour", it) }
                i.weekday?.let { put("weekday", it) }
            }
        }
        return o.takeIf { it.isNotEmpty() }
    }
}

object MotionClassifier {
    /** Speed (m/s, from location if enabled) wins over cadence for vehicles. */
    fun classify(cadenceSpm: Int?, speedMs: Float?): String = when {
        speedMs != null && speedMs > 7f -> "vehicle"
        cadenceSpm == null || cadenceSpm < 40 -> "still"
        cadenceSpm >= 140 -> "running"
        else -> "walking"
    }
}

object Geohash {
    private const val BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

    fun encode(lat: Double, lon: Double, precision: Int = 5): String {
        var latLo = -90.0; var latHi = 90.0; var lonLo = -180.0; var lonHi = 180.0
        val sb = StringBuilder()
        var bit = 0; var ch = 0; var even = true
        while (sb.length < precision) {
            if (even) {
                val mid = (lonLo + lonHi) / 2
                if (lon >= mid) { ch = ch or (1 shl (4 - bit)); lonLo = mid } else lonHi = mid
            } else {
                val mid = (latLo + latHi) / 2
                if (lat >= mid) { ch = ch or (1 shl (4 - bit)); latLo = mid } else latHi = mid
            }
            even = !even
            if (bit < 4) bit++ else { sb.append(BASE32[ch]); bit = 0; ch = 0 }
        }
        return sb.toString()
    }
}

@Serializable
data class PlaceCluster(val id: Int, val label: String, val lat: Double, val lon: Double, val count: Int)

/**
 * On-device place clustering: a location joins the nearest cluster within [radiusM] (its centre
 * moves to the running mean), otherwise starts "place-N". Coordinates never leave the phone.
 */
class PlaceClusterer(initial: List<PlaceCluster> = emptyList(), private val radiusM: Double = 250.0) {
    private val clusters = initial.toMutableList()
    val all: List<PlaceCluster> get() = clusters.toList()

    fun assign(lat: Double, lon: Double): PlaceCluster {
        val nearest = clusters.minByOrNull { distanceM(lat, lon, it.lat, it.lon) }
        if (nearest != null && distanceM(lat, lon, nearest.lat, nearest.lon) <= radiusM) {
            val n = nearest.count + 1
            val moved = nearest.copy(lat = nearest.lat + (lat - nearest.lat) / n, lon = nearest.lon + (lon - nearest.lon) / n, count = n)
            clusters[clusters.indexOf(nearest)] = moved
            return moved
        }
        val id = (clusters.maxOfOrNull { it.id } ?: 0) + 1
        return PlaceCluster(id, "place-$id", lat, lon, 1).also { clusters += it }
    }

    fun rename(id: Int, label: String) {
        val i = clusters.indexOfFirst { it.id == id }
        if (i >= 0) clusters[i] = clusters[i].copy(label = label.trim().ifBlank { "place-$id" })
    }

    fun forget(id: Int) { clusters.removeAll { it.id == id } }

    companion object {
        fun distanceM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
            val r = 6_371_000.0
            val dLat = Math.toRadians(lat2 - lat1)
            val dLon = Math.toRadians(lon2 - lon1)
            val a = sin(dLat / 2).let { it * it } +
                cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) * sin(dLon / 2).let { it * it }
            return 2 * r * atan2(sqrt(a), sqrt(1 - a))
        }
    }
}
