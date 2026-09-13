import json
import os
from pathlib import Path

from core.logger import get_logger
from core.secrets_vault import SecretsVault, is_protected

# Chiavi cifrate a riposo con DPAPI (F1, vedi core/secrets_vault.py) invece di restare in chiaro
# su config/settings.json: la passphrase admin e il token di un hub Home Assistant sono le uniche
# due credenziali vere che Config gestisce oggi. Aggiungere qui una nuova chiave la protegge
# automaticamente, sia in lettura (get) sia in scrittura (set) sia alla migrazione di un valore
# gia' salvato in chiaro da una versione precedente (_migrate_secrets).
SECRET_KEYS = {"admin_passphrase", "home_assistant_token", "companion_token"}


class Config:
    """Carica configurazione e credenziali di Jake da config/settings.json.

    Le variabili d'ambiente JAKE_<CHIAVE> hanno sempre la precedenza, cosi' le credenziali
    possono restare fuori dal file su disco quando serve (es. macchine condivise, CI): restano
    intenzionalmente in chiaro su quel canale, che e' gia' il modo per non scriverle affatto su
    disco - DPAPI protegge invece SECRET_KEYS quando finiscono comunque nel file."""

    DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else self.DEFAULT_PATH
        self._logger = get_logger()
        # F1.4.1: la protezione DPAPI e' ora consolidata in una classe vera (core/secrets_vault.py
        # ::SecretsVault), non piu' funzioni libere senza stato.
        self._vault = SecretsVault()
        self._values = self._load()
        self._migrate_secrets()

    def _load(self) -> dict:
        if self.path.is_file():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _migrate_secrets(self) -> None:
        """Cifra sul posto qualunque valore SECRET_KEYS trovato ancora in chiaro (F1: "migrazione
        sicura dei token gia' salvati") - una tantum, silenziosa, alla prima apertura dopo
        l'aggiornamento: chi aveva gia' admin_passphrase/home_assistant_token in chiaro da una
        versione precedente di Jake non deve fare nulla, e il file su disco smette di contenerli
        in chiaro dal prossimo avvio.

        F1.4.1 ("...con versione e migrazione atomica"): ricifra sul posto anche un valore GIA'
        protetto ma nel formato DPAPI legacy (senza tag di versione esplicito, da prima di questa
        correzione) - non solo uno ancora in chiaro. Un valore che non si riesce a decifrare
        (`needs_migration()` vero ma `unprotect()` restituisce None: vault corrotto o profilo
        Windows diverso) non viene MAI riscritto - stesso principio gia' verificato per un
        segreto gia' corrotto, il file su disco resta quello che era finche' non si riesce a
        leggerlo per davvero."""
        changed = False
        for key in SECRET_KEYS:
            value = self._values.get(key)
            if not value:
                continue
            if not is_protected(value):
                self._values[key] = self._vault.protect(value)
                changed = True
            elif self._vault.needs_migration(value):
                plaintext = self._vault.unprotect(value)
                if plaintext is not None:
                    self._values[key] = self._vault.protect(plaintext)
                    changed = True
        if changed:
            self._write()

    def get(self, key: str, default=None):
        env_value = os.environ.get(f"JAKE_{key.upper()}")
        if env_value:
            return env_value
        # Solo l'assenza della chiave deve far scattare il default: un valore memorizzato ma
        # "falsy" (False, 0, "", []) e' comunque una scelta esplicita dell'utente e va rispettata,
        # non silenziosamente scartata.
        value = self._values.get(key, default)
        if key in SECRET_KEYS and isinstance(value, str):
            # F1.4.8: unprotect() restituisce None quando il valore ERA protetto ma non e' piu'
            # decifrabile (vault corrotto, o cifrato su un profilo Windows/una macchina diversa -
            # vedi core/secrets_vault.py) - trattato qui come "segreto mai impostato" (torna
            # default, di solito None), non come un crash che si propagherebbe fino
            # all'avvio di JakeCore. Un avviso esplicito, non un fallimento silenzioso: l'utente
            # deve capire perche' la sua passphrase/il suo token ha smesso di funzionare, invece
            # di scoprire "stranamente" che l'autenticazione non e' piu' attiva.
            was_protected = is_protected(value)
            value = self._vault.unprotect(value)
            if value is None and was_protected:
                self._logger.warning(
                    "Impossibile decifrare '%s' da %s (vault corrotto o profilo Windows diverso "
                    "da quello che lo ha cifrato): trattato come mai impostato, va reinserito.",
                    key, self.path,
                )
                return default
        return value

    def set(self, key: str, value) -> None:
        """Aggiorna un valore e lo persiste su config/settings.json (v2.0: modelli
        intercambiabili, tra gli usi). Le variabili d'ambiente restano prioritarie in get()."""
        if key in SECRET_KEYS and value:
            value = self._vault.protect(value)
        self._values[key] = value
        self._write()

    def _write(self) -> None:
        # F1.4.1 ("consolidare... con... migrazione atomica"): buco reale, riprodotto prima del
        # fix - write_text() apre il file in scrittura (troncandolo) e scrive l'intero JSON in
        # una sola chiamata; un arresto improvviso a meta' (kill, crash, mancanza di corrente,
        # lo stesso scenario gia' riprodotto per il ledger in F1.7.1) lascia settings.json
        # TRONCATO A META'. A differenza del ledger (append-only: si perde solo l'ultima riga),
        # qui _load() incontra un json.JSONDecodeError sul file intero e torna {} - **perdendo
        # OGNI valore**, non solo l'ultimo scritto: admin_passphrase, home_assistant_token, il
        # modello scelto, tutto. Riprodotto per davvero: un troncamento a meta' del file dopo tre
        # set() ha fatto sparire tutti e tre i valori al riavvio. Corretto scrivendo prima su un
        # file temporaneo nella STESSA directory (stesso filesystem, condizione richiesta perche'
        # os.replace() sia atomico) e poi rinominandolo sopra il file finale con os.replace(): o
        # il file vecchio completo resta intatto, o il nuovo file completo prende il suo posto -
        # mai uno stato a meta'. Non risolto qui, dichiarato: se il processo muore tra la scrittura
        # del temporaneo e os.replace(), il file .tmp resta orfano su disco (innocuo: il file
        # reale non e' mai stato toccato), non viene ripulito automaticamente.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(json.dumps(self._values, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, self.path)
