package com.jake.companion.repository

import com.jake.companion.protocol.EventType
import com.jake.companion.protocol.HudEventDto
import com.jake.companion.protocol.TASK_MONITOR_ORIGIN
import com.jake.companion.protocol.TASK_STATUS_COMPLETED
import com.jake.companion.protocol.TASK_STATUS_ERROR
import com.jake.companion.protocol.TASK_STATUS_NEEDS_DECISION
import com.jake.companion.protocol.TaskNotificationPayload
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Riduce UN `HudEventDto` (gia' arrivato da `/events`) nello stato della UI - SOLO presentazione (F7.2: "nessuna
 * duplicazione di ConversationState, PolicyEngine, TaskAgent o logica decisionale lato mobile"). Questa funzione
 * non decide MAI se un'azione e' rischiosa, non esegue nulla, non chiama alcuna policy: legge cio' che il PC ha
 * gia' deciso (`DecisionDto`, gia' prodotto da `NotificationPolicy` lato server) e lo mostra. Pura, senza
 * effetti collaterali: testabile su JVM senza Android (vedi `UiStateReducerTest.kt`).
 *
 * Un evento di un tipo o con un payload che questa versione dell'app non riconosce lascia lo stato INVARIATO -
 * mai un crash su qualcosa pubblicato da una Jake futura con un vocabolario piu' ricco.
 */
fun reduce(state: UiState, event: HudEventDto, json: Json): UiState {
    return when (event.knownType) {
        EventType.USER_MESSAGE -> {
            val text = event.payload["text"]?.asStringOrNull() ?: return state
            state.copy(messages = state.messages + ChatMessage("user", text, event.at))
        }
        EventType.JAKE_MESSAGE -> {
            val text = event.payload["text"]?.asStringOrNull() ?: return state
            state.copy(messages = state.messages + ChatMessage("jake", text, event.at))
        }
        EventType.NOTIFICATION -> reduceNotification(state, event, json)
        else -> state
    }
}

private fun reduceNotification(state: UiState, event: HudEventDto, json: Json): UiState {
    // "pairing"/"reminder"/"advisory"/"trigger" (v4.3/F6.3) non aprono mai una decisione sul telefono - solo
    // gli eventi del task monitor (F6.7, origin == "task_monitor") lo fanno.
    val origin = event.payload["origin"]?.asStringOrNull()
    if (origin != TASK_MONITOR_ORIGIN) return state
    val notification = try {
        json.decodeFromJsonElement(TaskNotificationPayload.serializer(), event.payload)
    } catch (_: SerializationException) {
        return state
    }
    return when (notification.status) {
        TASK_STATUS_NEEDS_DECISION -> state.copy(
            currentTaskLabel = notification.label,
            currentTaskStatus = notification.status,
            pendingDecision = PendingDecision(
                taskId = notification.task_id, sessionId = notification.session_id, label = notification.label,
                situation = notification.message, actionsDone = notification.actions_done,
                decisionRequired = notification.decision_required, decision = notification.decision,
            ),
        )
        TASK_STATUS_COMPLETED, TASK_STATUS_ERROR -> {
            // Chiude SOLO la decisione DI QUESTO task - un secondo task (un altro task_id) potrebbe essere
            // ancora in attesa contemporaneamente, non va mai cancellato per errore.
            val stillPending = state.pendingDecision?.takeUnless { it.taskId == notification.task_id }
            state.copy(currentTaskLabel = null, currentTaskStatus = notification.status, pendingDecision = stillPending)
        }
        else -> state
    }
}

private fun kotlinx.serialization.json.JsonElement.asStringOrNull(): String? =
    (this as? JsonPrimitive)?.contentOrNull
