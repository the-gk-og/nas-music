package org.homebeat.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.NewMemberBody
import org.homebeat.app.data.api.PlaylistActivity
import org.homebeat.app.data.api.PlaylistMember
import org.homebeat.app.data.api.RoleBody
import org.homebeat.app.data.api.TagEditBody
import org.homebeat.app.data.api.TagSuggestion
import org.homebeat.app.data.api.TagValues
import org.homebeat.app.data.api.TrackTagDetail
import org.homebeat.app.data.api.UserRef

/** Admin tag edit for one or more tracks (v5). Edits are server-side overrides; music files are never written. */
data class TagEditTarget(val trackIds: List<String>, val title: String, val album: Boolean = false)

private data class TagField(val key: String, val label: String, val numeric: Boolean = false, val perTrack: Boolean = false)

private val TAG_FIELDS = listOf(
    TagField("title", "Title", perTrack = true),
    TagField("artist", "Artist"),
    TagField("albumArtist", "Album artist"),
    TagField("album", "Album"),
    TagField("trackNo", "Track number", numeric = true, perTrack = true),
    TagField("discNo", "Disc number", numeric = true),
    TagField("year", "Year", numeric = true),
    TagField("genre", "Genre"),
)

private fun TagValues.get(k: String): String = when (k) {
    "title" -> title; "artist" -> artist; "albumArtist" -> albumArtist; "album" -> album
    "trackNo" -> trackNo?.toString(); "discNo" -> discNo?.toString(); "year" -> year?.toString(); "genre" -> genre
    else -> null
}.orEmpty()

/** Builds the `set` object: only changed fields; blank = null (clear the tag). Null if a number is invalid. */
internal fun tagChanges(initial: Map<String, String>, values: Map<String, String>, numeric: Set<String>): JsonObject? {
    val out = LinkedHashMap<String, kotlinx.serialization.json.JsonElement>()
    for ((k, v) in values) {
        if (v == initial[k]) continue
        val t = v.trim()
        out[k] = when {
            t.isEmpty() -> JsonNull
            k in numeric -> JsonPrimitive(t.toIntOrNull() ?: return null)
            else -> JsonPrimitive(t)
        }
    }
    return JsonObject(out)
}

@Composable
fun TagEditDialog(c: AppContainer, target: TagEditTarget, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    val single = target.trackIds.size == 1 && !target.album
    val fields = if (single) TAG_FIELDS else TAG_FIELDS.filterNot { it.perTrack }
    var details by remember { mutableStateOf<List<TrackTagDetail>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val values = remember { mutableStateMapOf<String, String>() }
    val initial = remember { mutableStateMapOf<String, String>() }
    var busy by remember { mutableStateOf(false) }
    var suggestions by remember { mutableStateOf<List<TagSuggestion>?>(null) }

    suspend fun load() {
        runCatching { target.trackIds.take(300).map { c.api.trackTags(it) } }
            .onSuccess { d ->
                details = d
                for (f in TAG_FIELDS) {
                    val vs = d.map { it.effective.get(f.key) }.toSet()
                    val v = if (vs.size == 1) vs.first() else ""
                    values[f.key] = v; initial[f.key] = v
                }
            }
            .onFailure { error = "Couldn't load tags (admins only)" }
    }
    LaunchedEffect(target) { load() }

    fun save(set: JsonObject? = null, revert: List<String> = emptyList()) = scope.launch {
        val changes = set ?: tagChanges(initial.toMap(), fields.associate { it.key to values[it.key].orEmpty() }, TAG_FIELDS.filter { it.numeric }.map { it.key }.toSet())
        if (changes == null) { ui.toast = "Numbers only in number fields"; return@launch }
        if (changes.isEmpty() && revert.isEmpty()) { ui.toast = "Nothing changed"; return@launch }
        busy = true
        runCatching { c.api.editTags(TagEditBody(target.trackIds, changes, revert)) }
            .onSuccess { ui.toast = if (revert.isEmpty()) "Saved (${it.changed})" else "Reverted to file tags"; onDismiss() }
            .onFailure { ui.toast = "Couldn't save tags" }
        busy = false
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (target.album) "Edit album tags" else "Edit tags") },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    "${target.title} · ${if (single) "1 track" else "${target.trackIds.size} tracks"}. The music files stay untouched; edits are kept on the server.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                val d = details
                when {
                    error != null -> Text(error!!, color = MaterialTheme.colorScheme.error)
                    d == null -> Text("Loading…")
                    else -> {
                        for (f in fields) {
                            val edited = d.any { f.key in it.overridden }
                            val mixed = d.map { it.effective.get(f.key) }.toSet().size > 1
                            OutlinedTextField(
                                values[f.key].orEmpty(), { values[f.key] = it },
                                label = { Text(f.label + if (edited) " •" else "") },
                                placeholder = { if (mixed) Text("(several — leave to keep)") else if (single) Text("file: ${d[0].file.get(f.key)}") },
                                singleLine = true,
                                keyboardOptions = if (f.numeric) KeyboardOptions(keyboardType = KeyboardType.Number) else KeyboardOptions.Default,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                        if (single) {
                            TextButton(enabled = !busy, onClick = {
                                scope.launch {
                                    suggestions = runCatching { c.api.tagSuggestions(target.trackIds[0]).results }
                                        .onFailure { ui.toast = "MusicBrainz didn't answer" }.getOrNull()
                                }
                            }) { Text("Suggest from MusicBrainz") }
                            suggestions?.take(8)?.forEach { s ->
                                Column(Modifier.fillMaxWidth().clickable {
                                    s.title?.let { values["title"] = it }; s.artist?.let { values["artist"] = it }
                                    s.album?.let { values["album"] = it }; s.albumArtist?.let { values["albumArtist"] = it }
                                    s.year?.let { values["year"] = it.toString() }; s.trackNo?.let { values["trackNo"] = it.toString() }
                                    s.discNo?.let { values["discNo"] = it.toString() }
                                    suggestions = null
                                }.padding(vertical = 6.dp)) {
                                    Text("${s.title} — ${s.artist}", style = MaterialTheme.typography.bodyMedium)
                                    Text(listOfNotNull(s.album, s.year?.toString(), s.trackNo?.let { "#$it" }).joinToString(" · "),
                                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                        if (d.any { it.overridden.isNotEmpty() }) {
                            TextButton(enabled = !busy, onClick = { save(JsonObject(emptyMap()), listOf("*")) }) { Text("Revert to file tags") }
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(enabled = !busy && details != null, onClick = { save() }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** Members, roles, invite/leave and activity of a playlist (v5). */
@Composable
fun PlaylistMembersDialog(c: AppContainer, playlistId: String, isOwner: Boolean, onLeft: () -> Unit, onDismiss: () -> Unit) {
    val ui = LocalUi.current
    val scope = rememberCoroutineScope()
    var members by remember { mutableStateOf<List<PlaylistMember>?>(null) }
    var activity by remember { mutableStateOf<List<PlaylistActivity>>(emptyList()) }
    var users by remember { mutableStateOf<List<UserRef>>(emptyList()) }
    var inviteOpen by remember { mutableStateOf(false) }
    var version by remember { mutableStateOf(0) }
    val me = c.secureStore.config.value?.username

    LaunchedEffect(playlistId, version) {
        members = runCatching { c.api.playlistMembers(playlistId) }.getOrNull()
        activity = runCatching { c.api.playlistActivity(playlistId) }.getOrDefault(emptyList())
        if (isOwner && users.isEmpty()) users = runCatching { c.api.userDirectory() }.getOrDefault(emptyList())
    }
    LaunchedEffect(playlistId) { c.connect.playlistChanges.collect { if (it.playlistId == playlistId) version++ } }
    fun act(block: suspend () -> Unit) = scope.launch {
        runCatching { block() }.onFailure { ui.toast = "Not allowed" }
        version++
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Members") },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState())) {
                Text("Editors add, remove and reorder songs; viewers only listen. Changes show up live.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                members?.forEach { m ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(m.username, Modifier.weight(1f))
                        if (m.role == "owner" || !isOwner) {
                            Text(m.role.replaceFirstChar { it.uppercase() }, style = MaterialTheme.typography.bodySmall)
                        } else {
                            val other = if (m.role == "editor") "viewer" else "editor"
                            TextButton(onClick = { act { c.api.setPlaylistMemberRole(playlistId, m.userId, RoleBody(other)) } }) {
                                Text(m.role.replaceFirstChar { it.uppercase() })
                            }
                            IconButton(onClick = { act { c.api.removePlaylistMember(playlistId, m.userId) } }) { Icon(Icons.Default.Close, "Remove ${m.username}") }
                        }
                    }
                } ?: Text("Loading…")
                if (isOwner) {
                    val candidates = users.filter { u -> members?.none { it.userId == u.id } != false }
                    Column {
                        TextButton(enabled = candidates.isNotEmpty(), onClick = { inviteOpen = true }) { Text(if (candidates.isEmpty()) "Everyone is in" else "Invite…") }
                        DropdownMenu(inviteOpen, { inviteOpen = false }) {
                            candidates.forEach { u ->
                                DropdownMenuItem({ Text("${u.username} (editor)") }, { inviteOpen = false; act { c.api.addPlaylistMember(playlistId, NewMemberBody(userId = u.id, role = "editor")) } })
                                DropdownMenuItem({ Text("${u.username} (viewer)") }, { inviteOpen = false; act { c.api.addPlaylistMember(playlistId, NewMemberBody(userId = u.id, role = "viewer")) } })
                            }
                        }
                    }
                } else {
                    TextButton(onClick = {
                        scope.launch {
                            val mine = members?.firstOrNull { it.username.equals(me, ignoreCase = true) }
                            if (mine != null && runCatching { c.api.removePlaylistMember(playlistId, mine.userId) }.isSuccess) onLeft()
                            else ui.toast = "Couldn't leave"
                        }
                    }) { Text("Leave playlist") }
                }
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                Text("Activity", style = MaterialTheme.typography.titleSmall)
                activity.forEach { a ->
                    Text("${a.user ?: "Someone"} ${describe(a)}", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 2.dp))
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
    )
}

internal fun describe(a: PlaylistActivity): String {
    fun d(k: String) = a.detail[k]?.jsonPrimitive?.contentOrNull
    val n = d("count")?.toIntOrNull() ?: 1
    val what = a.trackTitle?.let { "“$it”" + if (n > 1) " and ${n - 1} more" else "" } ?: "$n song${if (n == 1) "" else "s"}"
    return when (a.action) {
        "create" -> "created the playlist"
        "rename" -> "renamed it to “${d("to")}”"
        "add" -> "added $what"
        "remove" -> "removed $what"
        "move" -> "moved $what"
        "member_add" -> "invited ${d("user")} as ${d("role")}"
        "member_role" -> "made ${d("user")} ${d("role")}"
        "member_remove" -> "removed ${d("user")}"
        "leave" -> "left"
        else -> a.action
    }
}
