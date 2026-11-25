package org.homebeat.app.ui.wrapped

import org.homebeat.app.data.api.AudioAura
import org.homebeat.app.data.api.WrappedStats
import java.text.NumberFormat
import java.util.Locale

// Wrapped story cards: a pure card model (unit-tested), drawn by WrappedRenderer onto a Bitmap that is both
// shown on screen and shared/saved as PNG — so the image is exactly what you saw. Same story as the web player.

val WEEKDAYS = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

fun hourLabel(h: Int): String = when {
    h == 0 -> "midnight"
    h == 12 -> "noon"
    h < 12 -> "$h am"
    else -> "${h - 12} pm"
}

fun fmt(n: Number): String = NumberFormat.getIntegerInstance(Locale.UK).format(Math.round(n.toDouble()))

data class CardRow(val title: String, val sub: String? = null, val value: String? = null, val albumId: String? = null)

sealed interface WrappedCard {
    val title: String
    data class Intro(override val title: String, val big: String, val caption: String) : WrappedCard
    data class ListCard(override val title: String, val rows: List<CardRow>, val heroAlbumId: String?) : WrappedCard
    data class Genres(override val title: String, val genres: List<Pair<String, Double>>) : WrappedCard
    data class Clock(override val title: String, val byHour: List<Double>, val caption: String) : WrappedCard
    data class Stat(override val title: String, val big: String, val caption: String, val sub: String? = null) : WrappedCard
    data class Aura(override val title: String, val aura: AudioAura) : WrappedCard
    data class Summary(override val title: String, val cells: List<Pair<String, String>>) : WrappedCard
}

private fun artAlbum(rel: String?): String? = rel?.let { Regex("^art/([^/?]+)").find(it)?.groupValues?.get(1) }

fun wrappedCards(w: WrappedStats, who: String = "Your"): List<WrappedCard> {
    val name = if (w.final) "$who ${w.year} Wrapped" else "$who ${w.year} so far"
    if (w.totals.plays == 0) return listOf(WrappedCard.Intro(name, "0", "No plays yet — press play and come back."))
    val out = mutableListOf<WrappedCard>(WrappedCard.Intro(name, fmt(w.totals.minutes), "minutes of music"))
    if (w.topTracks.isNotEmpty()) out += WrappedCard.ListCard(
        "Top songs", w.topTracks.take(5).map { CardRow(it.track.title, it.track.artist, "${it.plays} plays", it.track.albumId) },
        w.topTracks.first().track.albumId,
    )
    if (w.topArtists.isNotEmpty()) out += WrappedCard.ListCard(
        "Top artists", w.topArtists.take(5).map { CardRow(it.artist.name, null, "${fmt(it.minutes)} min", artAlbum(it.artUrl)) },
        artAlbum(w.topArtists.first().artUrl),
    )
    if (w.topAlbums.isNotEmpty()) out += WrappedCard.ListCard(
        "Top albums", w.topAlbums.take(5).map { CardRow(it.album.title, it.album.artist, "${fmt(it.minutes)} min", it.album.id) },
        w.topAlbums.first().album.id,
    )
    if (w.topGenres.isNotEmpty()) out += WrappedCard.Genres("Your genres", w.topGenres.take(5).map { it.name to it.share })
    w.peakHour?.let { h ->
        val day = w.peakWeekday?.let { ", on ${WEEKDAYS[it]}s" }.orEmpty()
        out += WrappedCard.Clock("When you listen", w.byHour, "Most of all around ${hourLabel(h)}$day")
    }
    w.streaks?.takeIf { it.longest > 1 }?.let {
        out += WrappedCard.Stat("Longest streak", "${it.longest} days", "in a row with music",
            if (it.current > 1) "Current streak: ${it.current} days" else null)
    }
    w.discoveries?.takeIf { it.artists > 0 }?.let { d ->
        out += WrappedCard.Stat("New discoveries", "${d.artists}",
            if (d.artists == 1) "artist you played for the first time" else "artists you played for the first time",
            d.topArtists.firstOrNull()?.let { "Favourite find: ${it.artist.name}" })
    }
    w.aura?.let { out += WrappedCard.Aura("Your audio aura", it) }
    out += WrappedCard.Summary(name, listOf(
        "Minutes" to fmt(w.totals.minutes), "Plays" to fmt(w.totals.plays), "Songs" to fmt(w.totals.tracks),
        "Artists" to fmt(w.totals.artists), "Top artist" to (w.topArtists.firstOrNull()?.artist?.name ?: "—"),
        "Aura" to (w.aura?.label ?: "—"),
    ))
    return out
}

fun cardAlbumIds(cards: List<WrappedCard>): List<String> =
    cards.filterIsInstance<WrappedCard.ListCard>().flatMap { c -> listOfNotNull(c.heroAlbumId) + c.rows.mapNotNull { it.albumId } }.distinct()

/** Gradient colours (#rrggbb): the aura's, alternating per card, else the accent. */
fun cardColors(w: WrappedStats, index: Int, accent: String = "#4ade80"): Pair<String, String> {
    val base = w.aura?.colors?.takeIf { it.size == 2 }?.let { it[0] to it[1] } ?: (accent to "#6d28d9")
    return if (index % 2 == 1) base.second to base.first else base
}

/** "Because Alex plays…" for a blend track. */
fun blendReason(reason: String, users: List<String>, names: Map<String, String>, me: String?): String {
    val n = users.map { if (it == me) "you" else names[it] ?: "someone" }
    if (n.isEmpty()) return ""
    if (reason == "shared") return if (n.size == 2) "${n[0].replaceFirstChar { it.uppercase() }} and ${n[1]} both love this" else "${n.size} of you love this"
    val who = n[0]
    if (who == "you") return if (reason == "top") "Because you play it a lot" else "From your taste"
    return if (reason == "top") "Because $who plays it a lot" else "Because $who plays this"
}

/** "2026-11-13" -> "13 Nov"; shorter dates stay as they are. */
fun releaseDate(d: String?): String {
    if (d.isNullOrBlank()) return ""
    if (d.length < 10) return d
    val months = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    val m = d.substring(5, 7).toIntOrNull() ?: return d
    val day = d.substring(8, 10).toIntOrNull() ?: return d
    return "$day ${months[m - 1]}"
}
