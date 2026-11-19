package org.nasmusic.app.ui

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.Blend
import org.nasmusic.app.data.api.LenientJson
import org.nasmusic.app.data.api.WrappedStats
import org.nasmusic.app.ui.wrapped.WrappedCard
import org.nasmusic.app.ui.wrapped.blendReason
import org.nasmusic.app.ui.wrapped.cardAlbumIds
import org.nasmusic.app.ui.wrapped.cardColors
import org.nasmusic.app.ui.wrapped.hourLabel
import org.nasmusic.app.ui.wrapped.releaseDate
import org.nasmusic.app.ui.wrapped.wrappedCards

class WrappedCardsTest {
    private val json: Json = ApiFactory.json

    // Shape as sent by GET /wrapped (server tests/test_insights_api.py).
    private val wrappedJson = """
        {"year":2026,"final":false,"timezone":"Europe/London","from":"2026-01-01T00:00:00Z","to":"2026-11-20T00:00:00Z",
         "totals":{"minutes":1234,"plays":300,"skips":4,"tracks":80,"artists":20,"albums":30,"days":40},
         "topTracks":[{"track":{"id":"t1","title":"Song","artist":"Band","albumId":"al1"},"plays":20,"minutes":70.5}],
         "topArtists":[{"artist":{"id":"ar1","name":"Band","albumCount":1},"artUrl":"art/al2","plays":50,"minutes":200.0}],
         "topAlbums":[{"album":{"id":"al3","title":"LP","artistId":"ar1","artist":"Band","trackCount":9},"plays":30,"minutes":100.0}],
         "topGenres":[{"name":"Rock","minutes":500.0,"share":0.4}],
         "byHour":[1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,9,1,1,1],"byWeekday":[1,1,1,1,1,1,1],"byMonth":[1,1,1,1,1,1,1,1,1,1,1,1],
         "peakHour":20,"peakWeekday":4,"topDay":{"date":"2026-07-01","minutes":90},
         "streaks":{"longest":5,"longestFrom":"2026-03-01","longestTo":"2026-03-05","current":2},
         "discoveries":{"artists":3,"tracks":10,"share":0.1,"topArtists":[]},
         "aura":{"label":"Golden","description":"x","energy":0.5,"danceability":0.6,"valence":0.2,"bpm":118,
                 "moods":[{"name":"happy","score":0.5}],"colors":["#111111","#222222"]},
         "firstPlay":{"track":{"id":"t1","title":"Song"},"at":"2026-01-01T00:30:00Z"}}
    """.trimIndent()

    @Test fun cardsFollowTheStory() {
        val w = json.decodeFromString(WrappedStats.serializer(), wrappedJson)
        val cards = wrappedCards(w)
        assertEquals(
            listOf("Intro", "ListCard", "ListCard", "ListCard", "Genres", "Clock", "Stat", "Stat", "Aura", "Summary"),
            cards.map { it::class.simpleName },
        )
        assertEquals("Your 2026 so far", cards[0].title)
        assertEquals("1,234", (cards[0] as WrappedCard.Intro).big)
        assertEquals(setOf("al1", "al2", "al3"), cardAlbumIds(cards).toSet())
        assertEquals("#111111" to "#222222", cardColors(w, 0))
        assertEquals("#222222" to "#111111", cardColors(w, 1))
        val none = wrappedCards(w.copy(totals = w.totals.copy(plays = 0)))
        assertEquals(1, none.size)
        assertTrue(wrappedCards(w.copy(final = true, aura = null))[0].title.endsWith("Wrapped"))
    }

    @Test fun labels() {
        assertEquals(listOf("midnight", "9 am", "noon", "9 pm"), listOf(0, 9, 12, 21).map(::hourLabel))
        assertEquals("13 Nov", releaseDate("2026-11-13"))
        assertEquals("2026", releaseDate("2026"))
        val names = mapOf("u1" to "alex", "u2" to "sam")
        assertEquals("Because sam plays it a lot", blendReason("top", listOf("u2"), names, "u1"))
        assertEquals("You and sam both love this", blendReason("shared", listOf("u1", "u2"), names, "u1"))
        assertEquals("From your taste", blendReason("match", listOf("u1"), names, "u1"))
    }

    @Test fun blendAndShelfItemsParse() {
        val b = json.decodeFromString(Blend.serializer(), """
            {"id":"b1","name":"alice + bob","myStatus":"joined","members":[{"userId":"u1","username":"alice","status":"joined"}],
             "matchPct":72,"pairs":[{"a":"u1","b":"u2","aName":"alice","bName":"bob","pct":72}],"trackCount":1,
             "tracks":[{"track":{"id":"t1","title":"Song"},"forUsers":["u1","u2"],"reason":"shared"}]}
        """.trimIndent())
        assertEquals(72, b.matchPct)
        assertEquals(listOf("u1", "u2"), b.tracks[0].forUsers)
        val feed = LenientJson.feed(json.parseToJsonElement("""
            {"shelves":[{"id":"blends","kind":"blends","title":"Blends","items":[
                {"type":"blend","id":"new","title":"Start a blend","blend":{"status":"new"}}]},
              {"id":"wrapped","kind":"wrapped","title":"Wrapped","items":[
                {"type":"wrapped","id":"2026","title":"Your 2026 so far","wrapped":{"year":2026,"final":false}}]},
              {"id":"release_radar","kind":"release_radar","title":"Release Radar","items":[
                {"type":"catalog","id":"mb:rg:x","title":"New LP","release":{"date":"2026-11-13","type":"Album","upcoming":false},
                 "catalog":{"id":"mb:rg:x","type":"album","title":"New LP","hasPreview":false,"owned":false,"links":[]}}]}]}
        """.trimIndent()))
        assertEquals(listOf("blends", "wrapped", "release_radar"), feed.shelves.map { it.kind })
        assertEquals("new", feed.shelves[0].items[0].blend?.status)
        assertEquals(2026, feed.shelves[1].items[0].wrapped?.year)
        assertEquals("Album", feed.shelves[2].items[0].release?.type)
    }
}
