package com.jake.companion.protocol

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Puro JVM - decodifica di quello che arriva davvero da `GET /events` (`core/hud_protocol.py::HudEvent.to_json`)
 * e dal payload NOTIFICATION del task monitor (`core/task_notification_bridge.py`). Ogni stringa qui e' presa
 * letteralmente da un esempio reale di quel formato, non inventata lato mobile.
 */
class HudEventParsingTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `a real hud event line decodes with every field in place`() {
        val raw = """
            {"schema_version":1,"type":"JAKE_MESSAGE","payload":{"text":"fatto"},"at":1727000000.5,
             "sequence_id":42,"trace_id":"trace-abc"}
        """.trimIndent()
        val event = json.decodeFromString(HudEventDto.serializer(), raw)
        assertEquals(1, event.schema_version)
        assertEquals("JAKE_MESSAGE", event.type)
        assertEquals(EventType.JAKE_MESSAGE, event.knownType)
        assertEquals(1727000000.5, event.at, 0.0001)
        assertEquals(42L, event.sequence_id)
        assertEquals("trace-abc", event.trace_id)
    }

    @Test
    fun `a known event type resolves to its enum value`() {
        val event = json.decodeFromString(HudEventDto.serializer(), """{"type":"NOTIFICATION","payload":{}}""")
        assertEquals(EventType.NOTIFICATION, event.knownType)
    }

    @Test
    fun `an event type this app version does not know yet never fails to parse`() {
        val event = json.decodeFromString(
            HudEventDto.serializer(),
            """{"type":"FUTURE_EVENT_TYPE_FROM_A_NEWER_JAKE","payload":{}}""",
        )
        assertEquals("FUTURE_EVENT_TYPE_FROM_A_NEWER_JAKE", event.type)
        assertNull(event.knownType)
    }

    @Test
    fun `optional fields fall back to their declared defaults when absent`() {
        val event = json.decodeFromString(HudEventDto.serializer(), """{"type":"IDLE"}""")
        assertEquals(1, event.schema_version)
        assertEquals(0.0, event.at, 0.0001)
        assertEquals(0L, event.sequence_id)
        assertNull(event.trace_id)
    }

    @Test
    fun `unknown extra fields from a newer server are ignored rather than failing decoding`() {
        val event = json.decodeFromString(
            HudEventDto.serializer(),
            """{"type":"IDLE","payload":{},"a_field_this_app_does_not_know_about":123}""",
        )
        assertEquals("IDLE", event.type)
    }

    @Test
    fun `a full task_monitor notification payload decodes field for field`() {
        val raw = """
            {"origin":"task_monitor","task_id":"task-7","session_id":"session-9","device_id":"phone-1",
             "label":"pulizia file temporanei","status":"needs_decision","message":"trovati 3GB da rimuovere",
             "actions_done":[{"intent":"scan_disk","success":true},{"intent":"list_candidates","success":true}],
             "decision_required":{"intent":"delete_files","message":"procedo con la cancellazione?",
                                   "policy_reason":"azione distruttiva non reversibile"},
             "decision":{"action":"deliver_now","reason":"richiede conferma esplicita","priority":0.95,
                         "channel":"push","device_id":"phone-1"}}
        """.trimIndent()
        val payload = json.decodeFromString(TaskNotificationPayload.serializer(), raw)

        assertEquals(TASK_MONITOR_ORIGIN, payload.origin)
        assertEquals("task-7", payload.task_id)
        assertEquals("session-9", payload.session_id)
        assertEquals(TASK_STATUS_NEEDS_DECISION, payload.status)
        assertEquals(2, payload.actions_done.size)
        assertTrue(payload.actions_done.all { it.success })
        assertEquals("delete_files", payload.decision_required?.intent)
        assertEquals("azione distruttiva non reversibile", payload.decision_required?.policy_reason)
        assertEquals("deliver_now", payload.decision.action)
        assertEquals(0.95, payload.decision.priority, 0.0001)
        assertEquals("push", payload.decision.channel)
    }

    @Test
    fun `a completed notification with no decision_required and no actions decodes with the declared defaults`() {
        val raw = """
            {"origin":"task_monitor","task_id":"task-8","label":"backup notturno","status":"completed",
             "message":"completato senza intervento","decision":{"action":"drop","reason":"nessuna decisione da mostrare","priority":0.1}}
        """.trimIndent()
        val payload = json.decodeFromString(TaskNotificationPayload.serializer(), raw)

        assertTrue(payload.actions_done.isEmpty())
        assertNull(payload.decision_required)
        assertNull(payload.session_id)
        assertEquals(TASK_STATUS_COMPLETED, payload.status)
    }
}
