package org.nasmusic.app.connect

import kotlin.random.Random
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.JsonElement
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.nasmusic.app.net.HttpClients
import org.nasmusic.app.net.Route
import org.nasmusic.app.net.RoutedCallFactory

/**
 * One WebSocket to `/api/v1/connect` over the active route (LAN pinned or tunnel + CF headers,
 * bearer auth from [HttpClients]). Reconnects with exponential backoff and whenever the route changes.
 */
class ConnectClient(
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

    /**
     * Commands / takeover / release / queue changes for the playback service. A buffered channel,
     * not a SharedFlow: if the service isn't running yet (e.g. another device transfers playback to
     * this phone while the app is closed) the events wait for it instead of being dropped.
     */
    private val _events = kotlinx.coroutines.channels.Channel<ConnectMessage>(64)
    val events: kotlinx.coroutines.flow.Flow<ConnectMessage> = _events.receiveAsFlow()

    /** Called on takeover so the app can start the playback service (set by the container). */
    @Volatile var onTakeover: () -> Unit = {}
    /** A speaker the server plays on reported a problem ("Kitchen: the speaker couldn't play this track"). */
    @Volatile var onSpeakerError: (String) -> Unit = {}

    @Volatile private var socket: WebSocket? = null
    private var loop: Job? = null

    val isActiveHere: Boolean get() = _state.value?.activeDeviceId.let { it != null && it == _myId.value }

    /** Another online device is playing: this phone acts as a remote. */
    val isRemote: Boolean
        get() {
            val s = _state.value ?: return false
            val active = s.activeDeviceId ?: return false
            if (active == _myId.value) return false
            return _devices.value.any { it.id == active && it.online }
        }

    fun activeDevice(): ConnectDevice? = _state.value?.activeDeviceId?.let { id -> _devices.value.firstOrNull { it.id == id } }

    fun start() {
        if (loop != null) return
        loop = scope.launch { runLoop() }
        scope.launch {
            route.collect { socket?.close(1001, "route changed") }
        }
    }

    fun stop() {
        loop?.cancel(); loop = null
        socket?.close(1000, "bye"); socket = null
        _connected.value = false
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

    /** Runs one socket until it closes. Returns true if the server welcomed us. */
    private suspend fun connectOnce(c: HttpClients, r: Route): Boolean {
        val base = c.config.baseFor(r) ?: return false
        val url = RoutedCallFactory.rewrite(RoutedCallFactory.virtual("connect").toHttpUrl(), base)
        var welcomed = false
        suspendCancellableCoroutine { cont ->
            val listener = object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send(ConnectProtocol.hello(deviceName(), appVersion))
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
            is ConnectMessage.Error -> if (m.source == "speaker" && m.detail.isNotBlank()) onSpeakerError(m.detail)
            else -> Unit
        }
    }

    private fun send(text: String): Boolean = socket?.send(text) ?: false

    /** Called by the playback service while this phone is the active device. */
    fun reportState(s: PlaybackState) {
        val mine = s.copy(activeDeviceId = _myId.value, rev = _state.value?.rev ?: 0)
        _state.value = mine
        send(ConnectProtocol.state(mine))
    }

    fun command(action: String, args: Map<String, JsonElement> = emptyMap()) =
        send(ConnectProtocol.command(action, args, _state.value?.activeDeviceId))

    fun transfer(toDeviceId: String, play: Boolean = true) = send(ConnectProtocol.transfer(toDeviceId, play))

    /** The user pressed play on this phone: become the active device (the old one gets "release"). */
    fun claimPlayback() {
        val me = _myId.value ?: return
        if (_state.value?.activeDeviceId != me) {
            _state.value = (_state.value ?: PlaybackState()).copy(activeDeviceId = me)
            transfer(me, true)
        }
    }
}
