package com.jake.companion.api

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

/** Un errore riportato dal Companion Server (F7.1: `_reject`/`_reject_body`, mai un'eccezione generica) - il
 * `errorCode` e' lo stesso identificatore stabile che il server usa (es. "no_matching_pending_decision",
 * "rate_limited"), cosi' la UI puo' mostrare un messaggio preciso invece di "qualcosa e' andato storto". */
class ApiException(val statusCode: Int, val errorCode: String, val retryAfterSeconds: Double? = null) :
    IOException("companion API error $statusCode: $errorCode")

/**
 * Client HTTP verso il Companion Server (F7.1/F7.2). Nessuna logica di dominio qui: solo richiesta/risposta sui
 * percorsi gia' esposti lato PC ([CompanionPaths]) - le decisioni (approvare un'azione, riconnettersi, cosa
 * mostrare) restano a `JakeRepository`/alla UI, mai a questo client.
 */
class CompanionApiClient(
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    private fun url(path: String): String = baseUrl.trimEnd('/') + path

    private fun Request.Builder.withAuth(token: String?): Request.Builder =
        if (token != null) header("Authorization", "Bearer $token") else this

    private fun <T> execute(request: Request, deserialize: (String) -> T): T {
        httpClient.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val error = runCatching { json.decodeFromString(ApiErrorBody.serializer(), bodyText) }.getOrNull()
                throw ApiException(response.code, error?.error ?: "unknown_error", error?.retry_after)
            }
            return deserialize(bodyText)
        }
    }

    private fun post(path: String, bodyJson: String, token: String? = null): Request =
        Request.Builder().url(url(path)).post(bodyJson.toRequestBody(JSON_MEDIA_TYPE)).withAuth(token).build()

    private fun get(path: String, token: String? = null): Request =
        Request.Builder().url(url(path)).get().withAuth(token).build()

    // ---- pairing (F7.1.2) --------------------------------------------------------------------------------------

    fun pairingStart(requestedName: String, syncPublicKey: SyncPublicKeyDto? = null): PairingStartResponse {
        val body = json.encodeToString(PairingStartRequest.serializer(), PairingStartRequest(requestedName, syncPublicKey))
        return execute(post(CompanionPaths.pairingStart(), body)) {
            json.decodeFromString(PairingStartResponse.serializer(), it)
        }
    }

    fun pairingPoll(challengeId: String): PairingPollResponse =
        execute(get(CompanionPaths.pairingPoll(challengeId))) {
            json.decodeFromString(PairingPollResponse.serializer(), it)
        }

    // ---- sessione (F1.2.3/F7.1) --------------------------------------------------------------------------------

    fun claim(deviceId: String, token: String, name: String): ClaimResponse {
        val body = json.encodeToString(ClaimRequest.serializer(), ClaimRequest(name))
        return execute(post(CompanionPaths.claim(deviceId), body, token)) {
            json.decodeFromString(ClaimResponse.serializer(), it)
        }
    }

    fun release(deviceId: String, token: String): ReleaseResponse =
        execute(post(CompanionPaths.release(deviceId), "{}", token)) {
            json.decodeFromString(ReleaseResponse.serializer(), it)
        }

    // ---- comandi e approvazioni (F7.1/F6.3/F6.7 -> F7.2) ---------------------------------------------------------

    fun sendCommand(token: String, text: String, deviceId: String?, sessionId: String?): CommandResponse {
        val body = json.encodeToString(CommandRequest.serializer(), CommandRequest(text, deviceId, sessionId))
        return execute(post(CompanionPaths.command(), body, token)) {
            json.decodeFromString(CommandResponse.serializer(), it)
        }
    }

    fun sendApproval(token: String, taskId: String, decision: String, sessionId: String?): ApprovalResponse {
        val body = json.encodeToString(ApprovalRequest.serializer(), ApprovalRequest(decision, sessionId))
        return execute(post(CompanionPaths.approvals(taskId), body, token)) {
            json.decodeFromString(ApprovalResponse.serializer(), it)
        }
    }

    // ---- dispositivi (F7.2.1) ---------------------------------------------------------------------------------

    fun listDevices(token: String): DevicesResponse =
        execute(get(CompanionPaths.devices(), token)) { json.decodeFromString(DevicesResponse.serializer(), it) }

    fun revoke(deviceId: String, token: String): RevokeResponse =
        execute(post(CompanionPaths.revoke(deviceId), "{}", token)) {
            json.decodeFromString(RevokeResponse.serializer(), it)
        }

    // ---- stato --------------------------------------------------------------------------------------------------

    fun status(token: String? = null): StatusResponse =
        execute(get(CompanionPaths.status(), token)) { json.decodeFromString(StatusResponse.serializer(), it) }
}
