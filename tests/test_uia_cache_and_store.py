"""Test per core/computer_use/uia_cache.py (F3.2.2), uia_events.py (F3.2.4), process_access.py (F3.2.5)
e selector_store.py (F3.3.5). La parte logica usa finti; la parte COM lancia DAVVERO la fixture Qt
(stesso approccio di tests/test_ui_automation_adapter.py: mockare comtypes testerebbe solo il mock)."""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.computer_use.process_access import assess_control, current_process_is_elevated, process_elevation
from core.computer_use.selector import ElementSelector
from core.computer_use.selector_store import (
    DEFAULT_SIMILARITY_THRESHOLD, AppSignature, SelectorStore, app_signature_for_pid, structure_similarity,
    structure_tokens,
)
from core.computer_use.ui_automation_adapter import ElementInfo, UIAutomationAdapter
from core.computer_use.uia_cache import TreeCache
from core.computer_use.uia_events import EVENT_FOCUS, UIAEventListener

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TITLE = "Jake Computer Use Fixture"


def node(role, name="", automation_id="", children=()):
    return ElementInfo(
        name=name, automation_id=automation_id, control_type=role, bounds=(0, 0, 10, 10), enabled=True,
        selected=None, toggle_state=None, focused=False, children=tuple(children),
    )


class FakeAdapter:
    def __init__(self):
        self.calls = 0
        self.tree = node("Window", "Finestra", children=[node("Button", "OK")])

    def describe_tree(self, element, max_depth):
        self.calls += 1
        return self.tree


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TreeCacheLogicTests(unittest.TestCase):
    def setUp(self):
        self.adapter, self.clock = FakeAdapter(), Clock()
        self.cache = TreeCache(self.adapter, ttl_s=3.0, clock=self.clock)

    def test_a_second_read_within_the_ttl_is_a_hit_and_does_not_touch_com(self):
        first = self.cache.get_tree(object(), 100)
        second = self.cache.get_tree(object(), 100)
        self.assertIs(first, second)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual((self.cache.hits, self.cache.misses), (1, 1))

    def test_the_entry_expires_after_the_ttl_even_without_any_event(self):
        self.cache.get_tree(object(), 100)
        self.clock.t = 3.1
        self.cache.get_tree(object(), 100)
        self.assertEqual(self.adapter.calls, 2)
        self.assertEqual(self.cache.expirations, 1)

    def test_an_event_makes_the_entry_obsolete_before_the_ttl(self):
        self.cache.get_tree(object(), 100)
        self.cache._on_event(100, "structure")
        self.cache.get_tree(object(), 100)
        self.assertEqual(self.adapter.calls, 2)
        self.assertEqual(self.cache.stats()["events_by_kind"], {"structure": 1})

    def test_an_event_for_another_window_does_not_invalidate_this_one(self):
        self.cache.get_tree(object(), 100)
        self.cache._on_event(200, "property")
        self.cache.get_tree(object(), 100)
        self.assertEqual(self.adapter.calls, 1)

    def test_an_event_that_arrives_while_the_tree_is_being_read_makes_the_next_read_fresh(self):
        """La generazione si legge PRIMA della lettura lenta: un cambiamento durante la lettura non viene
        perso (la voce salvata porta la generazione vecchia e alla lettura dopo risulta obsoleta)."""
        adapter = self.adapter
        original = adapter.describe_tree

        def slow(element, depth):
            self.cache._on_event(100, "focus")  # arriva un evento mentre si legge
            return original(element, depth)

        adapter.describe_tree = slow
        self.cache.get_tree(object(), 100)
        adapter.describe_tree = original
        self.cache.get_tree(object(), 100)
        self.assertEqual(adapter.calls, 2)

    def test_explicit_invalidation_of_one_window_or_all(self):
        self.cache.get_tree(object(), 100)
        self.cache.get_tree(object(), 200)
        self.cache.invalidate(100)
        self.cache.get_tree(object(), 200)
        self.assertEqual(self.adapter.calls, 2)  # la 200 resta in cache
        self.cache.invalidate()
        self.cache.get_tree(object(), 200)
        self.assertEqual(self.adapter.calls, 3)

    def test_different_depths_are_different_entries(self):
        self.cache.get_tree(object(), 100, max_depth=2)
        self.cache.get_tree(object(), 100, max_depth=8)
        self.assertEqual(self.adapter.calls, 2)

    def test_watch_without_a_listener_returns_false_and_the_cache_still_works(self):
        self.assertFalse(self.cache.watch(100))
        self.assertIsNotNone(self.cache.get_tree(object(), 100))

    def test_watch_routes_listener_events_to_the_right_window(self):
        class FakeListener:
            def subscribe(self, hwnd, callback):
                self.callback = callback
                return True

        listener = FakeListener()
        cache = TreeCache(self.adapter, listener=listener, clock=self.clock)
        self.assertTrue(cache.watch(100))
        cache.get_tree(object(), 100)
        listener.callback("structure", 3)
        self.assertEqual(cache.generation(100), 1)
        cache.get_tree(object(), 100)
        self.assertEqual(self.adapter.calls, 2)

    def test_concurrent_events_and_reads_do_not_corrupt_the_counters(self):
        def hammer():
            for _ in range(200):
                self.cache._on_event(100, "property")
                self.cache.get_tree(object(), 100)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.cache.event_invalidations, 800)
        self.assertEqual(self.cache.generation(100), 800)


class StructureTokensTests(unittest.TestCase):
    BASE = node("Window", "Fixture", children=[
        node("Button", "Aggiungi"), node("Edit", "Campo", "campo_testo"),
        node("List", "Elenco", "lista", children=[node("ListItem", "uno"), node("ListItem", "due")]),
    ])

    def test_identical_trees_are_fully_similar(self):
        self.assertEqual(structure_similarity(structure_tokens(self.BASE), structure_tokens(self.BASE)), 1.0)

    def test_list_content_and_window_title_do_not_change_the_structure(self):
        changed = node("Window", "Titolo diverso", children=[
            node("Button", "Aggiungi"), node("Edit", "Altro testo", "campo_testo"),
            node("List", "Elenco", "lista", children=[node("ListItem", f"voce {i}") for i in range(20)]),
        ])
        self.assertEqual(structure_similarity(structure_tokens(self.BASE), structure_tokens(changed)), 1.0)

    def test_a_missing_or_renamed_button_lowers_the_similarity(self):
        renamed = node("Window", "Fixture", children=[
            node("Button", "Salva"), node("Edit", "Campo", "campo_testo"),
            node("List", "Elenco", "lista", children=[node("ListItem", "uno")]),
        ])
        similarity = structure_similarity(structure_tokens(self.BASE), structure_tokens(renamed))
        self.assertLess(similarity, 1.0)

    def test_a_completely_different_window_is_dissimilar(self):
        other = node("Window", "Altro", children=[node("Document", "x"), node("Hyperlink", "vai"), node("MenuBar", "m")])
        self.assertLess(structure_similarity(structure_tokens(self.BASE), structure_tokens(other)), 0.3)

    def test_the_same_button_in_two_panels_is_not_collapsed(self):
        tree = node("Window", "W", children=[
            node("Pane", "A", "pannello_a", children=[node("Button", "OK")]),
            node("Pane", "B", "pannello_b", children=[node("Button", "OK")]),
        ])
        buttons = [t for t in structure_tokens(tree) if t.endswith("Button:OK")]
        self.assertEqual(len(buttons), 2)

    def test_empty_trees(self):
        self.assertEqual(structure_similarity(structure_tokens(None), structure_tokens(None)), 1.0)
        self.assertEqual(structure_similarity(structure_tokens(None), structure_tokens(self.BASE)), 0.0)


class AppSignatureTests(unittest.TestCase):
    A = AppSignature(r"C:\Program Files\App\app.exe", "1.2.3.4", 1000)

    def test_identical_signatures_match(self):
        self.assertTrue(self.A.matches(AppSignature(r"c:\program files\app\APP.EXE", "1.2.3.4", 1000))[0])

    def test_a_new_version_a_different_size_or_a_different_exe_do_not(self):
        for other, fragment in (
            (AppSignature(self.A.executable, "1.2.4.0", 1000), "versione"),
            (AppSignature(self.A.executable, "1.2.3.4", 1001), "modificato"),
            (AppSignature(r"C:\Altro\app.exe", "1.2.3.4", 1000), "eseguibile"),
        ):
            with self.subTest(fragment=fragment):
                ok, reason = self.A.matches(other)
                self.assertFalse(ok)
                self.assertIn(fragment, reason)

    def test_the_signature_of_this_very_process_is_readable(self):
        signature = app_signature_for_pid(os.getpid())
        assert signature is not None
        self.assertTrue(signature.executable.lower().endswith(".exe"))
        self.assertGreater(signature.size, 0)
        self.assertRegex(signature.version, r"^\d+\.\d+\.\d+\.\d+$|^mtime-\d+$")

    def test_a_pid_that_does_not_exist_has_no_signature(self):
        self.assertIsNone(app_signature_for_pid(999_999_999))


class SelectorStoreTests(unittest.TestCase):
    TREE = TreeCacheLogicTests  # solo per chiarezza: l'albero sta in StructureTokensTests.BASE
    APP = AppSignature(r"C:\App\app.exe", "1.0.0.0", 500)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "selectors.json"
        self.store = SelectorStore(self.path, clock=lambda: 99.0)
        self.tree = StructureTokensTests.BASE
        self.selector = ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Fixture")

    def test_a_saved_selector_is_returned_while_it_is_valid(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        restored = self.store.selector("aggiungi")
        assert restored is not None
        self.assertEqual(restored.to_dict(), self.selector.to_dict())
        self.assertEqual(self.store.get("aggiungi").saved_at, 99.0)

    def test_nothing_is_stored_as_absolute_coordinates(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("bounds", raw)

    def test_check_passes_when_app_and_structure_are_unchanged(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        result = self.store.check("aggiungi", self.APP, self.tree)
        self.assertEqual((result.usable, result.status, result.similarity), (True, "valid", 1.0))

    def test_an_app_update_invalidates_the_selector_persistently(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        updated = AppSignature(self.APP.executable, "2.0.0.0", 600)
        result = self.store.check("aggiungi", updated, self.tree)
        self.assertEqual((result.usable, result.status), (False, "stale"))
        self.assertIn("versione", result.reason)
        self.assertIsNone(self.store.selector("aggiungi"))  # non si consegna piu'
        # ...anche con una nuova istanza dello store (persistente) e anche se poi la firma "torna" uguale
        again = SelectorStore(self.path)
        self.assertEqual(again.check("aggiungi", self.APP, self.tree).status, "stale")

    def test_a_changed_structure_invalidates_it(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        different = node("Window", "Fixture", children=[node("Document", "x"), node("Hyperlink", "vai")])
        result = self.store.check("aggiungi", self.APP, different)
        self.assertEqual(result.status, "stale")
        self.assertLess(result.similarity, DEFAULT_SIMILARITY_THRESHOLD)
        self.assertIn("struttura", result.reason)

    def test_growing_a_list_does_not_invalidate(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        grown = node("Window", "Fixture", children=[
            node("Button", "Aggiungi"), node("Edit", "Campo", "campo_testo"),
            node("List", "Elenco", "lista", children=[node("ListItem", str(i)) for i in range(50)]),
        ])
        self.assertTrue(self.store.check("aggiungi", self.APP, grown).usable)

    def test_an_unreadable_app_signature_is_not_trusted(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        self.assertEqual(self.store.check("aggiungi", None, self.tree).status, "stale")

    def test_resaving_reactivates_a_stale_selector(self):
        self.store.save("aggiungi", self.selector, self.APP, self.tree)
        updated = AppSignature(self.APP.executable, "2.0.0.0", 600)
        self.store.check("aggiungi", updated, self.tree)
        self.store.save("aggiungi", self.selector, updated, self.tree)
        self.assertEqual(self.store.check("aggiungi", updated, self.tree).status, "valid")

    def test_invalidate_app_marks_every_selector_of_that_app(self):
        other = AppSignature(r"C:\Altra\altra.exe", "1.0.0.0", 1)
        self.store.save("uno", self.selector, self.APP, self.tree)
        self.store.save("due", self.selector, self.APP, self.tree)
        self.store.save("tre", self.selector, other, self.tree)
        self.assertEqual(self.store.invalidate_app(self.APP.executable, "aggiornamento noto"), 2)
        self.assertEqual(self.store.names(only_valid=True), ["tre"])
        self.assertEqual(self.store.get("uno").stale_reason, "aggiornamento noto")

    def test_missing_selector_and_delete(self):
        self.assertEqual(self.store.check("mai-salvato", self.APP, self.tree).status, "missing")
        self.store.save("x", self.selector, self.APP, self.tree)
        self.assertTrue(self.store.delete("x"))
        self.assertFalse(self.store.delete("x"))

    def test_a_selector_saved_without_an_app_signature_still_checks_structure(self):
        self.store.save("x", self.selector, None, self.tree)
        self.assertTrue(self.store.check("x", None, self.tree).usable)

    def test_a_corrupt_file_means_nothing_is_stored(self):
        self.path.write_text("{ rotto", encoding="utf-8")
        self.assertEqual(self.store.names(), [])
        self.assertIsNone(self.store.selector("aggiungi"))

    def test_the_file_is_valid_json_and_has_no_leftover_temp(self):
        self.store.save("x", self.selector, self.APP, self.tree)
        json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["selectors.json"])


class ProcessAccessTests(unittest.TestCase):
    def test_own_elevation_matches_the_operating_system(self):
        self.assertEqual(process_elevation(os.getpid()), bool(ctypes.windll.shell32.IsUserAnAdmin()))
        self.assertEqual(current_process_is_elevated(), bool(ctypes.windll.shell32.IsUserAnAdmin()))

    def test_a_pid_that_does_not_exist_is_not_queryable(self):
        self.assertIsNone(process_elevation(999_999_999))

    def test_the_system_process_is_either_unqueryable_or_elevated(self):
        self.assertIn(process_elevation(4), (None, True))

    def test_an_elevated_target_from_a_normal_jake_is_refused_with_the_uipi_reason(self):
        report = assess_control(1234, own_elevated=False, target_elevated=True)
        self.assertFalse(report.can_control)
        self.assertTrue(report.target_elevated)
        self.assertIn("UIPI", report.reason)
        self.assertIn("non si eleva da solo", report.reason)

    def test_two_normal_processes_can_be_controlled(self):
        self.assertTrue(assess_control(1234, own_elevated=False, target_elevated=False).can_control)

    def test_an_elevated_jake_can_control_an_elevated_target(self):
        self.assertTrue(assess_control(1234, own_elevated=True, target_elevated=True).can_control)

    def test_an_unknown_privilege_level_is_not_assumed_safe(self):
        report = assess_control(1234, own_elevated=False, target_elevated=None)
        self.assertFalse(report.can_control)
        self.assertIsNone(report.target_elevated)

    def test_a_real_process_of_the_same_user_is_controllable(self):
        self.assertEqual(assess_control(os.getpid()).can_control, True)


class _RealFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "90"], cwd=str(_REPO_ROOT),
        )
        cls.adapter = UIAutomationAdapter()
        try:
            cls.window = cls.adapter.find_window_by_title(_TITLE, timeout_seconds=15.0)
            cls.hwnd = cls.window.CurrentNativeWindowHandle
        except Exception:
            cls._stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    @classmethod
    def _stop(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait()

    def _invoke(self, name):
        from core.computer_use.ui_automation_adapter import UIA

        button = self.adapter.find_matching_elements(self.window, name=name, control_type="Button")[0]
        button.GetCurrentPattern(UIA.UIA_InvokePatternId).QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()


class RealTreeCacheTests(_RealFixture):
    def test_the_second_read_of_a_real_window_is_served_from_the_cache_and_much_faster(self):
        cache = TreeCache(self.adapter, ttl_s=30.0)
        started = time.perf_counter()
        tree = cache.get_tree(self.window, self.hwnd, max_depth=6)
        cold = time.perf_counter() - started
        started = time.perf_counter()
        again = cache.get_tree(self.window, self.hwnd, max_depth=6)
        warm = time.perf_counter() - started
        assert tree is not None
        self.assertIs(tree, again)
        self.assertLess(warm, cold / 20)  # un dict lookup contro centinaia di chiamate COM
        self.assertEqual((cache.hits, cache.misses), (1, 1))

    def test_the_real_tree_yields_a_stable_structure_across_two_reads(self):
        first = structure_tokens(self.adapter.describe_tree(self.window, 6))
        second = structure_tokens(self.adapter.describe_tree(self.window, 6))
        self.assertGreater(len(first), 10)
        self.assertEqual(structure_similarity(first, second), 1.0)

    def test_the_real_app_signature_and_a_stored_selector_survive_a_check(self):
        pid = self.window.CurrentProcessId
        signature = app_signature_for_pid(pid)
        assert signature is not None
        tree = self.adapter.describe_tree(self.window, 6)
        with tempfile.TemporaryDirectory() as tmp:
            store = SelectorStore(Path(tmp) / "s.json")
            store.save("aggiungi", ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Fixture"), signature, tree)
            self.assertTrue(store.check("aggiungi", app_signature_for_pid(pid), self.adapter.describe_tree(self.window, 6)).usable)


class RealEventListenerTests(_RealFixture):
    """Su un provider Qt arrivano gli eventi di FOCUS (misurato), non quelli di struttura/proprieta'."""

    def test_focus_events_from_the_real_app_reach_the_callback_and_invalidate_the_cache(self):
        listener = UIAEventListener()
        self.addCleanup(listener.close)
        self.assertTrue(listener.available, listener.error)
        cache = TreeCache(self.adapter, ttl_s=60.0, listener=listener)
        self.assertTrue(cache.watch(self.hwnd))
        cache.get_tree(self.window, self.hwnd, max_depth=4)
        before = cache.generation(self.hwnd)
        deadline = time.time() + 8
        while cache.generation(self.hwnd) == before and time.time() < deadline:
            self._invoke("Aggiungi")
            time.sleep(0.4)
            self._invoke("Reset")
            time.sleep(0.4)
        self.assertGreater(cache.generation(self.hwnd), before, "nessun evento arrivato dalla fixture")
        self.assertIn(EVENT_FOCUS, cache.stats()["events_by_kind"])
        misses = cache.misses
        cache.get_tree(self.window, self.hwnd, max_depth=4)
        self.assertEqual(cache.misses, misses + 1)  # l'evento ha reso obsoleta la voce prima del TTL (60 s)

    def test_unsubscribing_stops_further_events(self):
        listener = UIAEventListener()
        self.addCleanup(listener.close)
        seen = []
        self.assertTrue(listener.subscribe(self.hwnd, lambda kind, detail: seen.append(kind)))
        deadline = time.time() + 8
        while not seen and time.time() < deadline:
            self._invoke("Aggiungi")
            time.sleep(0.3)
            self._invoke("Reset")
            time.sleep(0.3)
        self.assertTrue(seen)
        self.assertTrue(listener.unsubscribe_all())
        time.sleep(0.5)
        count = len(seen)
        self._invoke("Aggiungi")
        time.sleep(0.8)
        self._invoke("Reset")
        time.sleep(0.8)
        self.assertEqual(len(seen), count)

    def test_a_broken_callback_does_not_kill_the_listener(self):
        listener = UIAEventListener()
        self.addCleanup(listener.close)

        def broken(kind, detail):
            raise RuntimeError("callback rotto")

        self.assertTrue(listener.subscribe(self.hwnd, broken))
        deadline = time.time() + 8
        while listener.events_received == 0 and time.time() < deadline:
            self._invoke("Aggiungi")
            time.sleep(0.3)
            self._invoke("Reset")
            time.sleep(0.3)
        self.assertGreater(listener.events_received, 0)
        self.assertTrue(listener.available)

    def test_subscribing_to_a_window_that_does_not_exist_fails_cleanly(self):
        listener = UIAEventListener()
        self.addCleanup(listener.close)
        self.assertFalse(listener.subscribe(0x7FFFFFFF, lambda kind, detail: None))
        self.assertTrue(listener.available)  # l'ascoltatore resta usabile

    def test_close_ends_the_thread_and_later_calls_are_refused(self):
        listener = UIAEventListener()
        thread = listener._thread
        listener.close()
        self.assertFalse(thread.is_alive())
        self.assertFalse(listener.subscribe(self.hwnd, lambda k, d: None))


if __name__ == "__main__":
    unittest.main()
