package com.jake.companion.storage

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** L'UNICA credenziale che questo telefono tiene (F7.2: "memorizzazione sicura del device_id e del token"):
 * lo stesso device_id/token che il Companion Server (F1.4.6, `core/device_credential_store.py`) ha emesso al
 * pairing. `tlsFingerprint` e' l'impronta confermata dall'utente durante IL pairing (trust-on-first-use,
 * `core/companion_tls.py`) - fissata una volta, mai riletta dal server dopo. */
data class PairedDevice(
    val deviceId: String,
    val token: String,
    val host: String,
    val port: Int,
    val useTls: Boolean,
    val tlsFingerprint: String?,
    val name: String,
)

/**
 * Storage sicuro per [PairedDevice] (F7.2), protetto da Android Keystore tramite `EncryptedSharedPreferences` -
 * mai in chiaro su disco. Stesso principio gia' applicato lato PC per admin_passphrase/i token per-dispositivo
 * (DPAPI, `core/secrets_vault.py`), con l'equivalente nativo della piattaforma: nessun sistema di cifratura
 * scritto a mano.
 */
class SecureDeviceStore(context: Context) {
    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context,
        PREFS_FILE_NAME,
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun save(device: PairedDevice) {
        prefs.edit()
            .putString(KEY_DEVICE_ID, device.deviceId)
            .putString(KEY_TOKEN, device.token)
            .putString(KEY_HOST, device.host)
            .putInt(KEY_PORT, device.port)
            .putBoolean(KEY_USE_TLS, device.useTls)
            .putString(KEY_FINGERPRINT, device.tlsFingerprint)
            .putString(KEY_NAME, device.name)
            .apply()
    }

    fun load(): PairedDevice? {
        val deviceId = prefs.getString(KEY_DEVICE_ID, null) ?: return null
        val token = prefs.getString(KEY_TOKEN, null) ?: return null
        val host = prefs.getString(KEY_HOST, null) ?: return null
        val port = prefs.getInt(KEY_PORT, -1)
        if (port <= 0) return null
        return PairedDevice(
            deviceId = deviceId,
            token = token,
            host = host,
            port = port,
            useTls = prefs.getBoolean(KEY_USE_TLS, false),
            tlsFingerprint = prefs.getString(KEY_FINGERPRINT, null),
            name = prefs.getString(KEY_NAME, "") ?: "",
        )
    }

    /** "Scollegare" lato app (F7.2): cancella la credenziale locale. Non revoca da sola il dispositivo sul PC -
     * quello e' `CompanionApiClient.revoke()`, un'azione DISTINTA che [com.jake.companion.repository.
     * JakeRepository.revokeThisDevice] chiama prima di arrivare qui. */
    fun clear() {
        prefs.edit().clear().apply()
    }

    private companion object {
        const val PREFS_FILE_NAME = "jake_companion_secure_prefs"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_TOKEN = "token"
        const val KEY_HOST = "host"
        const val KEY_PORT = "port"
        const val KEY_USE_TLS = "use_tls"
        const val KEY_FINGERPRINT = "tls_fingerprint"
        const val KEY_NAME = "name"
    }
}
