package org.homebeat.app.v6

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.homebeat.app.connect.ConnectMessage
import org.homebeat.app.connect.ConnectProtocol
import org.homebeat.app.data.FamilyRepository
import org.homebeat.app.data.api.AlbumDetail
import org.homebeat.app.data.api.FamilyMode
import org.homebeat.app.data.api.FamilyUpdate
import org.homebeat.app.data.api.HomebeatApi
import org.homebeat.app.data.api.Track
import java.lang.reflect.Proxy

class FamilyModeTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    @Test fun `decodes explicit flags and stays compatible with older servers`() {
        val t = json.decodeFromString<Track>("""{"id":"t","title":"A","explicit":"explicit","familyHidden":true}""")
        assertTrue(t.isExplicit && t.familyHidden)
        val old = json.decodeFromString<Track>("""{"id":"t","title":"A"}""")
        assertFalse(old.isExplicit || old.familyHidden)
        assertTrue(json.decodeFromString<AlbumDetail>("""{"id":"a","title":"A","artistId":"x","artist":"X","trackCount":1,"explicit":true}""").explicit)
    }

    @Test fun `parses family pushes`() {
        val m = ConnectProtocol.parse("""{"type":"family","family":{"enabled":true,"hideProbable":false,"hideUnknown":false,"locked":true,"hasPin":true}}""")
        assertEquals(ConnectMessage.FamilyChanged(FamilyMode(enabled = true, hideProbable = false, locked = true, hasPin = true)), m)
        assertTrue(ConnectProtocol.parse("""{"type":"family"}""") is ConnectMessage.Unknown)
    }

    /** A HomebeatApi whose putFamily returns [reply] (other calls are not used here). */
    private fun api(reply: (FamilyUpdate) -> FamilyMode, calls: MutableList<FamilyUpdate>): HomebeatApi =
        Proxy.newProxyInstance(HomebeatApi::class.java.classLoader, arrayOf(HomebeatApi::class.java)) { _, method, args ->
            when (method.name) {
                "putFamily" -> (args[0] as FamilyUpdate).also { calls += it }.let(reply)
                else -> throw UnsupportedOperationException(method.name)
            }
        } as HomebeatApi

    @Test fun `a locked policy asks for the PIN before loosening, never for tightening`() = runBlocking {
        val calls = mutableListOf<FamilyUpdate>()
        val repo = FamilyRepository { api({ FamilyMode(enabled = it.enabled ?: true, hideUnknown = it.hideUnknown ?: false, locked = true, hasPin = true) }, calls) }
        repo.changed(FamilyMode(enabled = true, locked = true, hasPin = true))
        val rev = repo.rev.value
        assertEquals(FamilyRepository.Result.NeedsPin, repo.update(FamilyUpdate(enabled = false)))
        assertTrue(calls.isEmpty())
        assertEquals(FamilyRepository.Result.Ok, repo.update(FamilyUpdate(hideUnknown = true)))
        assertTrue(repo.mode.value.hideUnknown && repo.rev.value == rev + 1)
        repo.update(FamilyUpdate(enabled = false, pin = "1234"))
        assertEquals("1234", calls.last().pin)
        // no PIN at all: only an admin can unlock
        repo.changed(FamilyMode(enabled = true, locked = true, hasPin = false))
        assertTrue(repo.update(FamilyUpdate(enabled = false)) is FamilyRepository.Result.Failed)
    }
}
