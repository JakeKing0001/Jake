package com.jake.companion.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Corpi di richiesta/risposta verso il Companion Server (F7.1/F7.2) - rispecchiano ESATTAMENTE i campi che
 * `core/companion_server.py`/`core/companion_guard.py` producono o accettano oggi. Nessun campo aggiuntivo,
 * nessuna logica: sono solo dati sul filo.
 */
@Serializable
data class SyncPublicKeyDto(val sign: String, val agree: String)

@Serializable
data class PairingStartRequest(val requested_name: String, val sync_public_key: SyncPublicKeyDto? = null)

@Serializable
data class PairingStartResponse(val challenge_id: String, val expires_at: Double, val tls_fingerprint: String? = null)

/** `status`: "pending" | "approved" | "rejected" | "expired" | "already_delivered" (`core/companion_server.py::
 * _handle_pairing_poll`). `device_id`/`token`/`expires_at` sono presenti SOLO quando `status == "approved"` - e
 * SOLO in quella risposta, mai di nuovo (consegna one-time, `core/pairing_service.py::take_result`). */
@Serializable
data class PairingPollResponse(
    val status: String,
    val device_id: String? = null,
    val token: String? = null,
    val expires_at: Double? = null,
)

@Serializable
data class ClaimRequest(val name: String)

@Serializable
data class ClaimResponse(val active_device: String, val session_id: String)

@Serializable
data class ReleaseResponse(val released: Boolean)

@Serializable
data class CommandRequest(val text: String, val device_id: String? = null, val session_id: String? = null)

@Serializable
data class CommandResponse(val response: String)

/** `decision`: "approve" | "deny" - diventano "si'"/"no" sul PC (`core/companion_server.py::_handle_approval`),
 * la STESSA pipeline di conferma di ogni altro turno, mai un secondo motore di esecuzione lato mobile. */
@Serializable
data class ApprovalRequest(val decision: String, val session_id: String? = null)

@Serializable
data class ApprovalResponse(val response: String)

@Serializable
data class DeviceInfoDto(
    val device_id: String,
    val name: String,
    val status: String,
    val created_at: Double,
    val last_seen_at: Double? = null,
)

@Serializable
data class DevicesResponse(val devices: List<DeviceInfoDto>)

@Serializable
data class RevokeResponse(val revoked: Boolean)

@Serializable
data class StatusResponse(
    val ok: Boolean,
    val version: String,
    val protocol_version: Int,
    val min_protocol_version: Int,
    val encrypted: Boolean,
    val active_device: String? = null,
    val devices: List<JsonElement> = emptyList(),
)

/** Corpo di un rifiuto (F7.1: `_reject`/`_reject_body`) - `error` e' sempre presente, `retry_after` solo per un
 * 429 (rate limit). */
@Serializable
data class ApiErrorBody(val error: String, val retry_after: Double? = null)
