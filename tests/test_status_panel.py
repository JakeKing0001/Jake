"""Test unitari per core/gui/status_panel.py con un solo interprete Tcl condiviso.

Il root Tkinter resta nascosto e viene distrutto a fine modulo; ogni test riceve una Toplevel
isolata e la distrugge in tearDown. show() e' mockato dove deiconify/lift/focus_force reali
ruberebbero il focus durante la suite.
"""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from core.gui.status_panel import StatusPanel


_TK_ROOT: tk.Tk | None = None


def setUpModule():
    """Un solo interprete Tcl per modulo; ogni test usa una finestra figlia isolata."""
    global _TK_ROOT
    _TK_ROOT = tk.Tk()
    _TK_ROOT.withdraw()


def tearDownModule():
    global _TK_ROOT
    if _TK_ROOT is not None:
        _TK_ROOT.destroy()
        _TK_ROOT = None


def _jake_core(history=None):
    core = mock.MagicMock()
    core.memory_manager.get_recent_history.return_value = history or []
    core.memory_manager.count_memories.return_value = 42
    core.conversation_state.get_short_term_history.return_value = []
    return core


class StatusPanelTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(_TK_ROOT)
        self.jake_core = _jake_core()
        self.panel = StatusPanel(self.jake_core, root=tk.Toplevel(_TK_ROOT))

    def tearDown(self):
        self.panel.root.destroy()


class InitTests(StatusPanelTestCase):
    def test_uses_the_injected_window_from_the_shared_tcl_interpreter(self):
        self.assertIsInstance(self.panel.root, tk.Toplevel)
        self.assertIs(self.panel.root.master, _TK_ROOT)

    def test_starts_hidden(self):
        self.assertEqual(self.panel.root.state(), "withdrawn")

    def test_loads_recent_history_into_the_output(self):
        jake_core = _jake_core(history=[{"role": "user", "text": "ciao"}, {"role": "assistant", "text": "ciao a te"}])
        self.assertIsNotNone(_TK_ROOT)
        panel = StatusPanel(jake_core, root=tk.Toplevel(_TK_ROOT))
        try:
            content = panel.output.get("1.0", "end")
            self.assertIn("Tu > ciao", content)
            self.assertIn("Jake > ciao a te", content)
        finally:
            panel.root.destroy()

    def test_refreshes_the_status_label_on_startup(self):
        text = self.panel.status_label.cget("text")
        self.assertIn("42", text)


class OnSendTests(StatusPanelTestCase):
    def test_empty_input_does_nothing(self):
        self.panel.entry.insert(0, "   ")
        self.panel._on_send()
        self.jake_core.answer.assert_not_called()

    def test_sends_the_typed_text_and_appends_the_response(self):
        self.jake_core.answer.return_value = "Sono le dieci."
        self.panel.entry.insert(0, "che ore sono")
        self.panel._on_send()
        self.jake_core.answer.assert_called_once_with("che ore sono")
        content = self.panel.output.get("1.0", "end")
        self.assertIn("Tu > che ore sono", content)
        self.assertIn("Jake > Sono le dieci.", content)

    def test_the_entry_is_cleared_after_sending(self):
        self.jake_core.answer.return_value = "ok"
        self.panel.entry.insert(0, "qualcosa")
        self.panel._on_send()
        self.assertEqual(self.panel.entry.get(), "")


class RefreshStatusTests(StatusPanelTestCase):
    def test_shows_memory_count_and_turn_count(self):
        self.jake_core.memory_manager.count_memories.return_value = 7
        self.jake_core.conversation_state.get_short_term_history.return_value = [1, 2, 3]
        self.panel._refresh_status()
        text = self.panel.status_label.cget("text")
        self.assertIn("7", text)
        self.assertIn("3", text)


class OpenSettingsTests(StatusPanelTestCase):
    def test_opens_the_real_settings_file_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text("{}")
            self.jake_core.skill_registry.config.path = str(settings_path)
            with mock.patch("os.startfile", create=True) as startfile:
                self.panel._open_settings()
            startfile.assert_called_once_with(str(settings_path))

    def test_falls_back_to_the_example_file_when_settings_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "settings.json"
            example_path = Path(tmp) / "settings.example.json"
            example_path.write_text("{}")
            self.jake_core.skill_registry.config.path = str(missing_path)
            with mock.patch("os.startfile", create=True) as startfile:
                self.panel._open_settings()
            startfile.assert_called_once_with(str(example_path))

    def test_does_nothing_when_neither_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "settings.json"
            self.jake_core.skill_registry.config.path = str(missing_path)
            with mock.patch("os.startfile", create=True) as startfile:
                self.panel._open_settings()
            startfile.assert_not_called()


class ShowHideTests(StatusPanelTestCase):
    def test_show_refreshes_status_and_deiconifies(self):
        with mock.patch.object(self.panel.root, "deiconify") as deiconify, \
             mock.patch.object(self.panel.root, "lift"), \
             mock.patch.object(self.panel.root, "focus_force"):
            self.panel.show()
        deiconify.assert_called_once()

    def test_hide_withdraws_the_window(self):
        with mock.patch.object(self.panel.root, "deiconify"), \
             mock.patch.object(self.panel.root, "lift"), \
             mock.patch.object(self.panel.root, "focus_force"):
            self.panel.show()
        self.panel.hide()
        self.assertEqual(self.panel.root.state(), "withdrawn")


class RunForeverTests(StatusPanelTestCase):
    def test_a_quit_command_stops_the_main_loop(self):
        import queue
        command_queue = queue.Queue()
        command_queue.put("quit")
        # Se "quit" non fermasse davvero mainloop(), questo test si bloccherebbe fino al
        # timeout della suite: il solo fatto che ritorni e' l'asserzione.
        self.panel.run_forever(command_queue)

    def test_a_show_command_calls_show(self):
        import queue
        command_queue = queue.Queue()
        command_queue.put("show")
        command_queue.put("quit")
        with mock.patch.object(self.panel, "show") as show:
            self.panel.run_forever(command_queue)
        show.assert_called_once()


if __name__ == "__main__":
    unittest.main()
