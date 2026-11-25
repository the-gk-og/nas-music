package org.homebeat.wear.connect

import kotlin.random.Random
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.homebeat.app.connect.ConnectDevice
import org.homebeat.app.connect.ConnectMessage
import org.homebeat.app.connect.ConnectProtocol
import org.homebeat.app.connect.PlaybackState
import org.homebeat.app.net.HttpClients
import org.homebeat.app.net.Route
import org.homebeat.app.net.RoutedCallFactory

/**
 * The watch's Connect socket (`/api/v1/connect`, docs/v2.md §5), same protocol as the phone
 * (shared [ConnectProtocol]) but announcing itself as `kind: "watch"`.
 *
 * The watch is a remote for whatever plays elsewhere, and a player itself when it plays to
 * Bluetooth headphones. The socket only runs while something needs it ([acquire]/[release]),
 * because a socket held open all day costs a watch battery.
 */
class WearConnect(
    private val clients: () -> HttpClients?,
    private val route: StateFlow<Route>,
    private val scope: CoroutineScope,
    private val deviceName: () -> String,
    private val appVersion: String,
) {
    private val _devices = MutableStateFlow<List<ConnectDevice>>(emptyList())
    val devices: StateFlow<List<ConnectDevice>> = _devices.asStateFlow()
    private val _state = MutableStateFlow<PlaybackState?>(null)
    val state: StateFlow<PlaybackState?> = _state.asStateFlow()
    private val _myId = MutableStateFlow<String?>(null)
    val myDeviceId: StateFlow<String?> = _myId.asStateFlow()
    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected.asStateFlow()

    /** Commands / takeover / release for the playback service (buffered until it runs). */
    private val _events = Channel<ConnectMessage>(64)
    val events: Flow<ConnectMessage> = _events.receiveAsFlow()

    @Volatile var onTakeover: () -> Unit = {}

    @Volatile private var socket: WebSocket? = null
    private var loop: Job? = null
    private var routeWatch: Job? = null
    private val holders = mutableSetOf<String>()

    val isActiveHere: Boolean get() = _state.value?.activeDeviceId.let { it != null && it == _myId.value }

    /** Another online device is the active player: the watch acts as its remote. */
    val isRemote: Boolean
        get() {
            val active = _state.value?.activeDeviceId ?: return false
            if (active == _myId.value) return false
            return _devices.value.any { it.id == active && it.online }
        }

    fun activeDevice(): ConnectDevice? = _state.value?.activeDeviceId?.let { id -> _devices.value.firstOrNull { it.id == id } }

    /** Keep the socket open while [who] needs it (UI on screen, playback running, tile refresh). */
    @Synchronized fun acquire(who: String) {
        holders += who
        if (loop == null) {
            loop = scope.launch { runLoop() }
            routeWatch = scope.launch { route.collect { socket?.close(1001, "route changed") } }
        }
    }

    @Synchronized fun release(who: String) {
        holders -= who
        if (holders.isEmpty()) {
            loop?.cancel(); loop = null
            routeWatch?.cancel(); routeWatch = null
            socket?.close(1000, "bye"); socket = null
            _connected.value = false
        }
    }

    private suspend fun runLoop() {
        var attempt = 0
        while (true) {
            val c = clients()
            val r = route.value
            if (c == null || !c.config.isPaired || r == Route.OFFLINE) {
                _connected.value = false
                delay(5_000)
                continue
            }
            val welcomed = connectOnce(c, r)
            attempt = if (welcomed) 0 else attempt + 1
            delay(ConnectProtocol.backoffMs(attempt, Random.nextDouble()))
        }
    }

    private suspend fun connectOnce(c: HttpClients, r: Route): Boolean {
        val base = c.config.baseFor(r) ?: return false
        val url = RoutedCallFactory.rewrite(RoutedCallFactory.virtual("connect").toHttpUrl(), base)
        var welcomed = false
        suspendCancellableCoroutine { cont ->
            val listener = object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send(hello(deviceName(), appVersion))
                    scope.launch {
                        while (socket === webSocket) { delay(25_000); webSocket.send(ConnectProtocol.PING) }
                    }
                }
                override fun onMessage(webSocket: WebSocket, text: String) {
                    val m = ConnectProtocol.parse(text)
                    if (m is ConnectMessage.Welcome) { welcomed = true; _connected.value = true }
                    handle(m)
                }
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = done()
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = done()
                private fun done() {
                    _connected.value = false
                    if (cont.isActive) cont.resumeWith(Result.success(Unit))
                }
            }
            val ws = c.forRoute(r).newWebSocket(Request.Builder().url(url).build(), listener)
            socket = ws
            cont.invokeOnCancellation { ws.cancel() }
        }
        socket = null
        return welcomed
    }

    internal fun handle(m: ConnectMessage) {
        when (m) {
            is ConnectMessage.Welcome -> {
                _myId.value = m.deviceId
                _devices.value = m.devices
                m.state?.let { _state.value = it }
            }
            is ConnectMessage.Devices -> _devices.value = m.devices
            is ConnectMessage.State -> if (ConnectProtocol.accept(_state.value, m.state)) _state.value = m.state
            is ConnectMessage.Takeover -> {
                _state.value = m.state.copy(activeDeviceId = _myId.value ?: m.state.activeDeviceId)
                _events.trySend(m)
                onTakeover()
            }
            is ConnectMessage.Command, ConnectMessage.Release, is ConnectMessage.QueuesChanged -> _events.trySend(m)
            else -> Unit
        }
    }

    private fun send(text: String): Boolean = socket?.send(text) ?: false

    /** From the watch's playback service while the watch is the active device. */
    fun reportState(s: PlaybackState) {
        val mine = s.copy(activeDeviceId = _myId.value, rev = _state.value?.rev ?: 0)
        _state.value = mine
        send(ConnectProtocol.state(mine))
    }

    fun command(action: String, args: Map<String, JsonElement> = emptyMap()): Boolean {
        // Optimistic local echo for play/pause so the button flips at once; the server's state follows.
        _state.value?.let { s ->
            when (action) {
                "play" -> _state.value = s.copy(playing = true)
                "pause" -> _state.value = s.copy(playing = false)
            }
        }
        return send(ConnectProtocol.command(action, args, _state.value?.activeDeviceId))
    }

    fun transfer(toDeviceId: String, play: Boolean = true) = send(ConnectProtocol.transfer(toDeviceId, play))

    /** The user started playback on the watch: become the active device (the old one gets "release"). */
    fun claimPlayback() {
        val me = _myId.value ?: return
        if (_state.value?.activeDeviceId != me) {
            _state.value = (_state.value ?: PlaybackState()).copy(activeDeviceId = me)
            transfer(me, true)
        }
    }

    companion object {
        fun hello(deviceName: String, appVersion: String) = buildJsonObject {
            put("type", "hello"); put("deviceName", deviceName); put("kind", "watch")
            put("canPlay", true); put("appVersion", appVersion)
        }.toString()
    }
}
