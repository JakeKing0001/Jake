package com.jake.companion.ui.pairing

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.jake.companion.repository.JakeRepository
import com.jake.companion.repository.PairingOutcome
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.delay

/** Stato locale della SOLA schermata (F7.2: "evita... UI complessa" - niente ViewModel/DI, solo `remember`, come
 * un primo MVP che deve funzionare prima di essere elegante). */
private enum class PairingStep { FORM, WAITING, DONE }

/**
 * Pairing iniziale (F7.1.2/F7.2): host/porta inseriti a mano O letti da un QR (`{"host","port","tls"}` - vedi
 * `QrPayload.kt`; nessun generatore lato PC ancora, limite dichiarato in ROADMAP_EXECUTION.md). L'approvazione
 * VERA resta un "si'" sul PC: questa schermata apre solo la challenge e la interroga finche' non si risolve, non
 * decide nulla da sola.
 */
@Composable
fun PairingScreen(repository: JakeRepository, onPaired: () -> Unit) {
    var host by remember { mutableStateOf("192.168.1.") }
    var port by remember { mutableStateOf("8765") }
    var useTls by remember { mutableStateOf(true) }
    var requestedName by remember { mutableStateOf(android.os.Build.MODEL ?: "Telefono") }
    var step by remember { mutableStateOf(PairingStep.FORM) }
    var challengeId by remember { mutableStateOf<String?>(null) }
    var tlsFingerprint by remember { mutableStateOf<String?>(null) }
    var statusText by remember { mutableStateOf("") }

    val qrScanLauncher = rememberLauncherForActivityResult(ScanContract()) { result ->
        val scanned = result.contents ?: return@rememberLauncherForActivityResult
        val payload = parseQrPairingPayload(scanned)
        if (payload == null) {
            statusText = "QR non riconosciuto: non contiene un indirizzo Jake valido."
        } else {
            host = payload.host
            port = payload.port.toString()
            useTls = payload.tls
            statusText = "Indirizzo letto dal QR: ${payload.host}:${payload.port}"
        }
    }

    Column(modifier = Modifier.padding(24.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Collega questo telefono a Jake")
        Text("Scansiona il QR mostrato sul PC, oppure inserisci l'indirizzo a mano.")

        Button(onClick = {
            qrScanLauncher.launch(ScanOptions().setPrompt("Inquadra il QR mostrato sul PC").setBeepEnabled(false))
        }, modifier = Modifier.fillMaxWidth()) { Text("Scansiona QR") }

        TextField(value = host, onValueChange = { host = it }, label = { Text("Indirizzo PC (es. 192.168.1.50)") }, modifier = Modifier.fillMaxWidth())
        TextField(
            value = port, onValueChange = { port = it.filter(Char::isDigit) }, label = { Text("Porta") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth(),
        )
        Row {
            Checkbox(checked = useTls, onCheckedChange = { useTls = it })
            Text("Usa HTTPS/TLS (obbligatorio se il PC non e' 127.0.0.1)")
        }
        TextField(value = requestedName, onValueChange = { requestedName = it }, label = { Text("Nome di questo dispositivo") }, modifier = Modifier.fillMaxWidth())

        when (step) {
            PairingStep.FORM -> Button(
                onClick = {
                    val portNumber = port.toIntOrNull()
                    if (host.isBlank() || portNumber == null) {
                        statusText = "Indirizzo o porta non validi."
                        return@Button
                    }
                    step = PairingStep.WAITING
                    statusText = "Richiesta di pairing inviata..."
                    repository.startPairing(
                        host = host, port = portNumber, useTls = useTls, requestedName = requestedName,
                        onChallenge = { id, fingerprint ->
                            challengeId = id
                            tlsFingerprint = fingerprint
                            statusText = "In attesa di conferma sul PC..."
                        },
                        onError = { error ->
                            step = PairingStep.FORM
                            statusText = "Errore: ${error.message ?: error::class.simpleName}"
                        },
                    )
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Inizia il pairing") }

            PairingStep.WAITING -> {
                CircularProgressIndicator()
                Text(statusText)
                tlsFingerprint?.let { fingerprint ->
                    Text("Impronta del certificato del PC (deve corrispondere a quella mostrata li'):")
                    Text(fingerprint)
                }
                Text("Sul PC: di' o scrivi \"si'\" quando Jake chiede di autorizzare '$requestedName'.")
                val currentChallengeId = challengeId
                val portNumber = port.toIntOrNull()
                if (currentChallengeId != null && portNumber != null) {
                    LaunchedEffect(currentChallengeId) {
                        while (step == PairingStep.WAITING) {
                            delay(2_000)
                            repository.pollPairing(host, portNumber, useTls, currentChallengeId, requestedName) { outcome ->
                                when (outcome) {
                                    is PairingOutcome.Approved -> {
                                        repository.confirmPairedFingerprint(tlsFingerprint)
                                        step = PairingStep.DONE
                                    }
                                    is PairingOutcome.Rejected -> {
                                        step = PairingStep.FORM
                                        statusText = "Il pairing e' stato rifiutato sul PC."
                                    }
                                    is PairingOutcome.Expired -> {
                                        step = PairingStep.FORM
                                        statusText = "La richiesta di pairing e' scaduta (5 minuti): riprova."
                                    }
                                    is PairingOutcome.Pending -> Unit
                                    is PairingOutcome.Failed -> {
                                        statusText = "Errore durante il controllo: ${outcome.error.message}"
                                    }
                                }
                            }
                        }
                    }
                }
                TextButton(onClick = { step = PairingStep.FORM; statusText = "" }) { Text("Annulla") }
            }

            PairingStep.DONE -> {
                Text("Pairing completato!")
                LaunchedEffect(Unit) { onPaired() }
            }
        }
    }
}
