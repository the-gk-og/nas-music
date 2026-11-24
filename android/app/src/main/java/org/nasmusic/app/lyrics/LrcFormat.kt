package org.nasmusic.app.lyrics

import org.nasmusic.app.data.api.Lyrics

object LrcFormat {
    /** Text for the editor: LRC ("[mm:ss.xx]line") when synced, else plain lines. */
    fun toEditable(l: Lyrics): String =
        if (l.synced && l.lines.any { it.timeMs != null }) l.lines.joinToString("\n") { line ->
            val body = line.words?.takeIf { it.isNotEmpty() }
                ?.joinToString("") { w -> "<${stamp(w.timeMs).removeSurrounding("[", "]")}>${w.text}" }
                ?: line.text
            "${stamp(line.timeMs ?: 0)}$body"
        }
        else l.text.ifBlank { l.lines.joinToString("\n") { it.text } }

    fun stamp(ms: Long): String {
        val cs = (ms / 10) % 100
        val s = (ms / 1000) % 60
        val m = ms / 60_000
        return "[%02d:%02d.%02d]".format(m, s, cs)
    }

    fun sourceLabel(source: String): String = when (source) {
        "lrc_file" -> ".lrc file"
        "user" -> "Your edit"
        else -> "Embedded lyrics"
    }
}
