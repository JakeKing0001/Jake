package com.jake.companion.connection

/**
 * Stato della connessione DI QUESTO telefono al Companion Server - solo presentazione (F7.2: "gestione perdita
 * connessione, reconnect e stato offline"). Nessuna decisione di dominio: e' lo stato che la schermata
 * principale mostra come online/offline/in riconnessione, niente altro.
 */
sealed class ConnectionState {
    /** Nessuna credenziale salvata: l'app deve ancora fare il pairing. */
    object Unpaired : ConnectionState()

    /** Credenziale presente, primo tentativo di collegamento in corso (claim + apertura di /events). */
    object Connecting : ConnectionState()

    /** Collegato: sessione attiva, stream eventi aperto. */
    data class Online(val deviceId: String, val sessionId: String) : ConnectionState()

    /** Il collegamento e' caduto (errore di rete, stream chiuso) - `lastSessionId` e' l'ultima sessione nota,
     * solo per mostrarla mentre si aspetta di riconnettersi, MAI riusata come se fosse ancora valida (una nuova
     * /devices/<id>/claim ne produce sempre una nuova). */
    data class Offline(val deviceId: String, val lastSessionId: String?, val reason: String) : ConnectionState()

    /** Un nuovo tentativo di collegamento e' schedulato (vedi `ReconnectPolicy`) - `attempt` conta da 1. */
    data class Reconnecting(val deviceId: String, val lastSessionId: String?, val attempt: Int) : ConnectionState()
}
