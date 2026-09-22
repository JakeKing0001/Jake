package com.jake.companion.repository

import com.jake.companion.api.ApiException
import com.jake.companion.api.CompanionApiClient
import com.jake.companion.api.CompanionPaths
import com.jake.companion.api.FingerprintTrustManager
import com.jake.companion.connection.ConnectionState
import com.jake.companion.connection.ReconnectPolicy
import com.jake.companion.events.EventStreamClient
import com.jake.companion.protocol.DeviceInfoDto
import com.jake.companion.storage.PairedDevice
import com.jake.companion.storage.SecureDeviceStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

sealed class PairingOutcome {
    object Approved : PairingOutcome()
    object Rejected : PairingOutcome()
    object Expired : PairingOutcome()
    object Pending : PairingOutcome()
    data class Failed(val error: Throwable) : PairingOutcome()
}

/**
 * Il PONTE tra il telefono e Jake (F7.2) - e nient'altro: nessuna copia di `ConversationState`, nessuna
 * `PolicyEngine`, nessun `TaskAgent` lato mobile (l'ask esplicito di questo incremento). Ogni chiamata qui sotto
 * arriva a un endpoint GIA' esposto da `core/companion_server.py` (F7.1/F7.2); ogni evento ricevuto passa da
 * [reduce] (`UiStateReducer.kt`), che legge cio' che il PC ha gia' deciso, mai lo ricalcola. Il telefono e' una
 * SURFACE di Jake: se questo file iniziasse a decidere qualcosa da solo (quando interrompere, se un'azione e'
 * rischiosa...) sarebbe un secondo Jake, esattamente cio' che questo incremento vieta.
 */
class JakeRepository(
    private val secureStore: SecureDeviceStore,
    private val scope: CoroutineScope,
    private val json: Json = Json { ignoreUnknownKeys = true },
    private val reconnectPolicy: ReconnectPolicy = ReconnectPolicy(),
) {
    private val _uiState = MutableStateFlow(UiState())
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()

    private var apiClient: CompanionApiClient? = null
    private var eventStreamClient: EventStreamClient? = null
    private var lastSequenceId: Long = 0
    private var reconnectAttempt: Int = 0
    private var reconnectJob: Job? = null

    // ---- pairing (F7.1.2) --------------------------------------------------------------------------------------

    /** Apre una nuova challenge di pairing - l'approvazione VERA resta un "si'" (o la passphrase, se
     * configurata) detto/scritto sul PC (`core/jake_core.py::_on_pairing_requested`), mai un secondo passo HTTP
     * che questo stesso telefono potrebbe completare da solo. */
    fun startPairing(
        host: String, port: Int, useTls: Boolean, requestedName: String,
        onChallenge: (challengeId: String, tlsFingerprint: String?) -> Unit, onError: (Throwable) -> Unit,
    ) {
        scope.launch(Dispatchers.IO) {
            try {
                // Trust-on-first-use: durante IL pairing non c'e' ancora un'impronta fissata da pinnare - lo
                // stesso rischio della primissima connessione SSH a un host. L'utente la conferma leggendola a
                // schermo PRIMA di dire "si'" sul PC; ogni chiamata SUCCESSIVA (claim/command/eventi) userra'
                // invece sempre l'impronta gia' fissata (vedi buildHttpClient).
                val client = buildHttpClient(useTls, pinnedFingerprint = null)
                val api = CompanionApiClient(baseUrl(host, port, useTls), client, json)
                val response = api.pairingStart(requestedName)
                withContext(Dispatchers.Main) { onChallenge(response.challenge_id, response.tls_fingerprint) }
            } catch (t: Throwable) {
                withContext(Dispatchers.Main) { onError(t) }
            }
        }
    }

    /** Un singolo poll (F7.1.2: `GET /pairing/<challenge_id>`) - chi chiama (la schermata di pairing) decide il
     * ritmo con cui richiamarlo, questo metodo non pianifica nulla da solo. */
    fun pollPairing(
        host: String, port: Int, useTls: Boolean, challengeId: String, host_name: String,
        onResult: (PairingOutcome) -> Unit,
    ) {
        scope.launch(Dispatchers.IO) {
            try {
                val client = buildHttpClient(useTls, pinnedFingerprint = null)
                val api = CompanionApiClient(baseUrl(host, port, useTls), client, json)
                val response = api.pairingPoll(challengeId)
                val outcome = when (response.status) {
                    "approved" -> {
                        val deviceId = response.device_id
                        val token = response.token
                        if (deviceId != null && token != null) {
                            secureStore.save(
                                PairedDevice(deviceId, token, host, port, useTls, tlsFingerprint = null, name = host_name),
                            )
                            PairingOutcome.Approved
                        } else {
                            PairingOutcome.Failed(IllegalStateException("risposta 'approved' senza device_id/token"))
                        }
                    }
                    "rejected" -> PairingOutcome.Rejected
                    "expired" -> PairingOutcome.Expired
                    else -> PairingOutcome.Pending
                }
                withContext(Dispatchers.Main) { onResult(outcome) }
            } catch (t: Throwable) {
                withContext(Dispatchers.Main) { onResult(PairingOutcome.Failed(t)) }
            }
        }
    }

    /** Da chiamare SOLO dopo `PairingOutcome.Approved`, con l'impronta che l'utente ha appena confermato a
     * schermo: la fissa nello storage sicuro, cosi' ogni connessione SUCCESSIVA la pinna invece di fidarsi di
     * nuovo alla cieca (vedi buildHttpClient). */
    fun confirmPairedFingerprint(tlsFingerprint: String?) {
        val device = secureStore.load() ?: return
        secureStore.save(device.copy(tlsFingerprint = tlsFingerprint))
    }

    // ---- connessione principale (F7.2: stato online/offline, reconnect) -----------------------------------------

    fun start() {
        val device = secureStore.load()
        if (device == null) {
            _uiState.value = _uiState.value.copy(connection = ConnectionState.Unpaired)
            return
        }
        connectWith(device)
    }

    private fun connectWith(device: PairedDevice) {
        reconnectJob?.cancel()
        _uiState.value = _uiState.value.copy(connection = ConnectionState.Connecting)
        val client = buildHttpClient(device.useTls, device.tlsFingerprint)
        val api = CompanionApiClient(baseUrl(device.host, device.port, device.useTls), client, json)
        apiClient = api
        scope.launch(Dispatchers.IO) {
            try {
                val claim = api.claim(device.deviceId, device.token, device.name)
                reconnectAttempt = 0
                withContext(Dispatchers.Main) {
                    _uiState.value = _uiState.value.copy(connection = ConnectionState.Online(device.deviceId, claim.session_id))
                }
                openEventStream(device, client, claim.session_id)
            } catch (t: Throwable) {
                withContext(Dispatchers.Main) { scheduleReconnect(device, describeFailure(t)) }
            }
        }
    }

    private fun openEventStream(device: PairedDevice, client: OkHttpClient, sessionId: String) {
        val stream = EventStreamClient(client, json)
        eventStreamClient = stream
        stream.connect(
            url = baseUrl(device.host, device.port, device.useTls) + CompanionPaths.events(),
            token = device.token,
            lastEventId = lastSequenceId,
            onEvent = { event ->
                lastSequenceId = event.sequence_id
                scope.launch(Dispatchers.Main) { _uiState.value = reduce(_uiState.value, event, json) }
            },
            onOpen = { },
            onFailure = { throwable, _ ->
                scope.launch(Dispatchers.Main) {
                    // Ignora un fallimento se nel frattempo abbiamo GIA' avviato una riconnessione (evita due
                    // sequenze di reconnect concorrenti per lo stesso stream chiuso).
                    if (_uiState.value.connection is ConnectionState.Online) {
                        scheduleReconnect(device, describeFailure(throwable) ?: "connessione persa")
                    }
                }
            },
        )
    }

    private fun scheduleReconnect(device: PairedDevice, reason: String) {
        val lastSessionId = when (val current = _uiState.value.connection) {
            is ConnectionState.Online -> current.sessionId
            is ConnectionState.Reconnecting -> current.lastSessionId
            is ConnectionState.Offline -> current.lastSessionId
            else -> null
        }
        reconnectAttempt += 1
        _uiState.value = _uiState.value.copy(
            connection = ConnectionState.Reconnecting(device.deviceId, lastSessionId, reconnectAttempt),
        )
        val delayMs = reconnectPolicy.delayForAttempt(reconnectAttempt)
        reconnectJob = scope.launch {
            delay(delayMs)
            connectWith(device)
        }
    }

    // ---- chat (F7.2: "usa /command e quindi la STESSA pipeline JakeCore") ----------------------------------------

    fun sendCommand(text: String) {
        val device = secureStore.load() ?: return
        val client = apiClient ?: return
        val sessionId = (_uiState.value.connection as? ConnectionState.Online)?.sessionId
        scope.launch(Dispatchers.IO) {
            try {
                client.sendCommand(device.token, text, device.deviceId, sessionId)
                // La risposta arriva ANCHE via SSE (USER_MESSAGE/JAKE_MESSAGE - JakeCore.answer le pubblica gia'
                // per OGNI scambio, non solo quelli da questo telefono: core/jake_core.py). Non la si duplica
                // leggendo command.response qui: lo stream resta l'UNICA fonte della cronologia mostrata,
                // altrimenti ogni messaggio comparirebbe due volte.
            } catch (t: Throwable) {
                withContext(Dispatchers.Main) { _uiState.value = _uiState.value.copy(lastError = describeFailure(t)) }
            }
        }
    }

    // ---- decisione richiesta (F6.3/F6.7 -> F7.2.4/F7.2.8) ----------------------------------------------------------

    /** "approve"/"deny" diventano "si'"/"no" sullo STESSO task, attraverso `POST /approvals/<task_id>`
     * (`core/companion_server.py::_handle_approval`) - la STESSA pipeline di conferma di JakeCore, mai un
     * secondo motore di esecuzione lato mobile. */
    fun resolveDecision(decision: PendingDecision, approve: Boolean) {
        val device = secureStore.load() ?: return
        val client = apiClient ?: return
        scope.launch(Dispatchers.IO) {
            try {
                client.sendApproval(device.token, decision.taskId, if (approve) "approve" else "deny", decision.sessionId)
                withContext(Dispatchers.Main) {
                    if (_uiState.value.pendingDecision?.taskId == decision.taskId) {
                        _uiState.value = _uiState.value.copy(pendingDecision = null)
                    }
                }
            } catch (t: Throwable) {
                // Un errore nell'INVIO dell'approvazione non deve far sparire la decisione dalla UI: l'utente
                // deve poter riprovare, mai perdere silenziosamente una richiesta che magari e' ADMIN/DESTRUCTIVE.
                withContext(Dispatchers.Main) { _uiState.value = _uiState.value.copy(lastError = describeFailure(t)) }
            }
        }
    }

    // ---- dispositivi (F7.2.1) ---------------------------------------------------------------------------------

    fun listDevices(onResult: (List<DeviceInfoDto>) -> Unit) {
        val device = secureStore.load() ?: return
        val client = apiClient ?: return
        scope.launch(Dispatchers.IO) {
            val response = runCatching { client.listDevices(device.token) }.getOrNull()
            withContext(Dispatchers.Main) { onResult(response?.devices ?: emptyList()) }
        }
    }

    /** Revoca QUESTO telefono sul server (`POST /devices/<id>/revoke`, self-only) e poi cancella la credenziale
     * locale - in quest'ordine: se la revoca remota fallisce, il telefono resta comunque autenticato e puo'
     * riprovare, invece di "scollegarsi" localmente lasciando un token ancora valido che nessuno ricorda piu'. */
    fun revokeThisDevice(onDone: (Boolean) -> Unit) {
        val device = secureStore.load() ?: return
        val client = apiClient ?: return
        scope.launch(Dispatchers.IO) {
            val revoked = runCatching { client.revoke(device.deviceId, device.token) }.map { it.revoked }.getOrDefault(false)
            eventStreamClient?.disconnect()
            secureStore.clear()
            withContext(Dispatchers.Main) {
                _uiState.value = UiState(connection = ConnectionState.Unpaired)
                onDone(revoked)
            }
        }
    }

    fun stop() {
        reconnectJob?.cancel()
        eventStreamClient?.disconnect()
    }

    // ---- HTTP/TLS -------------------------------------------------------------------------------------------------

    private fun baseUrl(host: String, port: Int, useTls: Boolean): String =
        "${if (useTls) "https" else "http"}://$host:$port"

    private fun describeFailure(t: Throwable?): String? = when (t) {
        null -> null
        is ApiException -> t.errorCode
        else -> t.message ?: t::class.simpleName
    }

    /** `pinnedFingerprint == null` con `useTls == true` accade SOLO durante il pairing (nessuna impronta ancora
     * fissata, vedi [startPairing]/[pollPairing]) - ogni altra chiamata (claim/comandi/eventi/dispositivi) passa
     * sempre con un'impronta gia' salvata, mai in questa modalita'. */
    private fun buildHttpClient(useTls: Boolean, pinnedFingerprint: String?): OkHttpClient {
        val builder = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS) // /events resta aperto apposta: nessun timeout di lettura sullo stream
        if (useTls) {
            val trustManager: X509TrustManager = if (pinnedFingerprint != null) {
                FingerprintTrustManager(pinnedFingerprint)
            } else {
                TrustOnFirstUseOnlyDuringPairing
            }
            val sslContext = SSLContext.getInstance("TLS")
            sslContext.init(null, arrayOf<TrustManager>(trustManager), SecureRandom())
            builder.sslSocketFactory(sslContext.socketFactory, trustManager)
            // La verifica e' sull'IMPRONTA (il TrustManager sopra), non sull'hostname: lo stesso motivo per cui
            // SAN/CN del certificato non contano per un certificato locale autofirmato (core/companion_tls.py).
            builder.hostnameVerifier { _, _ -> true }
        }
        return builder.build()
    }

    /** Accetta QUALUNQUE certificato - usato ESCLUSIVAMENTE per il primo scambio di pairing (vedi
     * buildHttpClient), mai per una connessione autenticata. Lo stesso rischio della primissima connessione SSH
     * a un host: l'utente conferma l'impronta a schermo subito dopo, prima di procedere. */
    private object TrustOnFirstUseOnlyDuringPairing : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }
}
