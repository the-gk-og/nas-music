package org.homebeat.app.ui

import org.homebeat.app.data.api.TrackInfo

object TrackFormat {
    /** "FLAC · 16-bit · 44.1 kHz · 912 kbps" (missing parts left out). */
    fun line(i: TrackInfo): String = listOfNotNull(
        i.codec?.uppercase(),
        i.bitsPerSample?.let { "$it-bit" },
        i.sampleRate?.let { sr -> if (sr % 1000 == 0) "${sr / 1000} kHz" else "%.1f kHz".format(sr / 1000.0) },
        i.bitrateKbps?.let { "$it kbps" },
        i.channels?.let { if (it == 1) "mono" else null },
    ).joinToString(" · ")

    fun orUnknown(v: Any?): String = v?.toString()?.takeIf { it.isNotBlank() } ?: "[unknown]"

    /** "Music › Artist › Album" from the folder relative to the library root. */
    fun location(folder: String?): String =
        folder?.split('/')?.filter { it.isNotBlank() }?.joinToString(" › ")?.ifBlank { null }?.let { "Library › $it" } ?: "Library"
}
