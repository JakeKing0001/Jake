# Jake Companion Mobile MVP (Android)

App Android che rende il telefono una **surface** di Jake: pairing con il PC, stato online/offline, chat via
`/command`, notifiche proattive dal task monitor (F6.7) con Approve/Deny che continuano lo stesso task nel
`JakeCore` sul PC. Riusa integralmente l'architettura F7.1/F7.2 gia' esistente lato server
(`core/companion_server.py`, `core/companion_guard.py`, `core/pairing_service.py`,
`core/device_credential_store.py`, `core/task_notification_bridge.py`, `core/companion_tls.py`) - **nessun
sistema parallelo**: nessuna copia lato mobile di `ConversationState`, `PolicyEngine`, `TaskAgent` o della
logica che decide se/quando interrompere l'utente (quella resta `NotificationPolicy`, F6.3, lato PC). Vedi il
docstring di [`JakeRepository.kt`](app/src/main/java/com/jake/companion/repository/JakeRepository.kt), il punto
in cui questo principio e' applicato concretamente.

## Limite dichiarato: questo codice non e' mai stato compilato qui

L'ambiente in cui questa app e' stata scritta **non ha una toolchain Android**: niente Gradle, niente compilatore
Kotlin, niente Android SDK (solo un JDK per uso generico). Il codice sotto `app/src/main/java` e' stato scritto
per intero e con attenzione, mirando esattamente ai contratti reali del server (percorsi HTTP, forma esatta dei
payload JSON, tipi di evento SSE), ma **non e' mai stato compilato, installato o eseguito** in questa sessione.
Non e' quindi corretto affermare che lo scenario end-to-end (`pair -> comando -> task -> decisione -> notifica ->
Approve -> stesso task continua sul PC`) sia stato *verificato* nel suo insieme: e' verificato lato server (vedi
sotto), ed e' scritto in modo coerente lato client, ma la prova finale richiede di aprire questo progetto in
Android Studio con un SDK reale e farlo girare contro un `JakeCore` in esecuzione.

Cosa e' invece verificato, e come:

- **Lato server**: ogni endpoint che questa app chiama (`/status`, `/pairing/start`, `/pairing/<id>`,
  `/devices/<id>/claim`, `/devices/<id>/release`, `/devices/<id>/revoke`, `/devices`, `/command`,
  `/approvals/<task_id>`, `/events` via SSE) e' coperto dalla suite Python esistente
  (`tests/test_companion_*.py`), eseguita per intero e verde prima di ogni commit di questo lavoro.
- **Lato client, la parte che NON dipende da Android** (protocollo sul filo, percorsi HTTP, riduzione dello
  stato UI, backoff di riconnessione, parsing del payload del QR) e' separata deliberatamente in codice Kotlin
  puro, senza import Android, cosi' da poter scrivere veri test JUnit eseguibili su una semplice JVM (vedi sotto
  "Cosa e' testato e come"). Non compilare questi test qui e' comunque un limite reale: sono scritti per compilare
  e passare su una JVM con Kotlin+JUnit, ma questa sessione non ha potuto invocarli.
- **Lato client, la parte che DIPENDE da Android** (storage cifrato via `EncryptedSharedPreferences`, client
  HTTP/SSE reale via OkHttp, scansione QR via ZXing, l'intera UI Compose) e' codice reale ma **non eseguibile
  senza un dispositivo/emulatore Android** - resta un gap dichiarato, non nascosto.

## Come costruirlo ed eseguirlo (in un ambiente con un vero Android Studio)

1. Apri la cartella `mobile/android/` come progetto in Android Studio (Koala o piu' recente consigliato).
2. Lascia che Android Studio generi il Gradle wrapper (`gradlew`/`gradlew.bat` e il jar del wrapper) se non
   presente: questo repo include solo `gradle/wrapper/gradle-wrapper.properties` (la versione dichiarata, Gradle
   8.7), non il jar binario del wrapper.
3. Compila: `./gradlew :app:assembleDebug` (o il pulsante Run in Android Studio, con un device/emulatore API 26+).
4. Esegui i test JVM puri: `./gradlew :app:testDebugUnitTest` - vedi sotto quali file coprono cosa.
5. Sul PC, avvia `JakeCore` con il Companion Server attivo e configurato per il pairing (vedi
   `core/companion_server.py`/`core/jake_core.py` per le chiavi di configurazione
   `companion_server_host`/`companion_server_tls_enabled`). Se il telefono non e' sullo stesso host del PC (il
   caso reale), la connessione **deve** essere TLS: il server genera da solo un certificato autofirmato
   persistente al primo avvio (`core/companion_tls.py::ensure_certificate`) e ne stampa/logga l'impronta
   SHA-256.
6. Nell'app, schermata di pairing: inserisci host/porta del PC (o scansiona un QR se un pannello lato PC lo
   mostra gia' - vedi il gap dichiarato sotto), conferma la richiesta con "si'" (o la passphrase, se configurata)
   sul PC, e **verifica a schermo che l'impronta TLS mostrata dall'app coincida con quella del server** prima di
   confermare: questo e' il punto in cui la fiducia viene stabilita (trust-on-first-use), non dopo.

## Modello di fiducia TLS (trust-on-first-use)

Il certificato del Companion Server e' autofirmato e locale: non esiste una CA che lo validi, per design (e' un
server casalingo, non un servizio pubblico). Il modello e' lo stesso della primissima connessione SSH a un host:

- **Durante il pairing** (prima che l'app abbia mai visto questo server), l'app accetta temporaneamente
  qualunque certificato per completare lo scambio (`JakeRepository.TrustOnFirstUseOnlyDuringPairing`) - l'unico
  scopo e' ottenere l'impronta SHA-256 del certificato da mostrare all'utente.
- L'utente confronta quell'impronta con quella mostrata/loggata dal PC **prima** di premere "si'" sul PC.
- Una volta che il pairing e' approvato, l'app fissa quell'impronta nello storage cifrato
  (`SecureDeviceStore`/`confirmPairedFingerprint`). **Ogni connessione successiva** (claim, comandi, eventi,
  dispositivi) usa `FingerprintTrustManager`, che rifiuta qualunque certificato la cui impronta SHA-256 non
  coincida esattamente con quella fissata - anche se il certificato del server dovesse cambiare in futuro, la
  connessione fallisce chiaramente invece di fidarsi silenziosamente di un host diverso.

## Gap dichiarato: nessun generatore di QR lato PC

Il pairing via QR previsto da questo incremento ha **solo il lato scansione** implementato
(`ui/pairing/QrPayload.kt` + il pulsante "Scansiona QR" in `PairingScreen.kt`, via ZXing). Il QR atteso contiene
solo `{"host","port","tls"}` (mai un token o una challenge - la challenge nasce dalla chiamata
`POST /pairing/start` che il telefono fa dopo aver letto il QR, non dal QR stesso). Non esiste ancora un
pannello lato PC che *generi e mostri* quel QR: fino a quando non verra' costruito, l'inserimento manuale di
host/porta nel modulo di pairing resta l'unico percorso completo. Questo e' uno scope deliberatamente ridotto
per questo MVP, non un bug.

## Cosa e' evitato in questo primo MVP (per scelta esplicita)

VoIP, wake word mobile, streaming audio, Home Hub, UI complessa, replica locale dell'LLM. Nessuno di questi e'
presente ne' abbozzato in questo codice.

## Cosa e' testato e come

Test JVM puri (JUnit 4, nessun framework Android, eseguibili con `./gradlew :app:testDebugUnitTest` in un
ambiente con toolchain reale - **non eseguiti in questa sessione**, per il limite descritto sopra):

- [`CompanionPathsTest.kt`](app/src/test/java/com/jake/companion/api/CompanionPathsTest.kt) - i percorsi HTTP
  costruiti dall'app coincidono esattamente con quelli che il server riconosce, incluso l'URL-encoding di un
  identificativo malformato.
- [`ReconnectPolicyTest.kt`](app/src/test/java/com/jake/companion/connection/ReconnectPolicyTest.kt) - backoff
  esponenziale, tetto massimo, jitter (con `random` iniettato per rendere il risultato deterministico), e
  validazione dei parametri di costruzione.
- [`HudEventParsingTest.kt`](app/src/test/java/com/jake/companion/protocol/HudEventParsingTest.kt) - decodifica
  di eventi SSE ed esempi reali di payload di notifica del task monitor, inclusi i casi limite (tipo di evento
  sconosciuto, campi opzionali assenti, campi extra da un server futuro) che NON devono mai far fallire il
  parsing.
- [`QrPayloadTest.kt`](app/src/test/java/com/jake/companion/ui/pairing/QrPayloadTest.kt) - un QR non e' mai un
  input fidato: testo non-JSON, JSON di uno schema diverso, porta fuori range, JSON troncato restituiscono tutti
  `null` invece di lanciare un'eccezione.
- [`UiStateReducerTest.kt`](app/src/test/java/com/jake/companion/repository/UiStateReducerTest.kt) - il test piu'
  importante di questo set: dimostra che `reduce()` (la funzione che trasforma un evento SSE nello stato della
  UI) **legge soltanto** cio' che il payload del PC ha gia' deciso (`DecisionDto`, gia' prodotta da
  `NotificationPolicy` lato server) - non calcola mai da sola urgenza, rischio o se interrompere l'utente. Copre
  anche il caso di due task in attesa di decisione contemporaneamente (chiudere il task A non deve mai cancellare
  la decisione ancora pendente del task B) e i payload malformati (ignorati, mai un crash).

Non testato con JUnit (dipende dal framework Android, richiede un device/emulatore reale):
`SecureDeviceStore`, `CompanionApiClient`/`EventStreamClient` (networking OkHttp reale), tutte le schermate
Compose, `MainActivity`/`JakeApplication`.
