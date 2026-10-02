"""Contesto della conversazione: turni vocali incerti, riparazione del transcript, esiti del dialogo, correzioni, riferimenti ordinali e pronomi, memoria dello scambio.

Estratto da JakeCore (3.2 Reliability & Architecture): metodi spostati alla lettera, comportamento
invariato. Lavorano sullo stato di JakeCore tramite self, come prima."""
from __future__ import annotations

import copy
from core import intent_patterns
from core.action_ledger import new_action_id
from core.command import Command
from core.nlu.index import lexical_similarity
from core.nlu.transcript_repair import AMBIGUOUS, Assessment, REPAIRED, TranscriptRepair, UNCLEAR
from core.request_context import current_conversation_channel, current_speaker_profile_id, current_stt_confidence
from core.risk import RiskLevel, risk_of
from core.voice.dialogue_runtime import DialogueRuntime
from core.voice.language_normalizer import resolve_ordinals

# Meta-comandi che eseguono DENTRO di se' il vero comando (correzione, "riprova"): il turno e l'ultimo scambio
# restano quelli del comando vero, non del meta-comando che l'ha lanciato.
META_TURN_INTENTS = frozenset({"CORRECT_LAST", "RETRY_LAST_ACTION"})


class DialogueMixin:
    def _transcript_repair(self) -> TranscriptRepair:
        """Lessico dagli esempi affidabili (ricostruito solo quando gli esempi cambiano) + la conversazione recente."""
        examples = self.example_store.all()
        cached = getattr(self, "_repair_cache", None)
        if cached is None or cached[0] != len(examples):
            cached = (len(examples), TranscriptRepair.from_examples(examples))
            self._repair_cache = cached
        repair = copy.copy(cached[1])
        repair.lexicon = set(cached[1].lexicon)
        for turn in self.conversation_state.get_short_term_history():
            repair.add_context(turn.get("text", ""))
        return repair

    def _assess_voice_turn(self, text: str) -> Assessment | None:
        """Solo per i turni vocali con una confidenza reale (testo scritto e provider senza confidenza: mai). La corsia
        deterministica (esempi esatti: "che ore sono", date, calcoli) passa sempre: la' non si interpreta nulla.
        Altrimenti trascrizione corrotta (chiedere), recuperabile (correggere con prudenza) o affidabile: vedi
        core/nlu/transcript_repair.py."""
        confidence = current_stt_confidence()
        if confidence is None or self.example_store.find_exact(text) is not None:
            return None
        assessment = self._transcript_repair().assess(
            text, confidence, self.example_store.find_exact, lambda intent: risk_of(intent) == RiskLevel.READ_ONLY)
        if assessment.verdict == REPAIRED:
            self.logger.info("Trascrizione corretta (confidenza %.2f, %s): '%s' -> '%s'", confidence,
                             assessment.reason, text, assessment.text)
        elif assessment.verdict in (UNCLEAR, AMBIGUOUS):
            self.logger.info("Trascrizione incerta (%s): chiedo di ripetere invece di interpretare '%s'",
                             assessment.reason, text)
        return assessment

    def _get_dialogue_runtime(self) -> DialogueRuntime:
        """Keep dialogue state available on cores constructed without __init__."""
        runtime = getattr(self, "dialogue_runtime", None)
        if runtime is None:
            runtime = DialogueRuntime()
            self.dialogue_runtime = runtime
        return runtime

    def _dialogue_scope(self) -> str:
        """
        Scope conversazionale per F2.6.

        Un profilo vocale riconosciuto ha priorità sul dispositivo.
        """
        profile_id = current_speaker_profile_id()

        if profile_id:
            return f"profile:{profile_id}"

        # l'HUD nativo e' il PC: stesso scope della voce locale (una correzione scritta nell'HUD vale per l'ultimo
        # comando detto a voce)
        device_id = current_conversation_channel()

        if device_id:
            return f"device:{device_id}"

        return "local"

    def _set_dialogue_outcome(
        self,
        *,
        action_id: str,
        text: str,
        command: Command,
        status: str,
        reversible: bool = False,
        scope: str | None = None,
    ) -> None:
        # CORRECT_LAST è un meta-comando.
        # Il vero turno corretto viene registrato separatamente.
        if command.intent in META_TURN_INTENTS:
            return

        effective_scope = scope or self._dialogue_scope()

        existing = self._get_dialogue_runtime().turn(action_id)

        if existing is None:
            self._get_dialogue_runtime().record_turn(
                scope=effective_scope,
                action_id=action_id,
                heard=text,
                intent=command.intent,
                parameters=command.parameters or {},
                risk=risk_of(command.intent),
                status=status,
                reversible=reversible,
            )
            return

        self._get_dialogue_runtime().update_turn(
            action_id,
            status=status,
            reversible=reversible,
        )

    def _cancel_dialogue_action(self, action: dict) -> None:
        action_id = action.get("action_id")

        if not action_id:
            return

        self._get_dialogue_runtime().update_turn(
            action_id,
            status="cancelled",
        )

        self._finish_correction_learning(
            action_id,
            verified=False,
        )

    def _finish_correction_learning(
        self,
        action_id: str,
        *,
        verified: bool,
    ) -> None:
        source_turn, learnable = (
            self._get_dialogue_runtime().finish_correction_learning(
                action_id,
                verified=verified,
            )
        )

        if source_turn is None or learnable is None:
            return

        previous_command = Command(
            source_turn.intent,
            dict(source_turn.parameters),
        )

        corrected_command = Command(
            learnable.intent,
            dict(learnable.parameters),
        )

        self.learning.correct(
            learnable.heard,
            previous_command,
            corrected_command,
        )

        self.logger.info(
            "Correzione verificata: %r -> %s %s",
            learnable.heard,
            corrected_command.intent,
            corrected_command.parameters,
        )

    def _execute_corrected_command(
        self,
        source_action_id: str,
        corrected_text: str,
        command: Command,
    ) -> str:
        corrected_action_id = new_action_id()

        source_turn = self._get_dialogue_runtime().turn(source_action_id)

        # Conserva la protezione già esistente:
        # una frase totalmente diversa non deve insegnare una falsa associazione.
        related = False

        if source_turn is not None:
            related = (
                lexical_similarity(
                    source_turn.heard,
                    corrected_text,
                ) >= 0.25
                or source_turn.intent
                in ("UNKNOWN", "CHITCHAT", "ASK_QUESTION")
            )

        if related:
            self._get_dialogue_runtime().begin_correction_learning(
                source_action_id=source_action_id,
                execution_action_id=corrected_action_id,
                corrected_text=corrected_text,
                intent=command.intent,
                parameters=command.parameters or {},
            )

        return self._execute_command(
            corrected_text,
            command,
            learn=False,
            action_id=corrected_action_id,
        )

    def _what_did_you_hear(self) -> str:
        turn = self._get_dialogue_runtime().last_turn(
            self._dialogue_scope()
        )

        if turn is None:
            return "Non ho ancora un comando precedente da riportarti."

        return f'Ho sentito: "{turn.heard}".'

    def _try_ordinal_reference(self, text: str) -> str | None:
        """
        F2.6.1:
        'apri il primo e il terzo' sui risultati dell'ultima ricerca.
        """
        first_word = (
            text.split(maxsplit=1)[0]
            if text.strip()
            else ""
        )

        if first_word not in {
            "apri",
            "aprimi",
            "mostra",
            "mostrami",
        }:
            return None

        results = self.conversation_state.get_last_search_results()

        if not results:
            return None

        indexes = resolve_ordinals(
            text,
            len(results),
        )

        if indexes is None:
            return None

        responses: list[str] = []

        for index in indexes:
            response = self._execute_command(
                text,
                Command(
                    "OPEN_SEARCH_RESULT",
                    {"index": index + 1},
                ),
                learn=False,
            )

            responses.append(response)

        return "\n".join(
            response
            for response in responses
            if response
        )

    def _resolve_pronouns(self, text: str) -> str:
        return intent_patterns.resolve_pronouns(text, self.conversation_state.get_entities())

    def _remember_exchange(
        self,
        text: str,
        command: Command,
        response: str,
        *,
        action_id: str | None = None,
    ) -> None:
        if action_id is None:
            # Anche UNKNOWN/chitchat/agente devono sostituire l'ultimo turno:
            # correggere una frase non capita non deve correggere un'azione piu' vecchia.
            action_id = new_action_id()
            self._set_dialogue_outcome(
                action_id=action_id, text=text, command=command,
                status="failed" if command.intent == "UNKNOWN" else "executed",
            )
        self.last_exchange = {
            "text": text,
            "command": command,
            "response": response,
            "action_id": action_id,
        }
