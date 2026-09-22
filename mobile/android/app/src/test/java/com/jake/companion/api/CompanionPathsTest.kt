package com.jake.companion.api

import org.junit.Assert.assertEquals
import org.junit.Test

/** Puro JVM, nessun framework Android - vedi `CompanionPaths.kt`. Ogni percorso confrontato letteralmente con
 * quello che `core/companion_server.py::do_GET`/`do_POST` riconosce davvero (F7.1/F7.2). */
class CompanionPathsTest {

    @Test
    fun `fixed paths match the server routes exactly`() {
        assertEquals("/status", CompanionPaths.status())
        assertEquals("/events", CompanionPaths.events())
        assertEquals("/pairing/start", CompanionPaths.pairingStart())
        assertEquals("/command", CompanionPaths.command())
        assertEquals("/devices", CompanionPaths.devices())
    }

    @Test
    fun `parameterized paths interpolate the identifier`() {
        assertEquals("/pairing/abc123", CompanionPaths.pairingPoll("abc123"))
        assertEquals("/devices/phone-1/claim", CompanionPaths.claim("phone-1"))
        assertEquals("/devices/phone-1/release", CompanionPaths.release("phone-1"))
        assertEquals("/devices/phone-1/revoke", CompanionPaths.revoke("phone-1"))
        assertEquals("/approvals/task-42", CompanionPaths.approvals("task-42"))
    }

    @Test
    fun `an identifier with characters outside the servers accepted set is url-encoded not left raw`() {
        // core/companion_guard.py::_ID_RE ammette solo [A-Za-z0-9._:-] - un id malformato arriva comunque
        // codificato, mai iniettato a crudo nel percorso (il server lo rifiutera' in modo pulito, non con un
        // percorso rotto).
        val path = CompanionPaths.claim("id con spazi/e?slash")
        assertEquals("/devices/id%20con%20spazi%2Fe%3Fslash/claim", path)
    }
}
