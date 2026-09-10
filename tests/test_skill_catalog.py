"""Test unitari per core/skill_catalog.py: nessuna suite esisteva finora, nonostante sia il
punto in cui SkillRegistry assembla ~180 skill built-in da 16 funzioni per dominio. Le
dipendenze pesanti (Ollama, NEST, HomeAssistant...) sono finte (MagicMock): qui non si testano
le skill stesse (hanno le proprie suite), si verifica solo l'ASSEMBLAGGIO - che ogni intent sia
registrato una volta sola, sotto la chiave giusta, con una skill che rispetta la forma minima
richiesta dal registro (metadata + execute). Nessun bug trovato: il valore e' la copertura, non
una correzione - un futuro copia-incolla che duplica una chiave di dizionario tra due domini
diversi (l'errore piu' facile in un file cosi') verrebbe individuato qui invece che a runtime."""
import inspect
import unittest
from unittest import mock

import core.skill_catalog as skill_catalog
from skills.search_files import SearchFilesSkill
from skills.web_search import WebSearchSkill


def _build_all_domains() -> dict:
    """Costruisce ogni build_*_skills() del modulo con dipendenze finte, cosi' come farebbe
    SkillRegistry.__init__ con quelle vere, e restituisce {nome_dominio: {intent: skill}}.

    search_files_skill/web_search_skill sono le uniche skill VERE, non finte: SkillRegistry le
    costruisce una volta sola e le passa a PIU' domini (build_filesystem_skills la registra
    sotto SEARCH_FILES, build_research_skills la usa come dipendenza di RESEARCH senza
    registrarla di nuovo) - un MagicMock generico qui avrebbe fatto scattare falsi positivi nei
    controlli di forma sotto (metadata non e' un dizionario vero su un Mock)."""
    fake_config = {}
    fake = mock.MagicMock()
    search_files_skill = SearchFilesSkill(nest_client=fake, conversation_state=fake)
    web_search_skill = WebSearchSkill()
    args_by_function = {
        "build_filesystem_skills": (fake, fake, search_files_skill),
        "build_web_skills": (fake_config, web_search_skill),
        "build_memory_notes_todo_skills": (fake, fake, fake),
        "build_automation_skills": (fake, fake, fake, fake, fake),
        "build_screen_input_skills": (fake,),
        "build_smart_home_skills": (fake_config,),
        "build_text_and_math_skills": (fake_config, fake),
        "build_research_skills": (fake_config, web_search_skill, search_files_skill),
        "build_communication_skills": (fake, "qwen2.5:7b", fake),
    }
    domains = {}
    for name, obj in vars(skill_catalog).items():
        if name.startswith("build_") and inspect.isfunction(obj):
            domains[name] = obj(*args_by_function.get(name, ()))
    return domains


class CatalogAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.domains = _build_all_domains()

    def test_every_domain_builder_is_exercised(self):
        # Un tetto largo, non un numero esatto: solo per accorgersi se un intero dominio
        # sparisse dal modulo (es. un import rotto che azzittisce silenziosamente la funzione).
        self.assertGreaterEqual(len(self.domains), 16)

    def test_no_intent_is_registered_in_more_than_one_domain(self):
        """L'errore piu' facile da fare copiando/incollando tra due build_*_skills(): due
        domini diversi che registrano la STESSA chiave si sovrascrivono a vicenda quando
        SkillRegistry unisce i dizionari - una skill sparirebbe in silenzio, non un errore."""
        seen = {}
        duplicates = []
        for domain_name, skills in self.domains.items():
            for intent in skills:
                if intent in seen:
                    duplicates.append((intent, seen[intent], domain_name))
                else:
                    seen[intent] = domain_name
        self.assertEqual(duplicates, [])

    def test_every_dict_key_matches_the_skill_own_declared_intent(self):
        """Se metadata['intent'] e la chiave del dizionario divergono, chi legge la skill dal
        registro (per intent) e chi ne legge i metadati (per lo stesso intent, es. la
        classificazione a rischio in core/risk.py) finirebbero per parlare di due cose diverse."""
        mismatches = []
        for domain_name, skills in self.domains.items():
            for key, skill in skills.items():
                declared = getattr(skill, "metadata", {}).get("intent")
                if declared is not None and declared != key:
                    mismatches.append((domain_name, key, declared))
        self.assertEqual(mismatches, [])

    def test_every_skill_has_the_minimum_shape_required_by_the_registry(self):
        malformed = []
        for domain_name, skills in self.domains.items():
            for intent, skill in skills.items():
                metadata = getattr(skill, "metadata", None)
                if not isinstance(metadata, dict) or "intent" not in metadata or "description" not in metadata:
                    malformed.append((domain_name, intent, "metadata"))
                if not callable(getattr(skill, "execute", None)):
                    malformed.append((domain_name, intent, "execute"))
        self.assertEqual(malformed, [])

    def test_merging_every_domain_produces_the_expected_total_intent_count(self):
        merged = {}
        for skills in self.domains.values():
            merged.update(skills)
        # Nessuna chiave persa nell'unione rispetto alla somma dei singoli domini: se lo fosse,
        # il test sui duplicati sopra l'avrebbe gia' segnalato, questo e' un controllo incrociato.
        total_before_merge = sum(len(skills) for skills in self.domains.values())
        self.assertEqual(len(merged), total_before_merge)


if __name__ == "__main__":
    unittest.main()
