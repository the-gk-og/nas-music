package org.homebeat.app.widget

import org.homebeat.app.data.api.FamilyMode
import org.homebeat.app.playback.MediaItems
import org.homebeat.app.playback.NowPlaying

/**
 * What the home-screen widgets and the quick-settings tile show. Plain data so the mapping from the
 * player / Connect state can be unit-tested without a launcher.
 */
data class PlayingInfo(
    val trackId: String? = null,
    val title: String = "",
    val artist: String = "",
    val albumId: String? = null,
    val starred: Boolean = false,
    val isPlaying: Boolean = false,
    val hasNext: Boolean = false,
    val hasPrevious: Boolean = false,
    /** "Playing on Kitchen" / "Casting from Pixel to Bedroom" / "Connecting to Kitchen…" when not this phone. */
    val deviceLabel: String? = null,
    /** False when this is the last song remembered from an earlier run (nothing is loaded in the player). */
    val live: Boolean = true,
) {
    val hasTrack: Boolean get() = trackId != null
}

/** What the big button (widget play/pause, tile tap) does. */
enum class PrimaryAction {
    /** Nothing to play: start the AI radio ("Play something for now"). */
    PLAY_SOMETHING,
    /** A remembered song but an empty player: resume the last queue (radio if there is none). */
    RESUME,
    TOGGLE,
}

enum class WidgetSize { SMALL, MEDIUM, LARGE }

data class TileUi(val label: String, val subtitle: String?, val active: Boolean, val icon: TileIcon)
enum class TileIcon { PLAY, PAUSE, SPARKLE }

data class FamilyTileUi(val active: Boolean, val locked: Boolean, val subtitle: String)

object WidgetStates {
    const val PLAY_SOMETHING = "Play something for now"

    /** The phone's own player (or the full flavour's Cast session overlaying it), or a Connect remote. */
    fun fromNowPlaying(np: NowPlaying, castName: String? = null): PlayingInfo {
        val item = np.item ?: return PlayingInfo(isPlaying = false, deviceLabel = np.remote?.label)
        val md = item.mediaMetadata
        val x = md.extras
        return PlayingInfo(
            trackId = item.mediaId.takeIf { it.isNotEmpty() },
            title = md.title?.toString().orEmpty(),
            artist = md.artist?.toString().orEmpty(),
            albumId = x?.getString(MediaItems.EXTRA_ALBUM_ID)?.takeIf { it.isNotBlank() },
            starred = x?.getBoolean(MediaItems.EXTRA_STARRED) ?: false,
            isPlaying = np.isPlaying,
            hasNext = np.hasNext,
            hasPrevious = np.hasPrevious,
            deviceLabel = np.remote?.label ?: castName?.let { "Playing on $it" },
        )
    }

    /**
     * Which state to show: a Connect remote (another device or speaker is active) wins, then this phone's
     * player while it has something loaded, then the last remembered song shown paused.
     */
    fun choose(remote: PlayingInfo?, local: PlayingInfo?, remembered: PlayingInfo?): PlayingInfo = when {
        remote != null -> remote
        local != null && local.hasTrack -> local
        remembered != null && remembered.hasTrack -> remembered.copy(isPlaying = false, deviceLabel = null, live = false)
        else -> local ?: PlayingInfo()
    }

    /** A favourite toggled from the widget wins until the player reports the track again with that flag. */
    fun withStar(info: PlayingInfo, overrides: Map<String, Boolean>): PlayingInfo =
        info.trackId?.let { overrides[it] }?.let { info.copy(starred = it) } ?: info

    fun primaryAction(info: PlayingInfo): PrimaryAction = when {
        !info.hasTrack -> PrimaryAction.PLAY_SOMETHING
        // A remote device has its own queue: play/pause always goes there.
        info.deviceLabel != null -> PrimaryAction.TOGGLE
        !info.live -> PrimaryAction.RESUME
        else -> PrimaryAction.TOGGLE
    }

    /** Glance responsive buckets (dp). Small = cover + play; medium adds text + transport; large adds shortcuts. */
    fun sizeFor(widthDp: Float, heightDp: Float): WidgetSize = when {
        widthDp >= 220f && heightDp >= 200f -> WidgetSize.LARGE
        widthDp >= 220f -> WidgetSize.MEDIUM
        else -> WidgetSize.SMALL
    }

    fun subtitle(info: PlayingInfo): String = when {
        !info.hasTrack -> info.deviceLabel ?: "Nothing playing"
        info.deviceLabel != null && info.artist.isNotBlank() -> "${info.artist} · ${info.deviceLabel}"
        info.deviceLabel != null -> info.deviceLabel
        else -> info.artist
    }

    fun tile(info: PlayingInfo): TileUi = when (primaryAction(info)) {
        PrimaryAction.PLAY_SOMETHING -> TileUi(PLAY_SOMETHING, info.deviceLabel, active = false, icon = TileIcon.SPARKLE)
        else -> TileUi(
            label = info.title.ifBlank { "Homebeat" },
            subtitle = subtitle(info).ifBlank { null },
            active = info.isPlaying,
            icon = if (info.isPlaying) TileIcon.PAUSE else TileIcon.PLAY,
        )
    }

    /** Locked by an admin: shown as locked and never toggled from the tile. */
    fun familyTile(m: FamilyMode): FamilyTileUi = FamilyTileUi(
        active = m.enabled,
        locked = m.locked,
        subtitle = when {
            m.locked -> if (m.enabled) "On · locked" else "Off · locked"
            m.enabled -> "On"
            else -> "Off"
        },
    )
}
