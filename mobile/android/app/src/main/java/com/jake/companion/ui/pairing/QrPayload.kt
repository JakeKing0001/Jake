package com.jake.companion.ui.pairing

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Il contenuto atteso in un QR di pairing (F7.1.2/F7.2): SOLO l'indirizzo del PC - MAI un token o un
 * challenge_id (la challenge nasce dalla chiamata `POST /pairing/start` che il telefono fa DOPO aver letto
 * questo QR, stesso principio "il QR porta solo dati non sensibili" gia' applicato lato PC per
 * `core/pairing_service.py::qr_payload`).
 *
 * Nessun generatore di questo QR esiste ancora lato PC (limite dichiarato, vedi ROADMAP_EXECUTION.md): questo e'
 * il lato SCANSIONE del flusso, pronto per quando un futuro pannello locale lo mostrera' - fino ad allora,
 * l'inserimento manuale nel modulo resta il percorso completo.
 */
@Serializable
data class QrPairingPayload(val host: String, val port: Int, val tls: Boolean = true)

/** `null` per qualunque testo che non sia il JSON atteso (un QR di un'altra app, uno corrotto) - mai
 * un'eccezione che interrompa la schermata di pairing. */
fun parseQrPairingPayload(raw: String, json: Json = Json { ignoreUnknownKeys = true }): QrPairingPayload? {
    val payload = runCatching { json.decodeFromString(QrPairingPayload.serializer(), raw) }.getOrNull() ?: return null
    if (payload.host.isBlank() || payload.port !in 1..65535) return null
    return payload
}
