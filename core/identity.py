"""Identita' dell'account Windows che esegue Jake (F1.4.2, prima fetta - "distinguere identita'
Windows, profilo Jake, dispositivo e speaker profile"). Deliberatamente SOLO questa fetta:
"profilo Jake" non e' ancora un concetto definito da nessuna parte nel progetto, e "speaker
profile" richiederebbe un'infrastruttura di riconoscimento vocale/voiceprint che non esiste
ancora (e che F1.4.7 avverte comunque di non usare mai come unico fattore) - entrambi restano
lavoro futuro dichiarato, non inventati qui senza una richiesta di prodotto dietro.

Utile solo quando piu' account Windows condividono la stessa installazione di Jake (una macchina
di famiglia, un PC condiviso in ufficio): distingue "chi ha eseguito Jake" (l'account Windows) da
"quale dispositivo companion ha inviato la richiesta" (F1.2.3, gia' esistente via
core/request_context.py::current_device_id) - due dimensioni ORTOGONALI, non un sostituto l'una
dell'altra: lo stesso account Windows puo' avere piu' dispositivi companion, e lo stesso
dispositivo companion non implica un solo account Windows nel tempo (un PC condiviso, account
diversi che avviano la stessa app companion)."""
import getpass


def current_windows_user() -> str:
    """Nome dell'account Windows che esegue il processo Jake - costante per tutta la vita del
    processo (a differenza di current_device_id, che varia per richiesta e vive in un
    contextvar): un solo valore per l'intero processo, niente da propagare per thread.
    getpass.getuser() invece di os.getlogin(): quest'ultimo puo' sollevare OSError quando non
    c'e' un terminale di controllo (un servizio, una sessione senza login interattivo) -
    getpass.getuser() ripiega sulle variabili d'ambiente (LOGNAME/USER/LNAME/USERNAME) prima di
    fallire, piu' robusto per un processo che gira in background."""
    return getpass.getuser()
