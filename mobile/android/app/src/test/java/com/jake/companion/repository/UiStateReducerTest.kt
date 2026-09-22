package com.jake.companion.repository

import com.jake.companion.protocol.HudEventDto
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Puro JVM - vedi `UiStateReducer.kt`. Il punto di questi test e' provare che `reduce()` fa SOLO presentazione:
 * legge una `DecisionDto`/`decision_required` gia' prodotta dal PC (F6.3 NotificationPolicy) e la mostra, senza
 * mai calcolare da sola se qualcosa e' rischioso o urgente (F7.2: "nessuna duplicazione ... di logica
 * decisionale lato mobile").
 */
class UiStateReducerTest {

    private val json = Json { ignoreUnknownKeys = true }

    private fun event(type: String, payload: JsonObject, at: Double = 1.0): HudEventDto =
        HudEventDto(schema_version = 1, type = type, payload = payload, at = at, sequence_id = 1)

    @Test
    fun `a user message event is appended as a chat message`() {
        val e = event("USER_MESSAGE", buildJsonObject { put("text", "accendi il pc") }, at = 42.0)
        val next = reduce(UiState(), e, json)
        assertEquals(1, next.messages.size)
        assertEquals(ChatMessage("user", "accendi il pc", 42.0), next.messages[0])
    }

    @Test
    fun `a jake message event is appended with the jake role`() {
        val e = event("JAKE_MESSAGE", buildJsonObject { put("text", "fatto") })
        val next = reduce(UiState(), e, json)
        assertEquals("jake", next.messages[0].role)
    }

    @Test
    fun `messages accumulate across successive events instead of replacing each other`() {
        var state = UiState()
        state = reduce(state, event("USER_MESSAGE", buildJsonObject { put("text", "uno") }), json)
        state = reduce(state, event("JAKE_MESSAGE", buildJsonObject { put("text", "due") }), json)
        assertEquals(listOf("uno", "due"), state.messages.map { it.text })
    }

    @Test
    fun `a chat event with no text field leaves the state unchanged`() {
        val e = event("USER_MESSAGE", buildJsonObject { })
        val state = UiState()
        assertSame(state, reduce(state, e, json))
    }

    @Test
    fun `an unknown event type leaves the state unchanged`() {
        val e = event("SOME_FUTURE_TYPE_NOT_YET_KNOWN", buildJsonObject { put("text", "x") })
        val state = UiState()
        assertSame(state, reduce(state, e, json))
    }

    @Test
    fun `a notification whose origin is not task_monitor never opens a decision`() {
        val e = event("NOTIFICATION", buildJsonObject {
            put("origin", "reminder")
            put("task_id", "t1")
            put("label", "promemoria")
            put("status", "needs_decision")
            put("message", "qualcosa")
        })
        val state = UiState()
        assertSame(state, reduce(state, e, json))
    }

    private fun taskMonitorPayload(
        taskId: String = "task-1",
        status: String = "needs_decision",
        sessionId: String? = "session-1",
    ): JsonObject = buildJsonObject {
        put("origin", "task_monitor")
        put("task_id", taskId)
        put("session_id", sessionId)
        put("label", "aggiorna dipendenze")
        put("status", status)
        put("message", "serve conferma prima di procedere")
        put("actions_done", buildJsonArray {
            add(buildJsonObject { put("intent", "backup"); put("success", true) })
        })
        put("decision_required", buildJsonObject {
            put("intent", "apply_update")
            put("message", "procedo con l'aggiornamento?")
            put("policy_reason", "azione irreversibile")
        })
        put("decision", buildJsonObject {
            put("action", "deliver_now")
            put("reason", "richiede conferma")
            put("priority", 0.9)
        })
    }

    @Test
    fun `a needs_decision task_monitor notification opens a pending decision built only from the servers payload`() {
        val e = event("NOTIFICATION", taskMonitorPayload())
        val next = reduce(UiState(), e, json)

        val pending = next.pendingDecision
        assertTrue(pending != null)
        assertEquals("task-1", pending!!.taskId)
        assertEquals("session-1", pending.sessionId)
        assertEquals("aggiorna dipendenze", pending.label)
        assertEquals("serve conferma prima di procedere", pending.situation)
        assertEquals(1, pending.actionsDone.size)
        assertEquals("backup", pending.actionsDone[0].intent)
        assertTrue(pending.actionsDone[0].success)
        assertEquals("apply_update", pending.decisionRequired?.intent)
        assertEquals("azione irreversibile", pending.decisionRequired?.policy_reason)
        assertEquals("deliver_now", pending.decision.action)
        assertEquals(0.9, pending.decision.priority, 0.0001)
        assertEquals("needs_decision", next.currentTaskStatus)
        assertEquals("aggiorna dipendenze", next.currentTaskLabel)
    }

    @Test
    fun `a completed notification for the pending tasks id clears that decision`() {
        val opened = reduce(UiState(), event("NOTIFICATION", taskMonitorPayload(taskId = "task-1")), json)
        assertTrue(opened.pendingDecision != null)

        val closed = reduce(opened, event("NOTIFICATION", taskMonitorPayload(taskId = "task-1", status = "completed")), json)
        assertNull(closed.pendingDecision)
        assertEquals("completed", closed.currentTaskStatus)
        assertNull(closed.currentTaskLabel)
    }

    @Test
    fun `an error notification for the pending tasks id also clears that decision`() {
        val opened = reduce(UiState(), event("NOTIFICATION", taskMonitorPayload(taskId = "task-1")), json)
        val closed = reduce(opened, event("NOTIFICATION", taskMonitorPayload(taskId = "task-1", status = "error")), json)
        assertNull(closed.pendingDecision)
        assertEquals("error", closed.currentTaskStatus)
    }

    @Test
    fun `completing a different task never clears a still-pending decision for another task`() {
        val opened = reduce(UiState(), event("NOTIFICATION", taskMonitorPayload(taskId = "task-1")), json)
        val untouched = reduce(opened, event("NOTIFICATION", taskMonitorPayload(taskId = "task-2", status = "completed")), json)
        assertEquals("task-1", untouched.pendingDecision?.taskId)
    }

    @Test
    fun `an unrecognized status on a task_monitor notification leaves the state unchanged`() {
        val e = event("NOTIFICATION", taskMonitorPayload(status = "some_future_status"))
        val state = UiState()
        assertSame(state, reduce(state, e, json))
    }

    @Test
    fun `a task_monitor notification missing required fields is ignored rather than crashing`() {
        val malformed = buildJsonObject {
            put("origin", "task_monitor")
            // manca task_id/label/status/message/decision - il payload non decodifica come TaskNotificationPayload.
        }
        val state = UiState()
        assertSame(state, reduce(state, event("NOTIFICATION", malformed), json))
    }
}
