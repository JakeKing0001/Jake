package com.jake.companion.ui.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Puro JVM - vedi `QrPayload.kt`. Un QR non e' mai un input fidato (potrebbe essere di un'altra app, corrotto,
 * o creato apposta per rompere il parsing): niente qui deve mai lanciare un'eccezione fuori da questa funzione. */
class QrPayloadTest {

    @Test
    fun `a well formed payload parses with all its fields`() {
        val payload = parseQrPairingPayload("""{"host":"192.168.1.20","port":8765,"tls":true}""")
        assertEquals(QrPairingPayload("192.168.1.20", 8765, true), payload)
    }

    @Test
    fun `tls defaults to true when the field is omitted`() {
        val payload = parseQrPairingPayload("""{"host":"192.168.1.20","port":8765}""")
        assertEquals(true, payload?.tls)
    }

    @Test
    fun `plain text that is not json returns null instead of throwing`() {
        assertNull(parseQrPairingPayload("this is a QR from a totally different app"))
    }

    @Test
    fun `json from an unrelated schema returns null`() {
        assertNull(parseQrPairingPayload("""{"wifi_ssid":"home","password":"secret"}"""))
    }

    @Test
    fun `a blank host is rejected`() {
        assertNull(parseQrPairingPayload("""{"host":"","port":8765}"""))
    }

    @Test
    fun `a port outside the valid tcp range is rejected`() {
        assertNull(parseQrPairingPayload("""{"host":"192.168.1.20","port":0}"""))
        assertNull(parseQrPairingPayload("""{"host":"192.168.1.20","port":70000}"""))
        assertNull(parseQrPairingPayload("""{"host":"192.168.1.20","port":-1}"""))
    }

    @Test
    fun `truncated or malformed json returns null`() {
        assertNull(parseQrPairingPayload("""{"host":"192.168.1.20","port":"""))
    }
}
