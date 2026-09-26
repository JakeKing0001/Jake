"""Stress deterministico del dialogo vocale (complementare a benchmarks/bench_dialogue.py).

Ogni caso passa da una funzione VERA del percorso di dialogo, senza LLM: classificazione della
risposta (conferma/auth/chiarimento/comando nuovo), classificazione di un'interruzione (stop /
correzione / nuova richiesta), ellissi, pronomi, ordinali, numeri in lettere, termini tecnici
italiano+inglese, nomi propri, piano di correzione di un turno gia' eseguito e isolamento della
correzione per profilo vocale.

Esiti per caso:
- ok: risultato atteso al primo tentativo;
- declined: il caso e' ambiguo e il sistema correttamente NON indovina (None / "ask" / "unknown");
- unsafe_rerun: un piano di correzione ripeterebbe da solo un'azione gia' eseguita non ripetibile;
- error: qualunque altro risultato diverso da quello atteso.

Il gate e' zero errori e zero riesecuzioni non sicure: nessuna soglia percentuale.

    python -m benchmarks.bench_dialogue_stress
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from core.intent_patterns import match_meta_command, resolve_pronouns
from core.risk import RiskLevel
from core.voice.barge_in import classify_interruption
from core.voice.dialogue import CorrectionPlanner, DialogueContext, ReplyKind, classify_reply
from core.voice.dialogue_runtime import DialogueRuntime
from core.voice.language_normalizer import (
    italian_number_to_int,
    normalize_transcript,
    numbers_to_digits,
    resolve_ellipsis,
    resolve_ordinals,
)

DECLINED = object()  # atteso: il sistema non deve indovinare


@dataclass(frozen=True)
class Case:
    category: str
    description: str
    run: Callable[[], object]
    expected: object
    correction: bool = False  # il caso e' una correzione dell'utente


def _reply(text, **context):
    reply = classify_reply(text, DialogueContext(**context))
    return (reply.kind, reply.text, reply.cancels_pending, reply.secret)


def _kind(text, **context):
    return classify_reply(text, DialogueContext(**context)).kind


def _interruption(text):
    result = classify_interruption(text)
    return (result.kind, result.remainder)


def _ellipsis(text, intent, parameters):
    result = resolve_ellipsis(text, intent, parameters)
    return DECLINED if result is None else (result.intent, result.parameters)


def _ordinals(text, total):
    result = resolve_ordinals(text, total)
    return DECLINED if result is None else result


def _plan(risk, status, reversible=False):
    planner = CorrectionPlanner(clock=lambda: 0.0)
    planner.record("a1", "sentito", "INTENT", {}, risk, status, reversible=reversible)
    return planner.plan("a1").action


def _scoped_correction(record_scope, correct_scope):
    runtime = DialogueRuntime()
    runtime.record_turn(scope=record_scope, action_id="a1", heard="manda ciao a Luca", intent="SEND_MESSAGE",
                        parameters={"contact": "Luca", "text": "ciao"}, risk=RiskLevel.EXTERNAL_ACTION,
                        status="executed")
    action_id, plan, _turn = runtime.plan_correction(correct_scope)
    return (action_id, plan.action)


def _scope_for(profile_id=None, device_id=None):
    """Lo scope che JakeCore usa per le correzioni, calcolato nel contesto del turno."""
    import contextvars

    from core.jake_core import JakeCore
    from core.request_context import set_current_device_id, set_current_speaker_profile_id

    def compute():
        if profile_id is not None:
            set_current_speaker_profile_id(profile_id)
        if device_id is not None:
            set_current_device_id(device_id)
        return JakeCore._dialogue_scope(None)
    return contextvars.copy_context().run(compute)


ENTITIES = {"path": "C:/Users/me/Documenti/relazione.docx", "app": "Spotify", "title": "Spotify Premium"}
VOCABULARY = ("Spotify", "Discord", "Telegram", "Photoshop", "Marco", "Giulia", "Visual Studio Code")

CASES: list[Case] = [
    # ---- ellissi --------------------------------------------------------------------------------
    Case("ellipsis", "citta' dopo il meteo", lambda: _ellipsis("e a Milano?", "GET_WEATHER", {"city": "Roma"}),
         ("GET_WEATHER", {"city": "Milano"})),
    Case("ellipsis", "invece + preposizione", lambda: _ellipsis("invece a Napoli", "GET_WEATHER", {"city": "Roma"}),
         ("GET_WEATHER", {"city": "Napoli"})),
    Case("ellipsis", "e poi + articolo", lambda: _ellipsis("e poi la Germania?", "GET_NEWS", {"topic": "Francia"}),
         ("GET_NEWS", {"topic": "Germania"})),
    Case("ellipsis", "anche + nome app", lambda: _ellipsis("anche Discord", "OPEN_APP", {"app": "Spotify"}),
         ("OPEN_APP", {"app": "Discord"})),
    Case("ellipsis", "un parametro testuale e uno numerico", lambda: _ellipsis("e a Parigi?", "GET_WEATHER",
         {"city": "Roma", "days": 3}), ("GET_WEATHER", {"city": "Parigi", "days": 3})),
    Case("ellipsis", "verbo di comando = comando nuovo", lambda: _ellipsis("e apri Spotify", "GET_WEATHER",
         {"city": "Roma"}), DECLINED),
    Case("ellipsis", "due slot testuali: ambiguo", lambda: _ellipsis("e Marco", "SEND_MESSAGE",
         {"contact": "Luca", "text": "ciao"}), DECLINED),
    Case("ellipsis", "nessun comando precedente", lambda: _ellipsis("e a Milano?", None, None), DECLINED),
    Case("ellipsis", "frase senza connettivo", lambda: _ellipsis("Milano", "GET_WEATHER", {"city": "Roma"}), DECLINED),

    # ---- pronomi e riferimenti -------------------------------------------------------------------
    Case("pronouns", "aprilo -> ultimo file", lambda: resolve_pronouns("aprilo", ENTITIES),
         "apri C:/Users/me/Documenti/relazione.docx"),
    Case("pronouns", "chiudila -> ultima app", lambda: resolve_pronouns("chiudila", ENTITIES), "chiudi Spotify"),
    Case("pronouns", "leggilo -> ultimo file", lambda: resolve_pronouns("leggilo", ENTITIES),
         "leggi il file C:/Users/me/Documenti/relazione.docx"),
    Case("pronouns", "eliminalo -> ultimo file", lambda: resolve_pronouns("eliminalo", ENTITIES),
         "elimina C:/Users/me/Documenti/relazione.docx"),
    Case("pronouns", "nessuna entita': testo invariato", lambda: resolve_pronouns("aprilo", {}), "aprilo"),
    Case("pronouns", "leggilo senza file: non inventa", lambda: resolve_pronouns("leggilo", {"app": "Spotify"}), "leggilo"),
    Case("pronouns", "frase senza pronome invariata", lambda: resolve_pronouns("apri Discord", ENTITIES), "apri Discord"),

    # ---- ordinali ----------------------------------------------------------------------------------
    Case("ordinals", "il secondo", lambda: _ordinals("apri il secondo", 5), [1]),
    Case("ordinals", "il primo e il terzo", lambda: _ordinals("apri il primo e il terzo", 5), [0, 2]),
    Case("ordinals", "l'ultimo", lambda: _ordinals("leggi l'ultimo", 4), [3]),
    Case("ordinals", "il penultimo", lambda: _ordinals("il penultimo", 4), [2]),
    Case("ordinals", "gli ultimi due", lambda: _ordinals("gli ultimi due", 5), [3, 4]),
    Case("ordinals", "tutti", lambda: _ordinals("aprili tutti", 3), [0, 1, 2]),
    Case("ordinals", "numero in cifre", lambda: _ordinals("apri il 2", 5), [1]),
    Case("ordinals", "fuori lista: chiede", lambda: _ordinals("apri il quinto", 3), DECLINED),
    Case("ordinals", "i primi tre su due: chiede", lambda: _ordinals("i primi tre", 2), DECLINED),

    # ---- correzioni ----------------------------------------------------------------------------------
    Case("corrections", "no, intendevo X", lambda: _interruption("no, intendevo Milano"), ("correction", "Milano"),
         correction=True),
    Case("corrections", "marcatori multipli", lambda: _interruption("no scusa, volevo dire apri Discord"),
         ("correction", "apri Discord"), correction=True),
    Case("corrections", "anzi", lambda: _interruption("anzi metti la playlist rock"),
         ("correction", "metti la playlist rock"), correction=True),
    Case("corrections", "inglese: I meant", lambda: _interruption("no, I meant Telegram"), ("correction", "Telegram"),
         correction=True),
    Case("corrections", "con wake word", lambda: _interruption("Jake, no, intendevo il file di ieri"),
         ("correction", "il file di ieri"), correction=True),
    Case("corrections", "correzione esplicita dell'ultimo comando",
         lambda: match_meta_command("no, intendevo apri Discord", True).intent if match_meta_command(
             "no, intendevo apri Discord", True) else None, "CORRECT_LAST", correction=True),
    Case("corrections", "correzione senza scambio precedente: niente da correggere",
         lambda: match_meta_command("no, intendevo apri Discord", False), None, correction=True),

    # ---- cambio di intento / conferma contro comando nuovo ---------------------------------------------
    Case("intent_change", "si' con conferma in sospeso", lambda: _kind("si", pending_confirmation=True),
         ReplyKind.CONFIRM_YES),
    Case("intent_change", "va bene", lambda: _kind("va bene", pending_confirmation=True), ReplyKind.CONFIRM_YES),
    Case("intent_change", "go ahead", lambda: _kind("go ahead", pending_confirmation=True), ReplyKind.CONFIRM_YES),
    Case("intent_change", "lascia perdere", lambda: _kind("lascia perdere", pending_confirmation=True),
         ReplyKind.CONFIRM_NO),
    Case("intent_change", "si' ma prima un altro comando: annulla la conferma",
         lambda: _reply("si ma prima apri Spotify", pending_confirmation=True)[0::2],
         (ReplyKind.NEW_COMMAND, True)),
    Case("intent_change", "domanda nuova durante una conferma", lambda: _reply("che ore sono?",
         pending_confirmation=True)[0::2], (ReplyKind.NEW_COMMAND, True)),
    Case("intent_change", "ok detto a un'altra cosa non conferma", lambda: _kind("ok apri la mail",
         pending_confirmation=True), ReplyKind.NEW_COMMAND),
    Case("intent_change", "chiarimento breve", lambda: _kind("Giulia", awaiting_clarification=True),
         ReplyKind.CLARIFICATION_ANSWER),
    Case("intent_change", "comando durante un chiarimento", lambda: _kind("apri Telegram", awaiting_clarification=True),
         ReplyKind.NEW_COMMAND),
    Case("intent_change", "domanda durante un chiarimento", lambda: _kind("quanto manca?", awaiting_clarification=True),
         ReplyKind.NEW_COMMAND),

    # ---- stop -----------------------------------------------------------------------------------------
    Case("stop", "basta", lambda: _interruption("basta"), ("stop", "")),
    Case("stop", "Jake, fermati", lambda: _interruption("Jake, fermati"), ("stop", "")),
    Case("stop", "no da solo", lambda: _interruption("no"), ("stop", "")),
    Case("stop", "inglese", lambda: _interruption("that's enough"), ("stop", "")),
    Case("stop", "continua non e' uno stop", lambda: _interruption("continua"), ("continue", "")),
    Case("stop", "basta come no a una conferma", lambda: _kind("basta", pending_confirmation=True),
         ReplyKind.CONFIRM_NO),

    # ---- autenticazione contro risposta normale -----------------------------------------------------------
    Case("auth", "il segreto non passa nel testo", lambda: _reply("cavallo-batteria-42", awaiting_auth=True),
         (ReplyKind.AUTH_SECRET, "", False, "cavallo-batteria-42")),
    Case("auth", "un si' durante l'auth e' il segreto, non una conferma",
         lambda: _reply("si", awaiting_auth=True, pending_confirmation=True)[0], ReplyKind.AUTH_SECRET),
    Case("auth", "un comando durante l'auth non viene interpretato",
         lambda: _reply("apri Spotify", awaiting_auth=True)[0:2], (ReplyKind.AUTH_SECRET, "")),
    Case("auth", "fuori dall'auth la stessa frase e' un comando", lambda: _kind("cavallo-batteria-42"),
         ReplyKind.NEW_COMMAND),

    # ---- numeri in lettere e cifre --------------------------------------------------------------------------
    Case("numbers", "volume al cinquanta per cento", lambda: numbers_to_digits("volume al cinquanta per cento"),
         "volume al 50 per cento"),
    Case("numbers", "numero composto", lambda: italian_number_to_int("centoventitre"), 123),
    Case("numbers", "migliaia", lambda: italian_number_to_int("tremilaquattrocento"), 3400),
    Case("numbers", "numero in piu' parole", lambda: numbers_to_digits("timer di venti due minuti"),
         "timer di 22 minuti"),
    Case("numbers", "sei verbo resta parola", lambda: numbers_to_digits("sei pronto?"), "sei pronto?"),
    Case("numbers", "sei con unita' di misura", lambda: numbers_to_digits("fra sei minuti"), "fra 6 minuti"),
    Case("numbers", "un articolo resta parola", lambda: numbers_to_digits("apri un file"), "apri un file"),
    Case("numbers", "cifre gia' in cifre", lambda: numbers_to_digits("imposta 25 gradi"), "imposta 25 gradi"),

    # ---- termini tecnici italiano + inglese -------------------------------------------------------------------
    Case("tech_terms", "sigla dettata", lambda: normalize_transcript("collega la chiavetta u s b"),
         "collega la chiavetta USB"),
    Case("tech_terms", "a/e tra parole non sono sigle", lambda: normalize_transcript("da Roma a Milano e a Napoli"),
         "da Roma a Milano e a Napoli"),
    Case("tech_terms", "parole inglesi non corrette verso i nomi", lambda: normalize_transcript(
         "fai uno screenshot e il backup della playlist", VOCABULARY), "fai uno screenshot e il backup della playlist"),
    Case("tech_terms", "code-switching con numeri", lambda: normalize_transcript("fai il download di tre file"),
         "fai il download di 3 file"),

    # ---- nomi propri -------------------------------------------------------------------------------------------
    Case("proper_names", "app storpiata", lambda: normalize_transcript("apri spotifi", VOCABULARY), "apri Spotify"),
    Case("proper_names", "app con una lettera sbagliata", lambda: normalize_transcript("scrivi su Discort", VOCABULARY),
         "scrivi su Discord"),
    Case("proper_names", "due nomi ugualmente vicini: non indovina",
         lambda: normalize_transcript("chiama Giulie", ("Giulia", "Giulio")), "chiama Giulie"),
    Case("proper_names", "parola italiana non toccata", lambda: normalize_transcript("apri la cartella", VOCABULARY),
         "apri la cartella"),
    Case("proper_names", "parola corta non toccata", lambda: normalize_transcript("chiama Mrco", VOCABULARY),
         "chiama Mrco"),
    Case("proper_names", "nome gia' corretto", lambda: normalize_transcript("apri Photoshop", VOCABULARY),
         "apri Photoshop"),

    # ---- un comando eseguito non si ripete da solo dopo una correzione ---------------------------------------------
    Case("correction_safety", "lettura eseguita: si ripete", lambda: _plan(RiskLevel.READ_ONLY, "executed"), "rerun",
         correction=True),
    Case("correction_safety", "locale reversibile: annulla poi ripeti",
         lambda: _plan(RiskLevel.LOCAL_REVERSIBLE, "executed", reversible=True), "undo_then_rerun", correction=True),
    Case("correction_safety", "locale non reversibile: chiede",
         lambda: _plan(RiskLevel.LOCAL_REVERSIBLE, "executed"), "ask", correction=True),
    Case("correction_safety", "messaggio inviato: chiede", lambda: _plan(RiskLevel.EXTERNAL_ACTION, "executed"),
         "ask", correction=True),
    Case("correction_safety", "distruttiva anche se marcata reversibile: chiede",
         lambda: _plan(RiskLevel.DESTRUCTIVE, "executed", reversible=True), "ask", correction=True),
    Case("correction_safety", "admin eseguita: chiede", lambda: _plan(RiskLevel.ADMIN, "executed"), "ask",
         correction=True),
    Case("correction_safety", "esterna annullata prima di partire: si puo' ripetere",
         lambda: _plan(RiskLevel.EXTERNAL_ACTION, "cancelled"), "rerun", correction=True),
    Case("correction_safety", "esterna fallita: si puo' ripetere", lambda: _plan(RiskLevel.EXTERNAL_ACTION, "failed"),
         "rerun", correction=True),

    # ---- scope della correzione per profilo --------------------------------------------------------------------------
    Case("correction_scope", "stesso profilo: trova il turno",
         lambda: _scoped_correction("profile:davide", "profile:davide"), ("a1", "ask"), correction=True),
    Case("correction_scope", "altro profilo: nessun turno da correggere",
         lambda: _scoped_correction("profile:davide", "profile:giulia"), (None, "unknown"), correction=True),
    Case("correction_scope", "dispositivo diverso: nessun turno",
         lambda: _scoped_correction("device:telefono", "local"), (None, "unknown"), correction=True),
    Case("correction_scope", "lo scope segue il profilo del turno", lambda: _scope_for("davide", "pc"), "profile:davide"),
    Case("correction_scope", "senza profilo vale il dispositivo", lambda: _scope_for(None, "telefono"), "device:telefono"),
    Case("correction_scope", "senza profilo ne' dispositivo", lambda: _scope_for(), "local"),
]

UNSAFE_ACTIONS = ("rerun", "undo_then_rerun")


def _outcome(case: Case, actual) -> str:
    if case.category == "correction_safety" and actual in UNSAFE_ACTIONS and case.expected == "ask":
        return "unsafe_rerun"
    if actual == case.expected or (case.expected is DECLINED and actual is DECLINED):
        declined = case.expected is DECLINED or case.expected in ("ask", "unknown", None) \
            or (isinstance(case.expected, tuple) and "unknown" in case.expected)
        return "declined" if declined else "ok"
    return "error"


def run() -> dict:
    rows = []
    for case in CASES:
        try:
            actual = case.run()
        except Exception as exc:  # un crash e' un errore del caso, non del benchmark
            actual = f"eccezione: {type(exc).__name__}: {exc}"
        outcome = _outcome(case, actual)
        rows.append({"category": case.category, "description": case.description, "outcome": outcome,
                     "correction": case.correction,
                     "expected": "DECLINED" if case.expected is DECLINED else repr(case.expected),
                     "actual": "DECLINED" if actual is DECLINED else repr(actual)})
    categories: dict[str, dict] = {}
    for row in rows:
        stats = categories.setdefault(row["category"], {"cases": 0, "ok": 0, "declined": 0, "errors": 0,
                                                        "unsafe_reruns": 0})
        stats["cases"] += 1
        key = {"ok": "ok", "declined": "declined", "error": "errors", "unsafe_rerun": "unsafe_reruns"}[row["outcome"]]
        stats[key] += 1
    return {
        "cases": len(rows),
        "first_try_correct": sum(r["outcome"] in ("ok", "declined") for r in rows),
        "correction_cases": sum(r["correction"] for r in rows),
        "corrections_handled": sum(r["correction"] and r["outcome"] in ("ok", "declined") for r in rows),
        "unsafe_reruns": sum(r["outcome"] == "unsafe_rerun" for r in rows),
        "ambiguous_declined": sum(r["outcome"] == "declined" for r in rows),
        "errors": sum(r["outcome"] == "error" for r in rows),
        "by_category": categories,
        "failures": [r for r in rows if r["outcome"] in ("error", "unsafe_rerun")],
    }


def main() -> int:
    report = run()
    for category, stats in report["by_category"].items():
        print(f"{category:<18} {stats['ok'] + stats['declined']:>2}/{stats['cases']:<2} "
              f"(declined {stats['declined']}, errori {stats['errors']}, riesecuzioni non sicure {stats['unsafe_reruns']})")
    for failure in report["failures"]:
        print(f"FAIL {failure['category']}: {failure['description']}: atteso {failure['expected']}, "
              f"ottenuto {failure['actual']}")
    print(f"\nCasi: {report['cases']}; corretti al primo tentativo: {report['first_try_correct']}; "
          f"correzioni gestite: {report['corrections_handled']}/{report['correction_cases']}; "
          f"ambigui non indovinati: {report['ambiguous_declined']}; riesecuzioni non sicure: {report['unsafe_reruns']}; "
          f"errori: {report['errors']}")
    return 0 if report["errors"] == 0 and report["unsafe_reruns"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
