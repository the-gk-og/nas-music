package org.homebeat.app.ui

import org.homebeat.app.AppContainer
import org.homebeat.app.data.api.Track

/** A snackbar with an Undo button (shown by the main scaffold); [undo] runs if it is pressed. */
class UndoRequest(val text: String, val undo: suspend () -> Unit)

/** Remove the song at [index] of the playing queue, with Undo putting it back in the same place. */
fun removeFromQueueWithUndo(c: AppContainer, ui: UiController, index: Int, track: Track) {
    c.player.removeItem(index)
    ui.undo = UndoRequest("Removed \"${track.title}\" from the queue") { c.player.insertItem(index, track) }
}
