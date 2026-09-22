package com.jake.companion.api

import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.X509TrustManager

/**
 * Fiducia trust-on-first-use per il certificato TLS autofirmato del Companion Server (F7.1.4/F7.2,
 * `core/companion_tls.py`): non esiste una CA verso cui convalidare una catena per un certificato locale
 * autofirmato, quindi non se ne prova una. Si convalida invece che l'impronta SHA-256 del certificato foglia
 * presentato ad OGNI connessione corrisponda ESATTAMENTE a quella che l'utente ha confermato durante il pairing
 * (mostrata li' accanto a quella stampata sul PC, `core/companion_tls.py::current_fingerprint`). Un'impronta
 * diversa (il PC e' stato reinstallato, o - lo scenario che conta - un server diverso si spaccia per quello
 * giusto) fa fallire la connessione: MAI un fallback silenzioso che si fida comunque.
 */
class FingerprintTrustManager(private val pinnedFingerprint: String) : X509TrustManager {

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("FingerprintTrustManager e' solo per la verifica del server")
    }

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull()
            ?: throw CertificateException("il server non ha presentato alcun certificato")
        val presented = fingerprintOf(leaf)
        if (!presented.equals(pinnedFingerprint, ignoreCase = true)) {
            throw CertificateException(
                "l'impronta del certificato e' cambiata (attesa $pinnedFingerprint, presentata $presented) - " +
                    "possibile sostituzione del server: la connessione NON e' fidata",
            )
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()

    companion object {
        /** Stesso formato di `core/companion_tls.py::fingerprint_sha256` (esadecimale a coppie maiuscole
         * separate da ':'), cosi' un'impronta letta dal PC e una calcolata qui si confrontano carattere per
         * carattere senza bisogno di normalizzare nulla. */
        fun fingerprintOf(certificate: X509Certificate): String {
            val digest = MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
            return digest.joinToString(":") { byte -> "%02X".format(byte) }
        }
    }
}
