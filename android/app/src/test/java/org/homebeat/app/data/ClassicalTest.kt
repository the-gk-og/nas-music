package org.homebeat.app.data

import org.junit.Assert.assertEquals
import org.junit.Test
import org.homebeat.app.data.api.PlaylistFolder
import org.homebeat.app.data.api.Track

class ClassicalTest {
    private fun t(id: String, work: String? = null, workId: String? = null, movement: String? = null, no: Int? = null) =
        Track(id, title = "T$id", artist = "Orchestra", composer = if (work != null) "Beethoven" else null,
            work = work, workId = workId, movement = movement, movementNo = no)

    private val tracks = listOf(
        t("1", "Sym 5", "w5", "Allegro", 1), t("2", "Sym 5", "w5", "Andante", 2), t("3"), t("4", "Sym 6", "w6", "Erwachen"),
    )

    @Test fun groups() {
        val g = Classical.groupByWork(tracks)
        assertEquals(listOf(Triple("w5", 2, 0), Triple(null, 1, 2), Triple("w6", 1, 3)), g.map { Triple(it.workId, it.tracks.size, it.start) })
        assertEquals(true, Classical.hasWorks(tracks))
    }

    @Test fun display() {
        assertEquals("II. Andante", Classical.movementTitle(tracks[1]))
        assertEquals("Erwachen", Classical.movementTitle(tracks[3]))
        assertEquals("T3", Classical.movementTitle(tracks[2]))
        assertEquals("Beethoven · Orchestra", Classical.artistLine(tracks[0], true))
        assertEquals("Orchestra", Classical.artistLine(tracks[0], false))
        assertEquals(listOf("I", "IV", "IX", "XIV", "XL"), listOf(1, 4, 9, 14, 40).map(Classical::toRoman))
    }

    @Test fun folders() {
        val all = listOf(PlaylistFolder("a", "A"), PlaylistFolder("b", "B", "a"), PlaylistFolder("c", "C"))
        assertEquals(listOf("a", "b"), PlaylistFolders.path(all, "b").map { it.id })
        assertEquals(listOf(null to 0, "a" to 1, "b" to 2, "c" to 1), PlaylistFolders.choices(all).map { it.first?.id to it.second })
        assertEquals(listOf(null, "c"), PlaylistFolders.choices(all, "a").map { it.first?.id })
    }
}
