"""Scala di ripiego (F3.5.1/F3.5.2/F3.5.3, prima fetta di F3.5 - "Fallback ladder", mai iniziata
prima d'ora - vedi ROADMAP_EXECUTION.md sezione F3.5, dipende dalla scoperta empirica di F3.4).

Motivazione CONCRETA, non teorica: F3.4 ha trovato tre buchi reali del ponte di accessibilita' di
Qt (ExpandCollapse senza effetto, Scroll non disponibile, e il piu' insidioso - SelectionItem su
un `QListWidgetItem` che riporta successo senza un effetto vero, vedi
`core/computer_use/executor.py`). Questo modulo e' la risposta strutturale: quando UI Automation
non basta, tentare la strategia SUCCESSIVA (F3.5.1, "API/app adapter -> UIA -> browser DOM -> OCR
-> vision -> coordinate" - qui solo UIA -> coordinate, gli altri gradini non ancora costruiti),
verificando DOPO ogni tentativo (F3.5.3, mai fidandosi che una chiamata "non abbia sollevato" come
prova di successo - lo stesso principio che ha trovato il buco di SelectionItem), registrando
PERCHE' ogni strategia precedente e' stata scartata (F3.5.2).

**Buco reale trovato USANDO questo modulo per risolvere il caso concreto di F3.4, non ipotizzato -
piu' insidioso di quanto sperato**: la sequenza ovvia "prova SelectionItem via UIA, se non riesce
prova un click reale a coordinate pixel" NON basta contro un `QListWidgetItem` di Qt. Il click
pixel DA SOLO (mai preceduto da un tentativo UIA sullo STESSO elemento) funziona in modo
affidabile, verificato ripetutamente - ma se preceduto da una `Select()` UIA gia' fallita (F3.4),
lo stesso identico click pixel, sugli stessi identici pixel, con `ComputerActionResult.success=
True`, smette di ottenere l'effetto reale (il bottone "Rimuovi selezionato" resta disabilitato) -
verificato riproducendo il fallimento piu' volte, anche con un click su un punto neutro in mezzo
per tentare di "resettare" lo stato (non ha aiutato). Il tentativo UIA fallito lascia l'elemento
in uno stato che impedisce anche al fallback successivo di recuperare - un problema DIVERSO e piu'
sottile di F3.5.4 ("non ricliccare un'azione non idempotente sui retry", che riguarda ripetere la
STESSA strategia): qui e' una PRIMA strategia fallita a corrompere lo stato per la SECONDA,
diversa. Conseguenza pratica dichiarata onestamente: la scala di ripiego costruita qui e'
CORRETTA come meccanismo generico (verificato con strategie che non si "avvelenano" a vicenda,
vedi `tests/test_fallback.py`), ma la coppia specifica "UIA SelectionItem poi click pixel sullo
STESSO elemento di lista" non e' oggi una combinazione affidabile - un chiamante reale dovrebbe
o saltare direttamente al click pixel per questo caso noto, o (passo futuro dichiarato, non
affrontato qui) inserire un passo di "reset" esplicito tra le strategie quando la prima e'
sospettata di aver lasciato uno stato parziale.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.5.1 (resto): solo due gradini della scala completa (UIA -> coordinate pixel) - "API/app
  adapter" e "browser DOM" non hanno ancora nulla da collegare (F3.6/F3.7 non iniziate), "OCR"/
  "vision" non ancora inseriti come gradini intermedi tra UIA e le coordinate pixel grezze;
- F3.5.4 (non ricliccare un'azione non idempotente sui retry - questo modulo tenta ogni strategia
  una volta sola in ordine, mai un retry della STESSA strategia, gia' prima di questo incremento);
- F3.5.5 (pixel diff come evidenza debole - la verifica qui e' quella fornita dal chiamante,
  tipicamente uno stato applicativo vero come nella fixture, non un pixel diff);
- F3.5.7 (conservare il resource lock durante il cambio strategia - nessun collegamento a
  `core/resource_lock.py` ancora).

`unsafe_after_failure` (F3.5.6, "fermarsi con diagnosi quando un ulteriore tentativo e' troppo
rischioso", adozione motivata dal buco di poisoning trovato sopra): un marcatore OPZIONALE per
STRATEGIA (terzo elemento della tupla, `False` di default - retrocompatibile con le tuple a due
elementi gia' in uso da ogni chiamante esistente) - quando la strategia cosi' marcata ESEGUE senza
sollevare ma `verify()` non conferma un effetto reale, la scala si FERMA li', senza tentare le
strategie successive, invece di incatenare alla cieca un altro tentativo su un bersaglio che questa
stessa strategia potrebbe aver gia' corrotto (esattamente il buco concreto documentato sopra -
UIA `select()` fallito seguito da un click pixel altrimenti affidabile che smette di funzionare).
Non RISOLVE il buco (nessun modo noto di recuperare lo stato una volta corrotto - dichiarato onesto
sopra), ma trasforma un fallimento silenzioso e fuorviante ("nessuna strategia ha funzionato",
senza dire perche' le successive non sono nemmeno state provate) in una diagnosi esplicita
(`FallbackAttempt.reason` nomina il rischio) che il chiamante puo' usare per decidere il prossimo
passo (es. saltare direttamente al click pixel DA SOLO, come gia' fa
`tests/test_computer_use_integration.py`, invece di scoprire la corruzione tentando comunque)."""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class FallbackAttempt:
    """F3.5.2: un tentativo di UNA strategia, con il motivo esplicito dello scarto se non e'
    quella che ha risolto - mai un tentativo "silenzioso" che sparisce se fallisce."""

    strategy_name: str
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class FallbackOutcome:
    """L'esito di `try_strategies_in_order` - `attempts` conserva OGNI tentativo nell'ordine
    fatto, non solo quello riuscito (F3.5.2, "registrare perche' ogni strategia precedente e'
    stata scartata")."""

    succeeded: bool
    attempts: tuple[FallbackAttempt, ...]

    @property
    def successful_strategy(self) -> str | None:
        for attempt in self.attempts:
            if attempt.succeeded:
                return attempt.strategy_name
        return None


def try_strategies_in_order(
    strategies: list[tuple[str, Callable[[], None]] | tuple[str, Callable[[], None], bool]],
    verify: Callable[[], bool],
) -> FallbackOutcome:
    """F3.5.1 (nucleo): tenta ogni strategia IN ORDINE (la lista e' gia' nell'ordine di
    preferenza dichiarato dal chiamante - questa funzione non ne conosce la semantica, solo
    l'ordine), fermandosi alla prima che `verify()` conferma riuscita DOPO averla tentata (F3.5.3,
    "ri-osservare PRIMA di cambiare strategia" - mai valutato prima del tentativo, sempre dopo).
    Un'eccezione sollevata da una strategia viene catturata e registrata come fallimento di
    QUELLA strategia (con il messaggio dell'eccezione come motivo, F3.5.2), non propagata - le
    strategie successive vengono comunque tentate. Se nessuna strategia riesce, `succeeded=False`
    con tutti i tentativi registrati, mai un'eccezione generica che nasconderebbe quali strategie
    sono state provate e perche' sono fallite.

    Ogni tupla accetta un terzo elemento opzionale `unsafe_after_failure` (F3.5.6, vedi il
    docstring del modulo, default `False` se omesso - retrocompatibile con le tuple a due elementi
    gia' in uso): se la strategia cosi' marcata ESEGUE senza sollevare ma `verify()` non conferma,
    la scala si ferma li' invece di tentare le successive alla cieca su un bersaglio potenzialmente
    gia' corrotto da questo stesso tentativo."""
    attempts: list[FallbackAttempt] = []
    for entry in strategies:
        strategy_name, action, unsafe_after_failure = entry if len(entry) == 3 else (*entry, False)
        try:
            action()
        except Exception as exc:
            attempts.append(FallbackAttempt(strategy_name=strategy_name, succeeded=False, reason=str(exc)))
            continue
        if verify():
            attempts.append(FallbackAttempt(strategy_name=strategy_name, succeeded=True))
            return FallbackOutcome(succeeded=True, attempts=tuple(attempts))
        reason = "verifica fallita dopo l'azione"
        if unsafe_after_failure:
            reason += (
                " - scala interrotta qui (F3.5.6): questa strategia e' marcata rischiosa da "
                "incatenare, il bersaglio potrebbe essere in uno stato che le strategie restanti "
                "non possono piu' recuperare"
            )
        attempts.append(FallbackAttempt(strategy_name=strategy_name, succeeded=False, reason=reason))
        if unsafe_after_failure:
            break
    return FallbackOutcome(succeeded=False, attempts=tuple(attempts))
