package com.jake.companion.connection

import kotlin.math.min
import kotlin.math.pow

/**
 * Ritmo dei tentativi di riconnessione dopo una disconnessione (F7.2: "gestione perdita connessione, reconnect")
 * - backoff esponenziale con un tetto e un po' di jitter, cosi' un'interruzione di rete non fa martellare il
 * Companion Server con una raffica di richieste. Puro calcolo, nessuna chiamata di rete/stato qui: e' solo un
 * "quanto aspettare", non una decisione su COSA fare (quella resta a `JakeRepository`).
 */
class ReconnectPolicy(
    private val baseDelayMs: Long = 1_000L,
    private val maxDelayMs: Long = 30_000L,
    private val jitterFraction: Double = 0.2,
    private val random: () -> Double = { Math.random() },
) {
    init {
        require(baseDelayMs > 0) { "baseDelayMs deve essere positivo" }
        require(maxDelayMs >= baseDelayMs) { "maxDelayMs deve essere >= baseDelayMs" }
        require(jitterFraction in 0.0..1.0) { "jitterFraction deve essere tra 0 e 1" }
    }

    /** Millisecondi da attendere prima del tentativo numero `attempt` (il primo tentativo e' 1, non 0). */
    fun delayForAttempt(attempt: Int): Long {
        require(attempt >= 1) { "attempt deve essere >= 1" }
        val exponential = baseDelayMs * 2.0.pow(attempt - 1)
        val capped = min(exponential, maxDelayMs.toDouble())
        val jitter = capped * jitterFraction * (random() * 2 - 1) // +/- jitterFraction intorno al valore
        return capped.plus(jitter).toLong().coerceIn(0L, maxDelayMs)
    }
}
