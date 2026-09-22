package com.jake.companion.repository

import com.jake.companion.connection.ConnectionState
import com.jake.companion.protocol.ActionDoneDto
import com.jake.companion.protocol.DecisionDto
import com.jake.companion.protocol.DecisionRequiredDto

/** Un turno di chat, ricostruito dagli eventi USER_MESSAGE/JAKE_MESSAGE che il PC pubblica GIA' per ogni scambio
 * (`core/jake_core.py::answer`, non solo quelli arrivati da questo telefono - la stessa cronologia che un HUD
 * locale o un altro companion vedrebbero). */
data class ChatMessage(val role: String, val text: String, val at: Double)

/** Cio' che serve alla schermata di notifica: "situazione, azioni gia' eseguite, evidenza disponibile e
 * decisione richiesta" (F7.2) - tutti campi che vengono GIA' dal payload del PC (`TaskNotificationPayload`),
 * mai calcolati qui. */
data class PendingDecision(
    val taskId: String,
    val sessionId: String?,
    val label: String,
    val situation: String,
    val actionsDone: List<ActionDoneDto>,
    val decisionRequired: DecisionRequiredDto?,
    val decision: DecisionDto,
)

data class UiState(
    val connection: ConnectionState = ConnectionState.Unpaired,
    val messages: List<ChatMessage> = emptyList(),
    val currentTaskLabel: String? = null,
    val currentTaskStatus: String? = null,
    val pendingDecision: PendingDecision? = null,
    val lastError: String? = null,
)
