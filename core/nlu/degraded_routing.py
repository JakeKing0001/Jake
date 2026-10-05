"""Instradamento quando la GPU e' ceduta (core/gpu_yield.py): prima tutto cio' che non chiede un modello.

Misura del 05/10/2026 (gemma3:4b, Ollama 0.35.1): sulla GPU il classificatore costa ~1 s; sulla CPU il solo prefill dei
~2000 token del prompt costa 19-26 s (~90 token/s), il caricamento a freddo 6,5 s una volta, la generazione 1-2 s.
Tenere il modello caldo non aiuta e alzare il timeout significherebbe aspettare 25 s per "abbassa il volume".

Quindi, a GPU ceduta, prima delle chiamate al modello:
- regole locali, ma solo per frasi brevi, non composte, con intent di sola lettura o reversibili (mai "chiudi",
  "elimina", spegnimenti: quelli passano dal modello come sempre) e, per ora/data, solo formule esplicite;
- l'esempio piu' simile, con soglia piu' bassa del normale ma stessi vincoli e parametri letterali;
- domande di cultura generale/spiegazioni/consigli non sul PC -> ASK_QUESTION (la regola di #201, senza modello).
Solo cio' che resta va al classificatore, in forma compatta (core/nlu/llm_classifier.py)."""
from __future__ import annotations

import re

from core.command import Command
from core.learning_manager import looks_compound
from core.risk import RiskLevel, risk_of

FAST_MAX_WORDS = 8
FAST_RETRIEVAL_SCORE = 0.80
FAST_RISKS = {RiskLevel.READ_ONLY, RiskLevel.LOCAL_REVERSIBLE}
_ARTICLES = {"il", "lo", "la", "l", "i", "gli", "le", "un", "una", "uno"}

_COURTESY_HEAD = re.compile(r"^(?:jake[,!.\s]+)?(?:(?:mi\s+)?(?:puoi|potresti|riesci\s+a)\s+)?", re.IGNORECASE)
_COURTESY_TAIL = re.compile(r"[\s,]*(?:per\s+favore|per\s+piacere|grazie|please)[\s.!?]*$", re.IGNORECASE)
# le regole riconoscono ora/data con un "acchiappa-tutto" su ora|ore|giorno|oggi ("a che ora parte il treno" ->
# GET_TIME): nella corsia veloce valgono solo le formule esplicite
_EXPLICIT_TIME = re.compile(r"\bche\s+or[ae]\s+(?:sono|e'|è)\b|\b(?:dimmi|sai\s+dirmi)\s+l'ora\b", re.IGNORECASE)
_EXPLICIT_DATE = re.compile(r"\bche\s+(?:giorno|data)\s+(?:e'|è|siamo|abbiamo)\b|\bquanti\s+ne\s+abbiamo\b|\bdimmi\s+la\s+data\b",
                            re.IGNORECASE)
_KNOWLEDGE_OPENER = re.compile(
    r"^(?:e\s+)?(?:chi\s+(?:era|e'|è|sono|erano|ha|fu)|che\s+cos[a']\s*(?:e'|è)?|cos[a']\s*(?:e'|è|sono|significa|vuol)|"
    r"cosa\s+(?:e'|è|sono|significa|vuol)|perch[eé']|come\s+(?:funziona|funzionano|si\s+fa|nasce|nascono|mai)|"
    r"qual\s*(?:e'|è|'è)|quali\s+sono|spiegami|spiegamelo|raccontami\s+(?:di|la\s+storia)|che\s+differenza|"
    r"dammi\s+un\s+consiglio|consigliami|quando\s+(?:e'|è)\s+(?:nato|nata|morto|morta|successo))",
    re.IGNORECASE,
)
# riferimenti a QUESTO PC/sessione: allora non e' cultura generale ("cos'e' questo file", "perche' il mio pc...")
_PC_REFERENCE = re.compile(
    r"\b(?:questo|questa|questi|queste|quel|quello|quella|mio|mia|miei|mie|sul\s+pc|del\s+pc|nel\s+pc|sul\s+computer|"
    r"del\s+computer|sullo\s+schermo|negli\s+appunti|ho\s+copiato|sto\s+usando|ho\s+aperto|"
    # domande su cio' che ha fatto o detto Jake ("perche' me l'hai detto"): sono sulla conversazione, non cultura
    r"hai|mi\s+hai|me\s+l'hai|l'hai|tu|ti)\b",
    re.IGNORECASE,
)
# correzioni ("no, intendevo apri spotify"): le gestisce JakeCore come correzione, mai la corsia veloce
_CORRECTION = re.compile(r"^(?:no|non)\b|\bintendevo\b|\bvolevo\s+dire\b", re.IGNORECASE)


def clean_courtesy(text: str) -> str:
    cleaned = _COURTESY_TAIL.sub("", _COURTESY_HEAD.sub("", (text or "").strip()))
    return cleaned.strip() or (text or "").strip()


def is_knowledge_question(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return bool(_KNOWLEDGE_OPENER.match(lowered)) and not _PC_REFERENCE.search(lowered)


def _safe_intent(intent: str) -> bool:
    if intent in ("UNKNOWN", "ASK_QUESTION", "CHITCHAT"):
        return False
    try:
        return risk_of(intent) in FAST_RISKS
    except Exception:
        return False


def rule_fast_path(text: str, rules, app_resolver=None) -> Command | None:
    """Il comando delle regole locali se e' abbastanza sicuro da non chiedere al modello, altrimenti None.
    OPEN_APP solo per un'app che il resolver trova davvero: "apri amazon" e' un sito (OPEN_URL), lo decide il modello."""
    cleaned = clean_courtesy(text)
    if len(cleaned.split()) > FAST_MAX_WORDS or looks_compound(cleaned) or _CORRECTION.search(cleaned):
        return None
    command = rules.detect_intent(cleaned.lower())
    if not _safe_intent(command.intent):
        return None
    if command.intent == "GET_TIME" and not _EXPLICIT_TIME.search(cleaned):
        return None
    if command.intent == "GET_DATE" and not _EXPLICIT_DATE.search(cleaned):
        return None
    if command.intent == "OPEN_APP":
        app = (command.parameters or {}).get("app", "")
        try:
            match = app_resolver.resolve(app) if app_resolver is not None and app else None
        except Exception:
            match = None
        if match is None:
            return None
        # somiglianza approssimata ("whatsapp web" -> WhatsApp): ogni parola chiesta deve stare nel nome trovato
        words = [w for w in re.split(r"[\s']+", app.lower()) if w and w not in _ARTICLES]
        if match.score < 1.0 and not all(word in str(match.matched_app).lower() for word in words):
            return None
    return command


def retrieval_fast_path(text: str, retriever) -> Command | None:
    """L'esempio piu' simile, con soglia ridotta ma solo per intent sicuri, frasi non composte e parametri letterali."""
    if retriever is None or looks_compound(text):
        return None
    try:
        retrieval = retriever.retrieve(text, max_capabilities=5, max_examples=3)
    except Exception:
        return None
    example = retrieval.best_example
    if example is None or example.source == "forge" or retrieval.best_score < FAST_RETRIEVAL_SCORE:
        return None
    if not _safe_intent(example.intent):
        return None
    lowered = text.lower()
    for value in example.parameters.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, str) and value and value.lower() not in lowered:
            return None
        if isinstance(value, (int, float)) and str(value) not in lowered:
            return None
    return Command(example.intent, dict(example.parameters))
