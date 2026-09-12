"""Test unitari per tools/replay_session.py: nessuna suite esisteva finora.

F1.7.5 ("rendere replay sicuro"): buco reale trovato e corretto in questa sessione -
replay_one() rieseguiva un record verbatim con SkillRegistry.execute() diretto, lo stesso
"percorso 7" (dispatcher grezzo) senza controllo di policy proprio (F1.2.1). Un record verbatim
salva i parametri ESATTI, inclusi eventuali "confirmed"/"authenticated" gia' impostati - un
DELETE_PATH gia' "confermato" nel record ma comunque fallito dopo veniva rieseguito SENZA alcun
controllo, cancellando il file per davvero (riprodotto: vedi RiskyReplayIsNowBlockedTests, che
dimostra anche cosa succedeva PRIMA del fix con la vecchia SkillRegistry.execute() diretta)."""
import tempfile
import unittest
from pathlib import Path

from core.policy_engine import PolicyDecision, PolicyEngine
from core.skill_registry import SkillRegistry
from tools.replay_session import _describe_parameters, load_records, replay_one


class LoadRecordsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "sessions.jsonl"

    def _write_lines(self, *records: dict) -> None:
        import json

        self.path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    def test_missing_file_returns_an_empty_list(self):
        self.assertEqual(load_records(self.path), [])

    def test_all_records_are_returned_without_a_filter(self):
        self._write_lines({"intent": "A", "trace_id": "t1"}, {"intent": "B", "trace_id": "t2"})
        self.assertEqual(len(load_records(self.path)), 2)

    def test_filters_by_intent(self):
        self._write_lines({"intent": "A", "trace_id": "t1"}, {"intent": "B", "trace_id": "t2"})
        records = load_records(self.path, intent="A")
        self.assertEqual([r["trace_id"] for r in records], ["t1"])

    def test_filters_by_trace_id(self):
        self._write_lines({"intent": "A", "trace_id": "t1"}, {"intent": "A", "trace_id": "t2"})
        records = load_records(self.path, trace_id="t2")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["trace_id"], "t2")

    def test_a_corrupted_line_is_skipped_not_fatal(self):
        self.path.write_text('{"intent": "A", "trace_id": "t1"}\nnon e' + "' " + 'json valido', encoding="utf-8")
        records = load_records(self.path)
        self.assertEqual(len(records), 1)


class DescribeParametersTests(unittest.TestCase):
    def test_verbatim_shows_the_real_values(self):
        result = _describe_parameters({"path": "C:\\segreto.txt"}, verbatim=True)
        self.assertIn("segreto.txt", result)

    def test_non_verbatim_is_still_a_valid_json_shape(self):
        import json

        result = _describe_parameters({"path": "<str:15 caratteri>"}, verbatim=False)
        self.assertEqual(json.loads(result), {"path": "<str:15 caratteri>"})


class _AllowAllPolicyEngine(PolicyEngine):
    """PolicyEngine di test che permette sempre - isola i test di replay_one() dalla vera
    classificazione di rischio, per verificare SOLO il comportamento di replay_one() stesso."""

    def decide_automated(self, intent):
        return PolicyDecision.ALLOW


class _BlockAllPolicyEngine(PolicyEngine):
    def decide_automated(self, intent):
        return PolicyDecision.CONFIRM


class ReplayOneTests(unittest.TestCase):
    def test_unknown_intent_is_reported_honestly(self):
        result = replay_one({"intent": "NON_ESISTE", "parameters": {}}, policy_engine=_AllowAllPolicyEngine())
        self.assertIn("UNKNOWN_INTENT", result)

    def test_a_now_successful_replay_is_reported_as_resolved(self):
        result = replay_one(
            {"intent": "GET_TIME", "parameters": {}, "error": "qualche_errore_vecchio"},
            policy_engine=_AllowAllPolicyEngine(),
        )
        self.assertIn("RISOLTO", result)

    def test_a_still_failing_replay_with_the_same_error_is_reported_as_still_present(self):
        result = replay_one(
            {
                "intent": "DELETE_TODO",
                "parameters": {"text": "compito-inesistente-xyz-123", "confirmed": True},
                "error": "NOT_FOUND",
            },
            policy_engine=_AllowAllPolicyEngine(),
        )
        self.assertIn("ANCORA PRESENTE", result)


class RiskyReplayIsNowBlockedTests(unittest.TestCase):
    """Il buco reale (F1.7.5): un intent DESTRUCTIVE/ADMIN gia' "confermato" nel record verbatim
    non deve piu' essere rieseguito automaticamente, perche' nessun utente e' presente per una
    nuova conferma durante un replay batch."""

    def test_a_policy_confirm_verdict_refuses_to_execute(self):
        result = replay_one(
            {"intent": "DELETE_TODO", "parameters": {"id": 1, "confirmed": True}, "error": "qualche_errore"},
            policy_engine=_BlockAllPolicyEngine(),
        )
        self.assertIn("NON RIESEGUITO", result)
        self.assertIn("confirm", result)

    def test_a_real_confirmed_destructive_intent_deletes_nothing_with_the_real_policy_engine(self):
        """Non un mock: SkillRegistry() e PolicyEngine() veri, un file reale sul disco. Prima del
        fix, un DELETE_PATH gia' 'confermato' nel record (plausibile: l'azione era stata
        confermata ma e' comunque fallita dopo, es. file bloccato) veniva rieseguito SENZA alcun
        controllo, cancellando il file per davvero - riprodotto durante lo sviluppo di questo
        fix con la vecchia implementazione (SkillRegistry.execute() diretto)."""
        tmp = Path(tempfile.mktemp())
        tmp.write_text("non deve essere cancellato dal replay", encoding="utf-8")
        self.addCleanup(lambda: tmp.unlink(missing_ok=True))

        record = {
            "intent": "DELETE_PATH",
            "parameters": {"path": str(tmp), "confirmed": True},
            "error": "OPERATION_FAILED",
        }
        result = replay_one(record)  # nessun policy_engine iniettato: usa quello vero di produzione

        self.assertTrue(tmp.exists(), "il file non deve essere cancellato da un replay automatico")
        self.assertNotEqual(result, "RISOLTO: ora ha successo")

    def test_a_read_only_intent_is_still_actually_replayed(self):
        """Non tutto deve fermarsi: un intent senza effetti (READ_ONLY) resta rieseguibile per
        davvero, altrimenti lo strumento smetterebbe di essere utile per il suo scopo originale."""
        result = replay_one({"intent": "GET_TIME", "parameters": {}, "error": "qualche_errore"})
        self.assertNotIn("NON RIESEGUITO", result)

    def test_authorization_signals_already_in_the_record_do_not_survive_into_the_re_execution(self):
        """Anche quando la policy centrale permetterebbe (self-confirming, vedi
        core/risk.py::SELF_CONFIRMING_INTENTS), un "confirmed": true gia' presente nel record non
        deve raggiungere la skill: senza questo, il gate interno della skill stessa verrebbe
        aggirato dal valore vecchio invece di richiedere una conferma fresca."""
        registry = SkillRegistry()
        skill = registry.get_skill("DELETE_TODO")
        original_execute = skill.execute
        seen_parameters = {}

        def _spy(parameters=None):
            seen_parameters.update(parameters or {})
            return original_execute(parameters)

        skill.execute = _spy
        try:
            replay_one(
                {"intent": "DELETE_TODO", "parameters": {"id": 1, "confirmed": True}, "error": "x"},
                policy_engine=_AllowAllPolicyEngine(), registry=registry,
            )
        finally:
            skill.execute = original_execute

        self.assertNotIn("confirmed", seen_parameters)


if __name__ == "__main__":
    unittest.main()
