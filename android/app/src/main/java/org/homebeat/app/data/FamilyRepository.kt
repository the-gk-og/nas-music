package org.homebeat.app.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.homebeat.app.data.api.FamilyMode
import org.homebeat.app.data.api.FamilyUpdate
import org.homebeat.app.data.api.HomebeatApi
import retrofit2.HttpException

/**
 * Family mode (api 0.6). The server does all the filtering; this mirrors the user's policy for the
 * Settings switch, the quick toggle in the profile menu and the "locked" state. [rev] bumps on every
 * change so screens can reload their lists.
 */
class FamilyRepository(private val api: () -> HomebeatApi) {
    private val _mode = MutableStateFlow(FamilyMode())
    val mode: StateFlow<FamilyMode> = _mode
    private val _rev = MutableStateFlow(0)
    val rev: StateFlow<Int> = _rev

    sealed interface Result {
        data object Ok : Result
        /** Loosening a locked policy: ask for the PIN (or say only an admin can, when there is none). */
        data object NeedsPin : Result
        data class Failed(val message: String) : Result
    }

    suspend fun refresh() {
        runCatching { api().family() }.onSuccess { changed(it) }
    }

    /** From Connect `{"type":"family"}` or our own PUT. */
    fun changed(m: FamilyMode) {
        if (m != _mode.value) {
            _mode.value = m
            _rev.value++
        }
    }

    fun loosens(u: FamilyUpdate): Boolean {
        val m = _mode.value
        return (m.enabled && u.enabled == false) || (m.hideProbable && u.hideProbable == false) ||
            (m.hideUnknown && u.hideUnknown == false)
    }

    suspend fun update(u: FamilyUpdate): Result {
        val m = _mode.value
        if (m.locked && loosens(u) && u.pin == null) {
            return if (m.hasPin) Result.NeedsPin else Result.Failed("Family mode is locked by an admin")
        }
        return try {
            changed(api().putFamily(u))
            Result.Ok
        } catch (e: HttpException) {
            Result.Failed(
                when (e.code()) {
                    429 -> "Too many wrong PINs — try again in a minute"
                    403 -> if (u.pin != null) "Wrong PIN" else "Family mode is locked by an admin"
                    else -> "Couldn't change family mode (${e.code()})"
                },
            )
        } catch (e: Exception) {
            Result.Failed("Couldn't reach the server")
        }
    }
}
