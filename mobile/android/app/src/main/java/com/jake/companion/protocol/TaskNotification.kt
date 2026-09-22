package com.jake.companion.protocol

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Rispecchia ESATTAMENTE il payload che `core/task_notification_bridge.py::TaskNotificationBridge._publish`
 * mette in un `HudEvent` di tipo NOTIFICATION con `origin == "task_monitor"` (F6.3/F6.7 -> F7.2). Nessun campo
 * in piu' ne' in meno: e' la STESSA busta che il PC pubblica, letta cosi' com'e' - la UI mobile non deduce/
 * ricalcola nulla che il server non abbia gia' detto esplicitamente (task_id/session_id/azioni gia' fatte/
 * decisione richiesta/decisione di Jake sull'urgenza).
 */
@Serializable
data class ActionDoneDto(
    val intent: String,
    val success: Boolean,
)

@Serializable
data class DecisionRequiredDto(
    val intent: String? = null,
    val parameters: JsonObject? = null,
    val message: String,
    val policy_reason: String? = null,
)

/** `action`: "deliver_now" | "queue" | "digest" | "drop" - la decisione che NotificationPolicy (F6.3, lato PC)
 * ha gia' preso su come/se interrompere. La app la MOSTRA, non la ricalcola mai. */
@Serializable
data class DecisionDto(
    val action: String,
    val reason: String,
    val priority: Double,
    val channel: String? = null,
    val device_id: String? = null,
    val spoken_text: String? = null,
    val screen_text: String? = null,
)

/** `status`: "needs_decision" | "completed" | "error" (core/task_monitor.py::TaskStatus - solo i tre valori che
 * generano davvero una notifica, F6.7.2). */
@Serializable
data class TaskNotificationPayload(
    val origin: String,
    val task_id: String,
    val session_id: String? = null,
    val device_id: String? = null,
    val label: String,
    val status: String,
    val message: String,
    val actions_done: List<ActionDoneDto> = emptyList(),
    val decision_required: DecisionRequiredDto? = null,
    val decision: DecisionDto,
)

const val TASK_MONITOR_ORIGIN = "task_monitor"
const val TASK_STATUS_NEEDS_DECISION = "needs_decision"
const val TASK_STATUS_COMPLETED = "completed"
const val TASK_STATUS_ERROR = "error"
