"""Controller unificato per le azioni sullo schermo (v3.7, Computer Use Engine): osserva
(OCR), localizza (trova il testo/elemento), agisce (click) e verifica (e' cambiato qualcosa?)
come UNA pipeline coerente in un solo posto, invece della stessa logica sparsa e duplicata tra
skill isolate (CLICK_TEXT, CLICK_ELEMENT) che agiscono ognuna per conto proprio.

Il "recupero" (ritentare, cambiare strategia) resta deliberatamente una decisione dell'agente a
passi (core/agent.py, fase 3.3) che vede l'osservazione e ragiona sul da farsi, non un
automatismo qui dentro: ricliccare alla cieca quando lo schermo non sembra cambiato rischierebbe
di attivare due volte un'azione che in realta' era gia' andata a buon fine (es. l'invio di un
modulo, l'attivazione di una casella di spunta), il che sarebbe peggio di riportare
onestamente "non verificato" e lasciare che sia l'agente a decidere il prossimo passo.

A coordinate pixel assolute copre click (`click_point`/`click_text`) e, da F3.4.2, digitazione
semantica per nome (`type_into_element`, vedi sotto) - scorrimento/tasti/drag/drop restano skill
isolate (PRESS_KEY, SCROLL) e sono un possibile prossimo passo di questa fase.

`click_element` (F3.4.2, prima fetta - "unificare click... nel ComputerAgent", adozione): trova
un elemento per nome/ruolo/automation_id DENTRO una finestra data (F3.3, `SelectorEngine`) e lo
clicca semanticamente tramite il pattern Invoke di UI Automation (F3.4, `ActionExecutor`) invece
di coordinate pixel ASSOLUTE fornite dal chiamante - le coordinate restano un dettaglio interno,
LETTE da UI Automation, non indovinate ne' passate dall'esterno come in `click_point`. Se Invoke
non e' disponibile o non produce un effetto visibile, ripiega su un click pixel alle STESSE
coordinate lette da UI Automation (F3.5, `try_strategies_in_order`) - non un secondo metodo
separato, la stessa scala di ripiego gia' costruita e testata in questa sessione. Nessun campo
`ElementSelector`/pattern e' passato dal chiamante oltre nome/ruolo/automation_id: un primo
gradino deliberatamente per il caso piu' comune (bottoni/link, dove Invoke e' gia' verificato
affidabile in F3.4) - elementi dove Invoke NON si applica (es. una voce di lista che va
selezionata, non "premuta") restano fuori da questo metodo, non affrontati qui.

**Assunzione dichiarata esplicitamente, NON verificata empiricamente per Invoke** (a differenza
della scoperta gia' fatta per SelectionItem, vedi `core/computer_use/fallback.py`): incatenare un
tentativo Invoke fallito prima di un click pixel sullo STESSO elemento potrebbe in teoria
"avvelenare" lo stato allo stesso modo gia' trovato per SelectionItem su un `QListWidgetItem` -
non ancora messo alla prova con un test dedicato per Invoke specificamente, quindi la strategia
Invoke qui NON e' marcata `unsafe_after_failure` (il comportamento di default, incatenare) invece
di assumere il limite peggiore senza prova. Se un futuro test dovesse trovare lo stesso
avvelenamento anche per Invoke, questa scelta andrebbe rivista.

La VERIFICA resta la stessa evidenza DEBOLE gia' dichiarata in F3.5.5 (pixel diff, `evidence=
EVIDENCE_PIXEL_DIFF`) - questo metodo non conosce il dominio dell'app target, a differenza dei
test end-to-end di F3.1-F3.5 (ognuno con un secondo segnale indipendente specifico del caso, es.
il bottone "Rimuovi selezionato" che si abilita). I metodi esistenti (`click_text`/`click_point`/
`locate_text`/`observe`) restano INVARIATI - `click_element` e' additivo, non ancora usato da
nessuna skill esistente (CLICK_TEXT/CLICK_ELEMENT restano sul vecchio percorso a coordinate pixel/
OCR - collegarli e' una decisione di adozione a parte, non affrontata qui).

`type_into_element` (F3.4.2, resto): stessa identica struttura di `click_element` (fattorizzata
in `_locate_element_center`, condivisa da entrambi) ma con il pattern Value invece di Invoke -
scrive `text` in un campo tramite `SetValue` (F3.4), non digitazione tasto per tasto simulata, con
ripiego a click + `pyautogui.write` reale se Value fallisce. `text` NON compare MAI in
`ComputerActionResult` - lo stesso principio gia' seguito da `ElementActionReceipt.set_value`
(F3.4.5): mai rischiare che una password o un dato sensibile finisca in una struttura che un
futuro chiamante potrebbe loggare.

`evidence` (F3.5.5, "usare pixel diff soltanto come evidenza debole", adozione): `verified=True`
qui viene SEMPRE da un pixel diff (`core/vision/screen_diff.py`), l'UNICO segnale disponibile a
questa classe - a differenza della verifica basata su UI Automation costruita in `core/
computer_use/` (F3.2-F3.5) contro la fixture (es. leggere se il bottone "Rimuovi selezionato" e'
davvero abilitato), un pixel diff non legge NULLA dello stato reale dell'applicazione, solo se i
pixel sullo schermo sono cambiati. E' un'evidenza DEBOLE in entrambe le direzioni: ne' necessaria
(un click puo' avere un effetto reale senza alcun cambiamento visibile, es. un link verso una
pagina gia' aperta - gia' gestito da questa classe, che riporta onestamente `verified=False` senza
far fallire il click) ne' sufficiente (un cursore che lampeggia, un orologio che avanza, una
qualunque animazione indipendente dal click potrebbero far cambiare i pixel senza che il click
abbia avuto l'effetto voluto - un falso positivo che questa classe non puo' distinguere da un vero
successo). Il campo rende esplicita la FONTE della verifica invece di lasciare che un futuro
chiamante legga `verified=True` come se fosse equivalente a una verifica basata su stato reale
dell'app - non lo e' mai, in questa classe.

**Buco reale trovato verificando `type_into_element` contro la fixture, non ipotizzato - una
conseguenza pratica CONCRETA della debolezza gia' dichiarata sopra**: `SetValue` (F3.4) puo'
riuscire per davvero (il campo cambia sul serio, verificato leggendo `CurrentValue` via UI
Automation, non assunto) mentre l'evidenza debole del pixel diff - calcolata sull'INTERO schermo,
non sul campo - non rileva un cambiamento cosi' piccolo e fa scattare comunque il ripiego pixel.
Riprodotto per davvero: un `wait_for_unique_element`/`click_element` su un nome INESISTENTE
(nessuna azione, solo una ricerca fallita) eseguito PRIMA di un `type_into_element` altrimenti
riuscito bastava a far scattare questo esatto scenario in modo deterministico (3/3), non un caso
isolato. Senza selezionare tutto il contenuto del campo PRIMA di scrivere, `pyautogui.write`
si limita ad AGGIUNGERE il testo a quello gia' impostato da `SetValue`, duplicandolo
(`"testo"` -> `"testotesto"`) invece di sostituirlo - corretto scrivendo Ctrl+A prima del testo
nel ripiego pixel di `type_into_element`, cosi' il ripiego resta SICURO da incatenare anche
quando la strategia precedente e' gia' riuscita silenziosamente, non solo quando e' davvero
fallita (lo stesso principio di sicurezza gia' dichiarato per `unsafe_after_failure`, F3.5.6, ma
qui risolto rendendo il ripiego stesso idempotente invece di doverlo evitare).

`idempotency_key` (F3.4.6, "evitare doppia esecuzione sui retry"): un parametro opzionale in piu'
per `click_element`/`type_into_element`, una protezione DIVERSA e complementare da quella sopra -
quella evita che il RIPIEGO INTERNO alla stessa chiamata duplichi l'effetto di una strategia gia'
riuscita; questa evita che una SECONDA CHIAMATA dall'esterno (un retry del chiamante, es. l'agente
a passi di F3.3 che rilancia lo stesso passo dopo un pixel diff debole/`verified=False` pur essendo
l'azione gia' riuscita davvero) ripeta l'azione una seconda volta. Implementata come una cache
per-ISTANZA (mai di modulo - vedi `ComputerAgent.__init__`) con scadenza esplicita (30s di
default, iniettabile) che memorizza SOLO i risultati riusciti - `core/action_ledger.py::
idempotency_key_of` esiste gia' per la stessa famiglia di chiave ma dichiara esplicitamente di
NON applicare ancora un'enforcement del genere, lasciandola come "una decisione di policy che
merita una revisione dedicata"; questa e' quella revisione, applicata pero' al livello PIU' BASSO
e piu' sicuro per farlo davvero (un'azione fisica sullo schermo), non ancora collegata alla chiave
dell'intent-level ledger - un chiamante che vuole questa protezione deve passare esplicitamente
`idempotency_key` (nessuna skill esistente e' toccata, il default resta `None`, comportamento
identico a prima di F3.4.6).

`risk_intent`/`policy_parameters`/`automated` (F3.4.3, "richiedere policy prima di upload,
submit, send, delete e purchase") - una decisione di design presa DELIBERATAMENTE, non l'unica
possibile: `ComputerAgent` non puo' sapere da SOLO se cliccare un bottone chiamato "Elimina" e'
un'azione distruttiva o innocua (un'euristica sul testo del bottone sarebbe fragile, dipendente
dalla lingua, con falsi positivi/negativi reali) - il CHIAMANTE (una skill, una procedura di F3.8,
un agente a passi) deve dichiarare esplicitamente il rischio passando `risk_intent` (lo stesso
nome di intent gia' censito in `core/risk.py`/`core/policy_engine.py`, es. "DELETE_PATH"), non
`ComputerAgent` che indovina dal contesto UI. `self.policy_engine` (`ComputerAgent.__init__`,
opzionale, `None` di default - IDENTICO comportamento a prima di F3.4.3 per ogni chiamante
esistente) e' lo stesso `PolicyEngine` gia' usato da `JakeCore`/`PlanExecutor` (F1), mai una
seconda istanza/un motore diverso - stesso pattern di dependency injection gia' usato da
`RunWorkflowSkill`. `automated` sceglie esplicitamente tra `decide_interactive`/`decide_automated`
(la STESSA scelta che `JakeCore`/`PlanExecutor` fanno gia' in base al proprio contesto, mai
inferita qui) - un `automated=True` spoglia SEMPRE i segnali di autorizzazione prima di decidere
(`strip_authorization_signals`), lo stesso passo gia' richiesto da `decide_automated` per evitare
esattamente il bug reale numero 1 gia' documentato nel modulo docstring di `core/policy_engine.py`
("un piano automatico poteva auto-autorizzarsi"). `ComputerAgent` non implementa (e non deve: non
ha un turno conversazionale a cui appartenere) il ciclo "chiedi conferma ora, riprova al turno
successivo" di `JakeCore._authorize_command` - un `CONFIRMATION_REQUIRED`/`AUTH_REQUIRED` si
comporta come un fallimento di ricerca (nessuna azione fisica tentata), lasciando al CHIAMANTE (che
ha un ciclo conversazionale, `ComputerAgent` non ce l'ha) il compito di chiedere e ririchiamare con
`policy_parameters={"confirmed": True, ...}` - la stessa busta di conferma che `JakeCore` gia'
costruisce oggi per ogni altro intent, non un formato nuovo inventato per questo modulo."""
import time
from dataclasses import dataclass, replace

from core.turn_cancellation import raise_if_cancelled

POST_ACTION_SETTLE_SECONDS = 0.4

# F3.4.6 ("evitare doppia esecuzione sui retry"): finestra entro cui un `idempotency_key` ripetuto
# restituisce il risultato GIA' ottenuto invece di rieseguire l'azione - vedi il docstring di
# `ComputerAgent.__init__` per la scelta del valore e i limiti dichiarati. 30s copre un retry
# immediato dello stesso passo (es. l'agente a passi di F3.3 che rivede un pixel diff debole e
# rilancia lo stesso passo), non una richiesta successiva scorrelata dell'utente ore dopo.
IDEMPOTENCY_TTL_SECONDS = 30.0

# F3.5.5: vocabolario chiuso per ComputerActionResult.evidence - vedi il docstring del modulo.
EVIDENCE_PIXEL_DIFF = "pixel_diff"
EVIDENCE_NONE = "none"
# Prova FORTE: la proprieta' dell'elemento riletta via UI Automation dopo l'azione (es. il testo
# del campo e' davvero quello scritto). A differenza del pixel diff dimostra l'effetto.
EVIDENCE_UIA_PROPERTY = "uia_property"


@dataclass
class ComputerActionResult:
    success: bool
    x: int | None = None
    y: int | None = None
    matched: str | None = None
    verified: bool = False
    change_ratio: float = 0.0
    error: str | None = None
    # F3.5.5: EVIDENCE_NONE quando nessun controllo e' stato possibile (es. la cattura schermo
    # iniziale e' fallita) - mai EVIDENCE_PIXEL_DIFF per un controllo che non e' davvero avvenuto.
    evidence: str = EVIDENCE_NONE
    # F3.5.2: quale strategia ha prodotto l'effetto e, per ogni tentativo, esito e motivo -
    # la diagnosi resta visibile anche quando l'azione e' riuscita solo in parte.
    strategy: str | None = None
    attempts: tuple = ()


def _describe_attempts(outcome) -> tuple:
    """(strategia, riuscita, motivo) per ogni tentativo della scala: la diagnosi del fallimento."""
    return tuple((a.strategy_name, a.succeeded, a.reason) for a in outcome.attempts)


def _last_performed_strategy(outcome) -> str | None:
    """La strategia verificata, altrimenti l'ultima tentata (quella il cui effetto non e' stato
    confermato): mai None se qualcosa e' stato eseguito."""
    return outcome.successful_strategy or (outcome.attempts[-1].strategy_name if outcome.attempts else None)


class ComputerAgent:
    def __init__(self, idempotency_ttl_seconds: float = IDEMPOTENCY_TTL_SECONDS, policy_engine=None) -> None:
        """F3.4.6: `idempotency_ttl_seconds` iniettabile (non solo la costante di modulo) per lo
        stesso motivo per cui `timeout_seconds` e' gia' un parametro esplicito ovunque in questo
        progetto - un test deve poter usare una finestra brevissima per osservare una vera
        scadenza senza un `time.sleep()` reale di 30s. La cache stessa (`_idempotency_cache`) vive
        sull'ISTANZA, mai a livello di modulo: un'istanza di `ComputerAgent` e' gia' il ciclo di
        vita giusto per questa protezione (una skill la crea una volta e la riusa tra le proprie
        `execute()`, vedi `skills/screen_click.py`) - una cache di modulo condivisa tra istanze/
        skill diverse rischierebbe di far combaciare chiavi scelte da parti del sistema che non si
        conoscono tra loro.

        `policy_engine` (F3.4.3, "richiedere policy prima di upload, submit, send, delete e
        purchase"): opzionale, `None` di default - IDENTICO comportamento a prima di F3.4.3 per
        ogni chiamante esistente che costruisce `ComputerAgent()` senza argomenti (`skills/
        screen_click.py`, l'intera suite di test gia' scritta). Stesso pattern di dependency
        injection gia' usato da `RunWorkflowSkill` (`core/jake_core.py`/`skills/workflow.py`) -
        un'istanza costruita altrove (tipicamente `JakeCore.policy_engine`) assegnata post-
        costruzione o passata qui, mai un singleton di modulo (che questo progetto non usa da
        nessuna parte per `PolicyEngine`, verificato non assunto)."""
        self._idempotency_ttl_seconds = idempotency_ttl_seconds
        self._idempotency_cache: dict[str, tuple[float, ComputerActionResult]] = {}
        self.policy_engine = policy_engine

    def _check_policy(
        self, risk_intent: str | None, policy_parameters: dict | None, automated: bool,
    ) -> ComputerActionResult | None:
        """F3.4.3: `None` (procedi, non c'e' nulla da bloccare) quando `risk_intent` non e' dato
        (il chiamante non ha dichiarato alcun rischio - comportamento INVARIATO, coerente con
        "opt-in" gia' seguito da ogni capability di `PolicyEngine`, es. `allowed_filesystem_roots`
        vuoto di default) o quando `self.policy_engine` e' `None` (nessun motore collegato - un
        `ComputerAgent()` senza `policy_engine` non puo' verificare nulla, non deve bloccare tutto
        per un pre-requisito mancante). Altrimenti delega a `PolicyEngine.decide_interactive`/
        `decide_automated` - la STESSA identica coppia gia' usata da `JakeCore`/`PlanExecutor`
        (mai una terza via inventata qui), scelta esplicitamente da `automated` invece di
        indovinata: `ComputerAgent` non sa da solo se sta girando dentro un turno interattivo o
        un'automazione in background, la STESSA ambiguita' che il codice esistente risolve
        lasciando che sia CHI CHIAMA a dichiararlo (`JakeCore` usa sempre `decide_interactive`,
        `PlanExecutor` sempre `decide_automated` - nessuno dei due lo inferisce).

        **`automated=True` spoglia sempre i segnali di autorizzazione PRIMA di decidere**
        (`strip_authorization_signals`, lo stesso identico passo gia' richiesto da
        `decide_automated` - vedi il suo docstring): un `automated=False` di default che
        rispettasse `confirmed`/`authenticated` senza che nessuno li abbia genuinamente concessi
        sarebbe esattamente il bug reale numero 1 gia' documentato nel modulo docstring di
        `core/policy_engine.py` ("PlanExecutor.execute() non toglieva mai confirmed/authenticated
        dai parametri di un passo: un piano automatico poteva auto-autorizzarsi")."""
        if risk_intent is None or self.policy_engine is None:
            return None
        from core.policy_engine import PolicyDecision, strip_authorization_signals

        parameters = policy_parameters or {}
        if automated:
            decision = self.policy_engine.decide_automated(risk_intent, strip_authorization_signals(parameters))
        else:
            decision = self.policy_engine.decide_interactive(risk_intent, parameters)
        if decision == PolicyDecision.ALLOW:
            return None
        if decision == PolicyDecision.BLOCK:
            return ComputerActionResult(success=False, error="POLICY_BLOCKED")
        if decision == PolicyDecision.CONFIRM:
            return ComputerActionResult(success=False, error="CONFIRMATION_REQUIRED")
        if decision == PolicyDecision.REQUIRE_AUTH:
            return ComputerActionResult(success=False, error="AUTH_REQUIRED")
        raise AssertionError(f"PolicyDecision sconosciuta: {decision!r}")  # difesa, non dovrebbe mai accadere

    def _cached_action_result(self, idempotency_key: str | None) -> ComputerActionResult | None:
        """F3.4.6: `None` (mai sollevare, mai inventare un risultato) sia quando `idempotency_key`
        non e' dato (il chiamante non ha chiesto questa protezione) sia quando la voce in cache e'
        scaduta - una voce scaduta viene anche RIMOSSA qui (non lasciata a crescere in eterno:
        `core/action_ledger.py::idempotency_key_of` dichiara esplicitamente che una cache di
        questo genere ha bisogno di una scadenza dichiarata, non di crescita illimitata come il
        ledger append-only, che e' un registro di controllo con un problema diverso). Una copia
        indipendente (`dataclasses.replace`, campi tutti primitivi) del risultato salvato, mai lo
        stesso oggetto: un chiamante che mutasse il risultato restituito non deve poter corrompere
        la voce in cache per una chiamata futura."""
        if idempotency_key is None:
            return None
        entry = self._idempotency_cache.get(idempotency_key)
        if entry is None:
            return None
        cached_at, result = entry
        if time.monotonic() - cached_at > self._idempotency_ttl_seconds:
            del self._idempotency_cache[idempotency_key]
            return None
        return replace(result)

    def _remember_action_result(self, idempotency_key: str | None, result: ComputerActionResult) -> None:
        """F3.4.6: memorizza SOLO un risultato riuscito (`success=True`) - un'azione FALLITA deve
        restare ritentabile normalmente (il punto dei retry), bloccarla dietro la stessa chiave
        trasformerebbe una protezione contro la doppia esecuzione in un modo accidentale di
        impedire per sempre un secondo tentativo legittimo dopo un fallimento transitorio."""
        if idempotency_key is None or not result.success:
            return
        self._idempotency_cache[idempotency_key] = (time.monotonic(), replace(result))

    def observe(self) -> list[dict] | None:
        """OCR dello schermo attuale: ogni parola visibile con il suo rettangolo in pixel."""
        from core.vision.screen import read_screen_words

        return read_screen_words()

    def locate_text(self, text: str, words: list[dict] | None = None) -> dict | None:
        """Trova 'text' tra le parole osservate (le rilegge da sola se non gia' fornite)."""
        from skills.screen_click import find_text_on_screen

        if words is None:
            words = self.observe()
        if not words:
            return None
        return find_text_on_screen(text, words)

    def click_text(self, text: str, button: str = "left") -> ComputerActionResult:
        hit = self.locate_text(text)
        if hit is None:
            return ComputerActionResult(success=False, error="NOT_FOUND")
        return self.click_point(hit["x"], hit["y"], button, matched=hit["matched"])

    def click_point(self, x: int, y: int, button: str = "left", matched: str | None = None) -> ComputerActionResult:
        from core.vision.screen import capture_screenshot_image
        from core.vision.screen_diff import pixel_change_ratio, screen_visibly_changed

        try:
            before = capture_screenshot_image()
        except Exception:
            before = None

        # Il bersaglio puo' essere stato cercato per secondi (OCR/visione): se nel frattempo
        # l'utente ha detto "Jake, basta", il click non parte (TurnCancelled non e' un Exception).
        raise_if_cancelled()
        try:
            import pyautogui
            if button == "double":
                pyautogui.doubleClick(x, y)
            else:
                pyautogui.click(x, y, button="right" if button == "right" else "left")
        except Exception:
            return ComputerActionResult(success=False, error="OPERATION_FAILED")

        verified, ratio, evidence = False, 0.0, EVIDENCE_NONE
        if before is not None:
            try:
                time.sleep(POST_ACTION_SETTLE_SECONDS)
                after = capture_screenshot_image()
                ratio = pixel_change_ratio(before, after)
                verified = screen_visibly_changed(before, after)
                evidence = EVIDENCE_PIXEL_DIFF
            except Exception:
                pass
        return ComputerActionResult(
            success=True, x=x, y=y, matched=matched, verified=verified, change_ratio=round(ratio, 4),
            evidence=evidence,
        )

    def _locate_element_center(
        self, *, window_title: str | None = None, root=None, name: str | None, control_type: str | None,
        automation_id: str | None, timeout_seconds: float,
    ):
        """F3.4.2 (fattorizzato per `type_into_element`, resto della fetta - stesso identico
        percorso "trova la radice -> trova elemento -> leggi i bounds" gia' usato da
        `click_element`, non duplicato una seconda volta): restituisce `(adapter, element,
        center_x, center_y)`, oppure un `ComputerActionResult` gia' pronto con l'errore giusto se
        un passo qualunque fallisce - il chiamante lo riconosce con `isinstance` e lo restituisce
        cosi' com'e', senza reinterpretare l'errore.

        `root` (F3.6, adozione): un elemento GIA' risolto (es. il nodo `Document` di una pagina
        web, `core/computer_use/browser_adapter.py::find_page_document`) su cui cercare
        direttamente, invece di `window_title` - un browser non ha un titolo di finestra
        prevedibile in anticipo (cambia con ogni pagina/tab caricata), a differenza di un'app Qt
        fissa. Esattamente uno dei due va dato: `root` ha la precedenza se entrambi sono
        presenti, `window_title` resta il percorso esistente e INVARIATO quando `root` e' `None`
        (nessuna skill/chiamante esistente e' toccato da questa aggiunta)."""
        from core.computer_use.selector import AmbiguousSelectionError, ElementSelector, NoMatchError, SelectorEngine
        from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError

        adapter = UIAutomationAdapter()
        if root is not None:
            window = root
        else:
            if window_title is None:
                raise ValueError("click_element/type_into_element richiedono window_title oppure root")
            try:
                window = adapter.find_window_by_title(window_title, timeout_seconds=timeout_seconds)
            except WindowNotFoundError:
                return ComputerActionResult(success=False, error="WINDOW_NOT_FOUND")

        engine = SelectorEngine(adapter)
        selector = ElementSelector(name=name, control_type=control_type, automation_id=automation_id)
        try:
            element = engine.wait_for_unique_element(window, selector, timeout_seconds=timeout_seconds)
        except NoMatchError:
            return ComputerActionResult(success=False, error="NOT_FOUND")
        except AmbiguousSelectionError:
            return ComputerActionResult(success=False, error="AMBIGUOUS_MATCH")

        info = adapter.describe_element(element)
        if info is None:
            return ComputerActionResult(success=False, error="OPERATION_FAILED")
        left, top, width, height = info.bounds
        center_x, center_y = left + width // 2, top + height // 2
        return adapter, element, center_x, center_y

    def click_element(
        self, *, window_title: str | None = None, root=None, name: str | None = None,
        control_type: str | None = None, automation_id: str | None = None, timeout_seconds: float = 5.0,
        idempotency_key: str | None = None, risk_intent: str | None = None,
        policy_parameters: dict | None = None, automated: bool = False, idempotent: bool = False,
    ) -> ComputerActionResult:
        """F3.4.2: trova un elemento per nome/ruolo/automation_id dentro `window_title` (o dentro
        `root`, un elemento gia' risolto - F3.6, un browser non ha un titolo di finestra
        prevedibile) e lo clicca via UI Automation (Invoke, F3.4), con ripiego a un click pixel
        alle stesse coordinate se Invoke fallisce o non ha un effetto visibile (F3.5). Vedi il
        docstring del modulo per le scelte e i limiti dichiarati.

        `idempotency_key` (F3.4.6, "evitare doppia esecuzione sui retry"): se dato e una chiamata
        RIUSCITA con la stessa chiave e' ancora in cache (vedi `ComputerAgent.__init__`), questa
        chiamata restituisce SUBITO quel risultato - senza cercare l'elemento una seconda volta,
        senza muovere il mouse - invece di eseguire di nuovo l'azione. `None` (il default) lascia
        il comportamento IDENTICO a prima di F3.4.6: nessuna skill/chiamante esistente e' toccato
        da questa aggiunta finche' non passa esplicitamente una chiave.

        `risk_intent`/`policy_parameters`/`automated` (F3.4.3, "richiedere policy prima di
        upload, submit, send, delete e purchase"): se `risk_intent` e' dato E `self.policy_engine`
        e' collegato (`ComputerAgent.__init__`), la policy viene verificata PRIMA di cercare
        l'elemento/muovere il mouse - un `POLICY_BLOCKED`/`CONFIRMATION_REQUIRED`/`AUTH_REQUIRED`
        si comporta come un fallimento di ricerca, nessuna azione fisica tentata. Controllato
        DOPO la cache di idempotenza (un cache HIT restituisce un'azione GIA' avvenuta ed e' gia'
        stata autorizzata a suo tempo, ricontrollare la policy per un'azione che non sta per
        accadere non avrebbe senso) - vedi `_check_policy` per i dettagli. `risk_intent=None` (il
        default) lascia il comportamento IDENTICO a prima di F3.4.3.

        `idempotent` (F3.4.6/F3.5.4): un Invoke ESEGUITO (nessuna eccezione) ha gia' premuto il
        bottone; se il pixel diff non vede un cambiamento non e' una prova che non sia successo
        nulla (e' un'evidenza debole, F3.5.5). Ripiegare su un click pixel vorrebbe dire premere
        DUE volte "Aggiungi"/"Invia". Di default quindi la scala si ferma (azione eseguita, effetto
        non verificato, diagnosi nei tentativi); solo il chiamante che sa che una seconda pressione
        e' innocua (selezionare una voce, aprire un menu gia' aperto) passa `idempotent=True` e
        riottiene il ripiego pixel per i controlli dove Invoke non ha effetto reale (Qt)."""
        cached = self._cached_action_result(idempotency_key)
        if cached is not None:
            return cached

        policy_result = self._check_policy(risk_intent, policy_parameters, automated)
        if policy_result is not None:
            return policy_result

        from core.computer_use.executor import ActionExecutor
        from core.computer_use.fallback import try_strategies_in_order
        from core.vision.screen import capture_screenshot_image
        from core.vision.screen_diff import pixel_change_ratio, screen_visibly_changed

        located = self._locate_element_center(
            window_title=window_title, root=root, name=name, control_type=control_type,
            automation_id=automation_id, timeout_seconds=timeout_seconds,
        )
        if isinstance(located, ComputerActionResult):
            return located
        _adapter, element, center_x, center_y = located
        executor = ActionExecutor()

        try:
            before = capture_screenshot_image()
        except Exception:
            before = None
        last_ratio = [0.0]
        # F3.5.5/click_point: "success" (l'azione e' stata davvero ESEGUITA, mai sollevato) resta
        # un fatto diverso da "verified" (l'evidenza debole del pixel diff l'ha confermato) -
        # stessa distinzione gia' seguita da click_point, un click legittimo che non cambia nulla
        # di visibile (es. un link verso una pagina gia' aperta) non deve diventare un fallimento.
        action_performed = [False]

        def _verify() -> bool:
            if before is None:
                return False
            try:
                time.sleep(POST_ACTION_SETTLE_SECONDS)
                after = capture_screenshot_image()
                last_ratio[0] = pixel_change_ratio(before, after)
                return screen_visibly_changed(before, after)
            except Exception:
                return False

        def _uia_invoke() -> None:
            executor.invoke(element)
            action_performed[0] = True

        def _pixel_click() -> None:
            import pyautogui
            raise_if_cancelled()
            pyautogui.click(center_x, center_y)
            action_performed[0] = True

        outcome = try_strategies_in_order(
            [("uia_invoke", _uia_invoke, not idempotent), ("pixel_click", _pixel_click)],
            verify=_verify,
        )

        attempts = _describe_attempts(outcome)
        if not action_performed[0]:
            return ComputerActionResult(success=False, error="OPERATION_FAILED", attempts=attempts)
        result = ComputerActionResult(
            success=True, x=center_x, y=center_y, matched=name,
            verified=outcome.succeeded, change_ratio=round(last_ratio[0], 4),
            evidence=EVIDENCE_PIXEL_DIFF if before is not None else EVIDENCE_NONE,
            strategy=_last_performed_strategy(outcome), attempts=attempts,
        )
        self._remember_action_result(idempotency_key, result)
        return result

    def type_into_element(
        self, text: str, *, window_title: str | None = None, root=None, name: str | None = None,
        control_type: str | None = None, automation_id: str | None = None, timeout_seconds: float = 5.0,
        idempotency_key: str | None = None, risk_intent: str | None = None,
        policy_parameters: dict | None = None, automated: bool = False,
    ) -> ComputerActionResult:
        """F3.4.2 (resto - "unificare... type... nel ComputerAgent"): trova un campo di testo per
        nome/ruolo/automation_id dentro `window_title` (o dentro `root`, F3.6, come per
        `click_element`) e vi scrive `text` tramite il pattern Value di UI Automation (F3.4,
        `SetValue` - non digitazione tasto per tasto simulata), ripiegando su un click +
        digitazione reale (`pyautogui.click` poi `pyautogui.write`) alle stesse coordinate se
        Value non e' disponibile o non ha un effetto visibile (F3.5). Stessa struttura di
        `click_element` sopra, stessa distinzione success/verified di `click_point` (F3.5.5) -
        vedi i loro docstring per le scelte gia' motivate, non ripetute qui.

        `text` NON compare MAI in `ComputerActionResult` (ne' in `matched` ne' altrove) - lo
        stesso principio gia' seguito da `ElementActionReceipt.set_value` (F3.4.5): un campo
        testo libero nel risultato rischierebbe di far finire una password o un dato sensibile
        digitato dall'utente in una struttura che un futuro chiamante potrebbe loggare.

        `idempotency_key` (F3.4.6): stessa identica protezione di `click_element` - vedi il suo
        docstring, non ripetuto qui. Particolarmente rilevante qui: senza, un retry su un campo
        gia' scritto correttamente rischierebbe di ADD/duplicare il testo invece di limitarsi a
        non fare nulla (il ripiego pixel gia' seleziona tutto prima di scrivere per restare
        sicuro da incatenare CON SE STESSO in un'unica chiamata - vedi sopra - ma questo non
        protegge da una SECONDA chiamata dall'esterno con lo stesso intento logico, il caso che
        `idempotency_key` copre).

        `risk_intent`/`policy_parameters`/`automated` (F3.4.3): stessa identica protezione di
        `click_element` - vedi il suo docstring. Verificato PRIMA di scrivere `text` da qualunque
        parte, mai dopo (un `POLICY_BLOCKED` scoperto dopo aver gia' scritto sarebbe inutile)."""
        cached = self._cached_action_result(idempotency_key)
        if cached is not None:
            return cached

        policy_result = self._check_policy(risk_intent, policy_parameters, automated)
        if policy_result is not None:
            return policy_result

        from core.computer_use.executor import ActionExecutor
        from core.computer_use.fallback import try_strategies_in_order
        from core.vision.screen import capture_screenshot_image
        from core.vision.screen_diff import pixel_change_ratio, screen_visibly_changed

        located = self._locate_element_center(
            window_title=window_title, root=root, name=name, control_type=control_type,
            automation_id=automation_id, timeout_seconds=timeout_seconds,
        )
        if isinstance(located, ComputerActionResult):
            return located
        _adapter, element, center_x, center_y = located
        executor = ActionExecutor()

        try:
            before = capture_screenshot_image()
        except Exception:
            before = None
        last_ratio = [0.0]
        action_performed = [False]
        evidence = [EVIDENCE_PIXEL_DIFF if before is not None else EVIDENCE_NONE]

        def _verify() -> bool:
            # Prova forte prima: il campo contiene davvero il testo (Value riletto). Il pixel diff
            # su tutto lo schermo non vede un campo piccolo e farebbe riscrivere per niente.
            try:
                if _adapter.read_value(element) == text:
                    evidence[0] = EVIDENCE_UIA_PROPERTY
                    return True
            except Exception:
                pass
            if before is None:
                return False
            try:
                time.sleep(POST_ACTION_SETTLE_SECONDS)
                after = capture_screenshot_image()
                last_ratio[0] = pixel_change_ratio(before, after)
                return screen_visibly_changed(before, after)
            except Exception:
                return False

        def _uia_set_value() -> None:
            executor.set_value(element, text)
            action_performed[0] = True

        def _pixel_type() -> None:
            import pyautogui
            raise_if_cancelled()
            pyautogui.click(center_x, center_y)
            # Ctrl+A poi scrivi, MAI scrivere direttamente sul campo com'e' - buco reale trovato
            # verificando questo metodo, non ipotizzato (vedi ROADMAP_EXECUTION.md): SetValue
            # (F3.4) puo' riuscire per davvero (il campo cambia sul serio, verificato leggendo
            # CurrentValue) mentre l'evidenza debole del pixel diff (F3.5.5, calcolata sull'INTERO
            # schermo) non rileva il cambiamento in un campo piccolo e fa scattare comunque questo
            # ripiego - senza selezionare tutto prima, `pyautogui.write` si limiterebbe ad
            # AGGIUNGERE il testo a quello gia' impostato da SetValue, duplicandolo invece di
            # sostituirlo (`"testo" -> "testotesto"`). Selezionare tutto prima rende il ripiego
            # SICURO da incatenare anche quando la strategia precedente e' gia' riuscita
            # silenziosamente, non solo quando e' davvero fallita.
            pyautogui.hotkey("ctrl", "a")
            pyautogui.write(text)
            action_performed[0] = True

        outcome = try_strategies_in_order(
            [("uia_set_value", _uia_set_value), ("pixel_type", _pixel_type)],
            verify=_verify,
        )

        attempts = _describe_attempts(outcome)
        if not action_performed[0]:
            return ComputerActionResult(success=False, error="OPERATION_FAILED", attempts=attempts)
        result = ComputerActionResult(
            success=True, x=center_x, y=center_y, matched=name,
            verified=outcome.succeeded, change_ratio=round(last_ratio[0], 4),
            evidence=evidence[0], strategy=_last_performed_strategy(outcome), attempts=attempts,
        )
        self._remember_action_result(idempotency_key, result)
        return result
