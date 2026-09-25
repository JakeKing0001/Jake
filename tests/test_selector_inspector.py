"""F3.3.6 inspector e F3.2.2 cache nel percorso runtime: logica pura su alberi costruiti a mano, piu'
il collegamento a ComputerAgent (diagnosi allegata a NOT_FOUND/AMBIGUOUS, cache invalidata dopo
ogni azione)."""
import unittest
from unittest import mock

from core.computer_agent import ComputerAgent
from core.computer_use.inspector import inspect
from core.computer_use.selector import ElementSelector, NoMatchError
from core.computer_use.uia_cache import TreeCache
from core.computer_use.ui_automation_adapter import ElementInfo


def _info(name, control_type="Button", automation_id="", enabled=True, children=()):
    return ElementInfo(name=name, automation_id=automation_id, control_type=control_type, bounds=(0, 0, 10, 10),
                       enabled=enabled, selected=None, toggle_state=None, focused=False, children=tuple(children))


TREE = _info("Finestra", "Window", children=[
    _info("Aggiungi", automation_id="w.add"),
    _info("Aggiungi tutto", automation_id="w.add_all"),
    _info("Salva", automation_id="w.save_a"),
    _info("Salva", automation_id="w.save_b", enabled=False),
    _info("Aggiungi", control_type="Text", automation_id="w.label"),
])


class InspectTests(unittest.TestCase):
    def test_a_unique_full_match_is_chosen_with_near_alternatives(self):
        report = inspect(TREE, ElementSelector(name="Aggiungi", control_type="Button"))
        self.assertEqual(report.verdict, "unique")
        self.assertEqual(report.chosen.automation_id, "w.add")
        alternative_ids = [a.automation_id for a in report.alternatives]
        self.assertIn("w.label", alternative_ids)  # stesso nome, ruolo diverso
        self.assertIn("w.add_all", alternative_ids)  # nome simile
        label = next(a for a in report.alternatives if a.automation_id == "w.label")
        self.assertIn("ruolo Text invece di Button", label.reason)

    def test_ambiguity_is_never_resolved_at_random(self):
        report = inspect(TREE, ElementSelector(name="Salva", control_type="Button"))
        self.assertEqual(report.verdict, "ambiguous")
        self.assertIsNone(report.chosen)
        self.assertEqual({a.automation_id for a in report.alternatives}, {"w.save_a", "w.save_b"})
        self.assertIn("automation id", report.reason)
        self.assertTrue(any("disabilitato" in a.reason for a in report.alternatives))

    def test_no_match_explains_the_closest_candidate(self):
        report = inspect(TREE, ElementSelector(name="Aggiugni", control_type="Button"))  # refuso
        self.assertEqual(report.verdict, "no_match")
        self.assertIn("'Aggiungi'", report.reason)
        self.assertGreater(report.alternatives[0].score, 0.5)


class RuntimeWiringTests(unittest.TestCase):
    def test_a_not_found_result_carries_the_inspection(self):
        adapter = mock.MagicMock()
        adapter.find_window_by_title.return_value = mock.MagicMock(CurrentNativeWindowHandle=4242)
        adapter.describe_tree.return_value = TREE
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter", return_value=adapter), \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine:
            MockEngine.return_value.wait_for_unique_element.side_effect = NoMatchError("nessuno")
            result = ComputerAgent().click_element(window_title="Finestra", name="Aggiugni", control_type="Button")
        self.assertEqual(result.error, "NOT_FOUND")
        self.assertEqual(result.inspection["verdict"], "no_match")
        self.assertEqual(result.inspection["alternatives"][0]["automation_id"], "w.add")

    def test_the_tree_is_cached_per_window_and_invalidated_after_an_action(self):
        adapter = mock.MagicMock()
        adapter.describe_tree.return_value = TREE
        window = mock.MagicMock(CurrentNativeWindowHandle=7)
        agent = ComputerAgent()
        agent.observe_window(window, adapter)
        agent.observe_window(window, adapter)
        self.assertEqual(adapter.describe_tree.call_count, 1)
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=None), mock.patch("pyautogui.click"):
            agent.click_point(5, 5)
        agent.observe_window(window, adapter)
        self.assertEqual(adapter.describe_tree.call_count, 2, "dopo un click l'albero va riletto")

    def test_the_cache_expires_even_without_events(self):
        clock = {"t": 0.0}
        adapter = mock.MagicMock()
        adapter.describe_tree.return_value = TREE
        cache = TreeCache(adapter=None, ttl_s=3.0, clock=lambda: clock["t"])
        cache.get_tree(object(), 1, adapter=adapter)
        clock["t"] = 5.0
        cache.get_tree(object(), 1, adapter=adapter)
        self.assertEqual(adapter.describe_tree.call_count, 2)
        self.assertEqual(cache.stats()["expirations"], 1)


if __name__ == "__main__":
    unittest.main()
