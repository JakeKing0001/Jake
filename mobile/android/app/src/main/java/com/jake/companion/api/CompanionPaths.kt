package com.jake.companion.api

import java.net.URLEncoder

/**
 * Costruisce SOLO i percorsi HTTP del Companion Server (F7.1/F7.2) - nessuna logica di rete, nessun client qui:
 * per questo e' testabile su JVM puro senza Android/OkHttp (vedi `CompanionPathsTest.kt`). Rispecchia
 * `core/companion_server.py::do_GET`/`do_POST` percorso per percorso, mai un percorso inventato lato mobile.
 */
object CompanionPaths {
    fun status(): String = "/status"

    fun events(): String = "/events"

    fun pairingStart(): String = "/pairing/start"

    fun pairingPoll(challengeId: String): String = "/pairing/${encode(challengeId)}"

    fun command(): String = "/command"

    fun claim(deviceId: String): String = "/devices/${encode(deviceId)}/claim"

    fun release(deviceId: String): String = "/devices/${encode(deviceId)}/release"

    fun revoke(deviceId: String): String = "/devices/${encode(deviceId)}/revoke"

    fun approvals(taskId: String): String = "/approvals/${encode(taskId)}"

    fun devices(): String = "/devices"

    /** `core/companion_guard.py::_ID_RE` ammette solo `[A-Za-z0-9._:-]` - un id che rispetta gia' quel formato
     * (ogni id generato lato server, sempre) non cambia mai codificandolo; questo protegge solo un id inserito
     * a mano/malformato dal rompere il percorso invece di essere rifiutato in modo pulito dal server. */
    private fun encode(segment: String): String =
        URLEncoder.encode(segment, "UTF-8").replace("+", "%20")
}
