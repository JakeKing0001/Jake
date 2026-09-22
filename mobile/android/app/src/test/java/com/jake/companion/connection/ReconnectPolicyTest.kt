package com.jake.companion.connection

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** Puro JVM - vedi `ReconnectPolicy.kt`. Il jitter e' neutralizzato passando un `random` fisso, cosi' i tempi
 * attesi sono verificabili esattamente invece che solo "in un intervallo". */
class ReconnectPolicyTest {

    private fun noJitterPolicy(base: Long = 1_000L, max: Long = 30_000L) =
        ReconnectPolicy(baseDelayMs = base, maxDelayMs = max, jitterFraction = 0.0, random = { 0.5 })

    @Test
    fun `the first attempt waits exactly the base delay with no jitter`() {
        assertEquals(1_000L, noJitterPolicy().delayForAttempt(1))
    }

    @Test
    fun `each attempt doubles the previous delay until the cap`() {
        val policy = noJitterPolicy()
        assertEquals(1_000L, policy.delayForAttempt(1))
        assertEquals(2_000L, policy.delayForAttempt(2))
        assertEquals(4_000L, policy.delayForAttempt(3))
        assertEquals(8_000L, policy.delayForAttempt(4))
        assertEquals(16_000L, policy.delayForAttempt(5))
    }

    @Test
    fun `the delay never exceeds the configured cap`() {
        val policy = noJitterPolicy(max = 30_000L)
        assertEquals(30_000L, policy.delayForAttempt(6))
        assertEquals(30_000L, policy.delayForAttempt(20))
    }

    @Test
    fun `jitter stays within the declared fraction around the value`() {
        val policy = ReconnectPolicy(baseDelayMs = 1_000L, maxDelayMs = 30_000L, jitterFraction = 0.2, random = { 1.0 })
        // random() = 1.0 -> il jitter e' al suo MASSIMO positivo: +20% esatto del valore (nessun tetto raggiunto qui).
        assertEquals(1_200L, policy.delayForAttempt(1))
    }

    @Test
    fun `jitter can also push the delay below the raw exponential value`() {
        val policy = ReconnectPolicy(baseDelayMs = 1_000L, maxDelayMs = 30_000L, jitterFraction = 0.2, random = { 0.0 })
        // random() = 0.0 -> il jitter e' al suo MINIMO (negativo): -20% esatto.
        assertEquals(800L, policy.delayForAttempt(1))
    }

    @Test
    fun `the result is never negative even with an extreme jitter`() {
        val policy = ReconnectPolicy(baseDelayMs = 1_000L, maxDelayMs = 30_000L, jitterFraction = 1.0, random = { 0.0 })
        assertTrue(policy.delayForAttempt(1) >= 0L)
    }

    @Test
    fun `attempt zero or negative is rejected`() {
        val policy = noJitterPolicy()
        assertThrows(IllegalArgumentException::class.java) { policy.delayForAttempt(0) }
        assertThrows(IllegalArgumentException::class.java) { policy.delayForAttempt(-1) }
    }

    @Test
    fun `an invalid configuration is rejected at construction`() {
        assertThrows(IllegalArgumentException::class.java) { ReconnectPolicy(baseDelayMs = 0) }
        assertThrows(IllegalArgumentException::class.java) { ReconnectPolicy(baseDelayMs = 5_000L, maxDelayMs = 1_000L) }
        assertThrows(IllegalArgumentException::class.java) { ReconnectPolicy(jitterFraction = 1.5) }
    }
}
