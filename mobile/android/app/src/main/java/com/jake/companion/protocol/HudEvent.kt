package com.jake.companion.protocol

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Rispecchia ESATTAMENTE `core/hud_protocol.py::EventType` - nessun valore nuovo inventato lato mobile. Il
 * telefono e' una SURFACE di Jake (vedi il docstring del modulo repository): il vocabolario degli eventi resta
 * quello che JakeCore gia' pubblica, non uno scelto qui.
 */
enum class EventType {
    HUD_SHOW, HUD_HIDE, USER_MESSAGE, JAKE_MESSAGE, LISTENING, THINKING, EXECUTING, ERROR,
    AGENT_STEP, NOTIFICATION, IDLE, DICTATION, PAUSED, DEVICE_HANDOFF, UNDO, VERIFICATION,
    TRANSCRIPT, MIC_STATE,
}

/**
 * Un evento SSE cosi' come arriva da `GET /events` - rispecchia `HudEvent.to_json()`
 * (`core/hud_protocol.py`). `type` resta una stringa libera (non l'enum direttamente): un tipo che questa
 * versione dell'app non conosce ancora (introdotto da una Jake piu' recente) non deve MAI far fallire il
 * parsing dell'intero evento - [knownType] e' `null` in quel caso, e chi riduce lo stato (vedi
 * `UiStateReducer.kt`) lo ignora invece di crashare.
 */
@Serializable
data class HudEventDto(
    val schema_version: Int = 1,
    val type: String,
    val payload: JsonObject = JsonObject(emptyMap()),
    val at: Double = 0.0,
    val sequence_id: Long = 0,
    val trace_id: String? = null,
) {
    val knownType: EventType?
        get() = runCatching { EventType.valueOf(type) }.getOrNull()
}
