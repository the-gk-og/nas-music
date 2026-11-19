package org.nasmusic.app.cast

import androidx.compose.runtime.Composable
import org.nasmusic.app.AppContainer

/**
 * FOSS build: no Google Cast SDK. Speakers (Chromecast included) are offered by the server as Connect
 * devices instead, so there is nothing to add here. The `full` flavour has the real implementation.
 */
object NativeCast {
    const val AVAILABLE = false

    fun init(c: AppContainer) = Unit

    /** Extra row in the Devices sheet. */
    @Composable
    fun DevicesRow(c: AppContainer) = Unit
}
