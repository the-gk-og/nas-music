package org.nasmusic.app.voice

import kotlinx.serialization.encodeToString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.nasmusic.app.data.api.ApiFactory
import org.nasmusic.app.data.api.AppVoiceCommand
import org.nasmusic.app.data.api.VoiceResult
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class VoiceCommandsTest {
    private fun result(json: String) = ApiFactory.json.decodeFromString<VoiceResult>(json)

    @Test
    fun localAnswersBecomePlayerActions() {
        assertEquals(VoiceAction.PlayQueue("q1"),
            VoiceCommands.action(result("""{"ok":true,"intent":"play","speech":"Playing x.","local":true,"queueId":"q1","item":{"kind":"vibe","id":null,"title":"chill"}}"""), 1f))
        assertEquals(VoiceAction.Next, VoiceCommands.action(result("""{"ok":true,"intent":"next","speech":"Skipping.","local":true}"""), 1f))
        assertEquals(VoiceAction.SetVolume(0.4f), VoiceCommands.action(result("""{"ok":true,"intent":"volume","speech":"","local":true,"volume":0.4}"""), 1f))
        assertEquals(VoiceAction.SetVolume(1f), VoiceCommands.action(result("""{"ok":true,"intent":"volume","speech":"","local":true,"volumeStep":0.1}"""), 0.95f))
        // Done by the server (another room / a playlist add) or failed: nothing to do on this phone.
        assertEquals(VoiceAction.None, VoiceCommands.action(result("""{"ok":true,"intent":"play","speech":"Playing x on Bedroom.","queueId":"q1","future":1}"""), 1f))
        assertEquals(VoiceAction.None, VoiceCommands.action(result("""{"ok":true,"intent":"add_to_playlist","speech":"Added Song to Gym."}"""), 1f))
        assertEquals(VoiceAction.None, VoiceCommands.action(result("""{"ok":false,"intent":"unknown","speech":"Sorry, I didn't understand that.","local":true}"""), 1f))
    }

    @Test
    fun requestSendsOnlyWhatIsSet() {
        assertEquals("""{"text":"add this to gym","trackId":"t1"}""", ApiFactory.json.encodeToString(AppVoiceCommand("add this to gym", "t1")))
    }

    @Test
    fun nowPlayingAndVoskJson() {
        assertEquals("This is Bang by AJR.", VoiceCommands.nowPlayingSpeech("Bang", "AJR", true))
        assertEquals("Bang is paused.", VoiceCommands.nowPlayingSpeech("Bang", null, false))
        assertNull(VoiceCommands.nowPlayingSpeech(null, "AJR", true))
        assertEquals("play something chill", VoiceCommands.voskText("{\n  \"text\" : \"play something chill\"\n}"))
        assertEquals("play some", VoiceCommands.voskText("""{"partial" : "play some"}""", "partial"))
        assertEquals("", VoiceCommands.voskText("not json"))
    }

    private fun zip(vararg entries: Pair<String, String>): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { z -> entries.forEach { (n, body) -> z.putNextEntry(ZipEntry(n)); z.write(body.toByteArray()); z.closeEntry() } }
        return out.toByteArray()
    }

    @Test
    fun modelUnzipsAndIsFoundInsideItsFolder() {
        val tmp = Files.createTempDirectory("vosk").toFile()
        try {
            ModelFiles.unzip(zip("vosk-model-small-en-us-0.15/am/final.mdl" to "x", "vosk-model-small-en-us-0.15/conf/mfcc.conf" to "y").inputStream(), tmp)
            val root = ModelFiles.modelRoot(tmp)
            assertEquals(File(tmp, "vosk-model-small-en-us-0.15").canonicalPath, root?.canonicalPath)
            assertTrue(ModelFiles.looksComplete(root!!))
            assertFalse(ModelFiles.looksComplete(tmp))
        } finally { tmp.deleteRecursively() }
    }

    @Test
    fun zipSlipIsRefused() {
        val tmp = Files.createTempDirectory("vosk").toFile()
        try {
            ModelFiles.unzip(zip("../evil.txt" to "x").inputStream(), File(tmp, "in"))
            fail("expected an IOException")
        } catch (_: IOException) {
            assertFalse(File(tmp, "evil.txt").exists())
        } finally { tmp.deleteRecursively() }
    }
}
