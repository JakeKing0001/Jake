"""Regole del dialogo parlato (F2.6.3, F2.6.4, F2.6.6, F2.6.7): puro testo e decisioni, nessun modello.

Quattro domande che nel parlato si confondono facilmente e qui hanno una risposta scritta:

1. Cosa e' questa frase? Un si'/no a una conferma, una risposta a una domanda di chiarimento, la
   password che Jake ha chiesto, oppure un comando nuovo (`classify_reply`). La password non passa mai
   per il resto della pipeline: e' un tipo di risposta a parte, e il suo testo non compare in `repr`.
2. Serve chiedere conferma? Dipende dall'impatto E dalla certezza del riconoscimento
   (`needs_confirmation`): chiedere "sei sicuro?" per ogni "che ore sono" rende Jake inutilizzabile,
   non chiederlo prima di cancellare un file lo rende pericoloso.
3. L'utente corregge cio' che ha sentito ("no, intendevo..."): l'azione precedente e' gia' avvenuta?
   Se si', NON la si rifa' in silenzio (`CorrectionPlanner`): si propone di annullare, o si chiede.
4. Cosa si impara da una correzione? Solo se l'esito del comando corretto e' stato VERIFICATO
   (`CorrectionPlanner.resolve_learning`): un esempio nato da un comando fallito insegnerebbe l'errore."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from core.risk import RiskLevel
from core.voice.language_normalizer import COMMAND_VERBS

# ---- 1. che tipo di risposta e' ----------------------------------------------------------------------------


class ReplyKind(str, Enum):
    CONFIRM_YES = "confirm_yes"
    CONFIRM_NO = "confirm_no"
    AUTH_SECRET = "auth_secret"
    CLARIFICATION_ANSWER = "clarification_answer"
    NEW_COMMAND = "new_command"


@dataclass(frozen=True)
class DialogueContext:
    pending_confirmation: bool = False
    awaiting_auth: bool = False
    awaiting_clarification: bool = False


@dataclass(frozen=True)
class Reply:
    kind: ReplyKind
    text: str = ""  # vuoto per AUTH_SECRET: il segreto sta solo in `secret`
    cancels_pending: bool = False  # una conferma in sospeso va annullata: l'utente e' passato ad altro
    secret: str = field(default="", repr=False)  # mai in repr/log per errore


_WAKE = re.compile(r"^\s*(?:(?:ehi|hey|ok|okay|ciao)\s+)?(?:jake|geek|jack)[\s,.!?]*", re.IGNORECASE)
_YES = re.compile(
    r"^(?:s[iì]|s[iì] s[iì]|s[iì] certo|certo|certamente|ok|okay|va bene|d'accordo|confermo|conferma|"
    r"procedi|vai|vai pure|fallo|fai pure|esatto|affermativo|yes|yeah|yep|sure|do it|go ahead|confirm)[\s,.!?]*$",
    re.IGNORECASE,
)
_NO = re.compile(
    r"^(?:no|no no|nope|annulla|annullalo|negativo|lascia stare|lascia perdere|non farlo|non lo fare|fermati|"
    r"stop|basta|cancel|don'?t|mai|niente|nulla)[\s,.!?]*$",
    re.IGNORECASE,
)
_QUESTION_WORDS = {"che", "come", "quando", "dove", "chi", "quanto", "quale", "quali", "perche", "perché", "cosa"}


def _looks_like_new_command(text: str) -> bool:
    words = re.findall(r"[\w']+", text.lower())
    if not words:
        return False
    return words[0] in COMMAND_VERBS or words[0] in _QUESTION_WORDS or "?" in text or bool(_WAKE.match(text))


def classify_reply(text: str, context: DialogueContext) -> Reply:
    """Tipo di una frase in base a cosa Jake sta aspettando. Ordine di priorita' (dal piu' sensibile):
    1. In attesa di autenticazione TUTTO e' il segreto: non si interpreta, non si normalizza, non si
       manda a NLU/LLM e non si registra.
    2. Conferma in sospeso: solo una frase INTERA di si'/no e' una risposta alla conferma; "si' ma
       prima apri Spotify" e' un comando nuovo e annulla la conferma (lasciarla armata mentre l'utente
       parla d'altro significherebbe che un "ok" detto a Spotify potrebbe confermare una cancellazione).
    3. In attesa di un chiarimento: una frase breve che non sembra un comando e' la risposta.
    4. Tutto il resto e' un comando nuovo."""
    if context.awaiting_auth:
        return Reply(ReplyKind.AUTH_SECRET, secret=text)
    cleaned = _WAKE.sub("", text or "", count=1).strip()
    if context.pending_confirmation:
        if _YES.match(cleaned):
            return Reply(ReplyKind.CONFIRM_YES, cleaned)
        if _NO.match(cleaned):
            return Reply(ReplyKind.CONFIRM_NO, cleaned)
        return Reply(ReplyKind.NEW_COMMAND, cleaned or (text or "").strip(), cancels_pending=True)
    if context.awaiting_clarification and cleaned and not _looks_like_new_command(text) and len(cleaned.split()) <= 8:
        return Reply(ReplyKind.CLARIFICATION_ANSWER, cleaned)
    return Reply(ReplyKind.NEW_COMMAND, cleaned or (text or "").strip())


# ---- 2. serve una conferma? -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfirmDecision:
    required: bool
    reason: str


# soglia di confidenza SOTTO la quale si chiede conferma, per livello di rischio
_CONFIRM_BELOW = {
    RiskLevel.READ_ONLY: 0.0,  # sbagliare una lettura costa una risposta inutile, mai un danno
    RiskLevel.LOCAL_REVERSIBLE: 0.5,
    RiskLevel.EXTERNAL_ACTION: 0.8,  # esce dal PC: non si torna indietro con un "annulla"
}
UNKNOWN_CONFIDENCE = 0.7  # motore che non riporta la confidenza: ne' fiducia piena ne' sospetto totale


def needs_confirmation(confidence: float | None, risk: RiskLevel) -> ConfirmDecision:
    """Conferma solo quando l'impatto la richiede (F2.6.6). DESTRUCTIVE e ADMIN chiedono SEMPRE,
    qualunque sia la confidenza: un riconoscimento sicuro al 99% resta un file cancellato."""
    if risk in (RiskLevel.DESTRUCTIVE, RiskLevel.ADMIN):
        return ConfirmDecision(True, f"azione {risk.value}: conferma sempre richiesta")
    value = UNKNOWN_CONFIDENCE if confidence is None else confidence
    threshold = _CONFIRM_BELOW[risk]
    if value < threshold:
        return ConfirmDecision(True, f"riconoscimento incerto ({value:.2f} < {threshold:.2f}) per un'azione {risk.value}")
    return ConfirmDecision(False, "impatto e certezza non richiedono conferma")


# ---- 3./4. correzioni e apprendimento ---------------------------------------------------------------------------

@dataclass
class HeardTurn:
    utterance_id: str
    heard: str
    intent: str
    parameters: dict
    risk: RiskLevel
    status: str  # "pending" | "executed" | "failed" | "cancelled"
    reversible: bool = False
    at: float = 0.0


@dataclass(frozen=True)
class CorrectionPlan:
    action: str  # "rerun" | "undo_then_rerun" | "ask" | "unknown"
    reason: str


@dataclass(frozen=True)
class Learnable:
    heard: str
    corrected_text: str
    intent: str
    parameters: dict


class CorrectionPlanner:
    """Tiene gli ultimi turni sentiti e decide cosa fare quando l'utente li corregge."""

    def __init__(self, clock: Callable[[], float] = time.time, keep: int = 20) -> None:
        self._clock = clock
        self._keep = keep
        self._turns: dict[str, HeardTurn] = {}
        self._pending_learning: dict[str, Learnable] = {}

    def record(self, utterance_id: str, heard: str, intent: str, parameters: dict, risk: RiskLevel, status: str, reversible: bool = False) -> HeardTurn:
        turn = HeardTurn(utterance_id, heard, intent, dict(parameters), risk, status, reversible, self._clock())
        self._turns[utterance_id] = turn
        while len(self._turns) > self._keep:
            self._turns.pop(next(iter(self._turns)))
        return turn

    def update_status(self, utterance_id: str, status: str) -> None:
        if utterance_id in self._turns:
            self._turns[utterance_id].status = status

    def last_heard(self, count: int = 1) -> list[HeardTurn]:
        """"Cosa hai sentito?" (F2.6.2): gli ultimi turni, dal piu' recente."""
        return list(self._turns.values())[-count:][::-1]

    def plan(self, utterance_id: str) -> CorrectionPlan:
        """Cosa fare se l'utente corregge il turno `utterance_id` (F2.6.3). L'azione gia' AVVENUTA non
        si ripete mai da sola: se e' reversibile si propone di annullarla prima, altrimenti si chiede."""
        turn = self._turns.get(utterance_id)
        if turn is None:
            return CorrectionPlan("unknown", "turno non trovato: chiedi all'utente di ripetere")
        if turn.status in ("pending", "failed", "cancelled"):
            return CorrectionPlan("rerun", f"nulla e' stato eseguito ({turn.status}): si puo' rieseguire con il testo corretto")
        if turn.reversible and turn.risk in (RiskLevel.READ_ONLY, RiskLevel.LOCAL_REVERSIBLE):
            return CorrectionPlan("undo_then_rerun", "azione locale gia' eseguita e reversibile: annullala, poi esegui la versione corretta")
        return CorrectionPlan("ask", f"azione {turn.risk.value} gia' eseguita e non annullabile in sicurezza: chiedi all'utente cosa vuole fare")

    def propose_learning(self, utterance_id: str, corrected_text: str, intent: str, parameters: dict) -> bool:
        """Registra che la correzione POTREBBE diventare un esempio: non lo diventa finche' l'esito
        del comando corretto non e' verificato (`resolve_learning`)."""
        turn = self._turns.get(utterance_id)
        if turn is None or not corrected_text.strip():
            return False
        self._pending_learning[utterance_id] = Learnable(turn.heard, corrected_text.strip(), intent, dict(parameters))
        return True

    def resolve_learning(self, utterance_id: str, verified: bool) -> Learnable | None:
        """L'esempio da salvare (solo se l'esito e' verificato) oppure None. In ogni caso la proposta
        viene consumata: non resta in sospeso per un eventuale esito successivo."""
        learnable = self._pending_learning.pop(utterance_id, None)
        return learnable if verified else None

    @property
    def pending_learning_count(self) -> int:
        return len(self._pending_learning)
