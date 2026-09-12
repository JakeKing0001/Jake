"""Test unitari per core/gui/hud/app.py::JarvisApp: nessuna suite esisteva finora, nessun bug
trovato.

ATTENZIONE alla sicurezza dei test: costruire un JarvisApp reale farebbe apparire per davvero
un'icona nella system tray di Windows (QSystemTrayIcon.show()) e registrerebbe per davvero un
hotkey globale (Ctrl+Shift+J di default) sulla tastiera dell'utente - entrambi effetti visibili/
attivi sul sistema reale durante la suite. Percio' QSystemTrayIcon e' SEMPRE sostituita con una
classe finta (core.gui.hud.app.QSystemTrayIcon patchato) e 'keyboard' e' SEMPRE mockato via
sys.modules in ogni test che costruisce un JarvisApp. run() (avvia un vero QApplication.exec()
bloccante) non e' mai chiamato."""
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from core.gui.hud.app import JarvisApp, _build_tray_icon

_app = QApplication.instance() or QApplication([])


def _fake_core():
    core = mock.MagicMock()
    core.EXIT_SENTINEL = "ESCI"
    core.skill_registry.skills = {}
    core.memory_manager.get_recent_history.return_value = []
    core.learning.list_taught.return_value = []
    core.reminder_manager.list_upcoming.return_value = []
    core.skill_registry.todo_manager.list_pending.return_value = []
    core.session_hooks = None
    return core


def _jarvis_app(session=None, **kwargs):
    fake_tray_class = mock.MagicMock()
    with mock.patch("core.gui.hud.app.QSystemTrayIcon", fake_tray_class):
        with mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()}):
            core = kwargs.pop("core", None) or _fake_core()
            app = JarvisApp(core, session=session, **kwargs)
    return app


class BuildTrayIconTests(unittest.TestCase):
    def test_returns_a_non_null_icon(self):
        icon = _build_tray_icon()
        self.assertFalse(icon.isNull())


class InitTests(unittest.TestCase):
    def test_builds_a_real_hud_overlay(self):
        app = _jarvis_app()
        self.assertEqual(app.hud.mode, "full")

    def test_the_hotkey_label_is_capitalized_and_joined(self):
        app = _jarvis_app()
        self.assertEqual(app.hotkey_label, "Ctrl+Shift+J")

    def test_without_a_session_the_scheduler_hook_is_wired(self):
        core = _fake_core()
        app = _jarvis_app(core=core)
        self.assertEqual(core.scheduler.on_due, app._on_reminder_due_text)

    def test_with_a_session_its_callbacks_are_wired_instead(self):
        session = mock.MagicMock()
        _jarvis_app(session=session)
        self.assertIsNotNone(session.on_state)
        self.assertIsNotNone(session.on_level)

    def test_loads_recent_history_into_the_hud(self):
        core = _fake_core()
        core.memory_manager.get_recent_history.return_value = [{"role": "user", "text": "ciao"}]
        app = _jarvis_app(core=core)
        self.assertEqual(app.hud.conversation.list.count(), 1)


class SnapshotTests(unittest.TestCase):
    def test_includes_the_skill_count(self):
        core = _fake_core()
        core.skill_registry.skills = {"a": 1, "b": 2}
        app = _jarvis_app(core=core)
        snapshot = app._snapshot()
        self.assertEqual(snapshot["skills"], 2)

    def test_a_failing_desktop_context_does_not_crash_the_snapshot(self):
        core = _fake_core()
        core.desktop_context.get_current_window.side_effect = RuntimeError("boom")
        app = _jarvis_app(core=core)
        snapshot = app._snapshot()
        self.assertIsNone(snapshot["window"])

    def test_a_failing_reminder_manager_falls_back_to_empty_lists(self):
        core = _fake_core()
        core.reminder_manager.list_upcoming.side_effect = RuntimeError("boom")
        app = _jarvis_app(core=core)
        snapshot = app._snapshot()
        self.assertEqual(snapshot["timers"], [])
        self.assertEqual(snapshot["reminders"], [])

    def test_a_failing_learning_module_falls_back_to_an_empty_list(self):
        core = _fake_core()
        core.learning.list_taught.side_effect = RuntimeError("boom")
        app = _jarvis_app(core=core)
        snapshot = app._snapshot()
        self.assertEqual(snapshot["learned"], [])


class RefreshQuickActionsTests(unittest.TestCase):
    def test_uses_the_default_actions_without_customization(self):
        app = _jarvis_app()
        app._refresh_quick_actions()
        self.assertGreater(app.hud.quick_actions.flow.count(), 0)

    def test_uses_custom_actions_when_provided(self):
        core = _fake_core()
        app = _jarvis_app(core=core, quick_actions=[("Una cosa", "fai una cosa")])
        self.assertEqual(app.hud.quick_actions.flow.count(), 1)

    def test_taught_examples_are_appended_and_starred(self):
        core = _fake_core()
        taught = mock.MagicMock()
        taught.text = "apri il mio editor preferito"
        core.learning.list_taught.return_value = [taught]
        app = _jarvis_app(core=core, quick_actions=[("Una cosa", "fai una cosa")])
        chip_texts = [app.hud.quick_actions.flow.itemAt(i).widget().text() for i in range(app.hud.quick_actions.flow.count())]
        self.assertTrue(any("apri il mio editor preferito" in text for text in chip_texts))

    def test_a_failing_learning_module_still_shows_the_base_actions(self):
        core = _fake_core()
        core.learning.list_taught.side_effect = RuntimeError("boom")
        app = _jarvis_app(core=core, quick_actions=[("Una cosa", "fai una cosa")])
        self.assertEqual(app.hud.quick_actions.flow.count(), 1)


class ToggleListeningTests(unittest.TestCase):
    def test_disabling_listening_pauses_the_session_for_a_long_time(self):
        session = mock.MagicMock()
        app = _jarvis_app(session=session)
        app._toggle_listening(False)
        session.pause_listening.assert_called_once_with(minutes=24 * 60)
        self.assertFalse(app.hud.command_bar.mic.isChecked())

    def test_enabling_listening_resumes_the_session(self):
        session = mock.MagicMock()
        app = _jarvis_app(session=session)
        app._toggle_listening(True)
        session.resume_listening.assert_called_once()

    def test_without_a_session_it_only_updates_the_hud(self):
        app = _jarvis_app(session=None)
        app._toggle_listening(False)  # non deve sollevare in assenza di sessione


class OnOrbClickedTests(unittest.TestCase):
    def test_without_a_session_it_opens_the_hud_in_input_mode(self):
        app = _jarvis_app(session=None)
        with mock.patch.object(app.hud, "show_hud") as show_hud:
            app._on_orb_clicked()
        show_hud.assert_called_once_with(input_mode=True)

    def test_with_a_session_it_arms_listening_and_shows_the_hud(self):
        session = mock.MagicMock()
        app = _jarvis_app(session=session)
        with mock.patch.object(app.hud, "show_hud") as show_hud:
            app._on_orb_clicked()
        session.arm_listening.assert_called_once()
        show_hud.assert_called_once_with()

    def test_re_enables_listening_first_if_it_was_disabled(self):
        session = mock.MagicMock()
        app = _jarvis_app(session=session)
        app._listening_enabled = False
        with mock.patch.object(app.hud, "show_hud"):
            app._on_orb_clicked()
        session.resume_listening.assert_called_once()


class OnHotkeyTests(unittest.TestCase):
    def test_shows_the_hud_in_input_mode_when_hidden(self):
        app = _jarvis_app()
        with mock.patch.object(app.hud, "isVisible", return_value=False):
            with mock.patch.object(app.hud, "show_hud") as show_hud:
                app._on_hotkey()
        show_hud.assert_called_once_with(input_mode=True)

    def test_hides_the_hud_when_already_visible_in_input_mode(self):
        app = _jarvis_app()
        app.hud.input_mode = True
        with mock.patch.object(app.hud, "isVisible", return_value=True):
            with mock.patch.object(app.hud, "hide_hud") as hide_hud:
                app._on_hotkey()
        hide_hud.assert_called_once()


class OnStateTests(unittest.TestCase):
    def test_exit_state_quits_the_app(self):
        app = _jarvis_app()
        with mock.patch.object(app, "quit") as quit_method:
            app._on_state("exit", "")
        quit_method.assert_called_once()

    def test_listening_state_clears_the_transcript_and_shows_the_hud(self):
        app = _jarvis_app()
        with mock.patch.object(app.hud, "show_hud") as show_hud:
            app._on_state("listening", "")
        show_hud.assert_called_once()
        self.assertEqual(app.hud.state, "listening")

    def test_thinking_state_adds_a_user_turn(self):
        app = _jarvis_app()
        with mock.patch.object(app.hud, "show_hud"):
            app._on_state("thinking", "che ore sono")
        self.assertEqual(app.hud.conversation.list.count(), 1)

    def test_responding_state_adds_a_jake_turn_and_refreshes_actions(self):
        app = _jarvis_app()
        with mock.patch.object(app.hud, "show_hud"), mock.patch.object(app, "_refresh_quick_actions") as refresh:
            app._on_state("responding", "Sono le dieci.")
        self.assertEqual(app.hud.conversation.list.count(), 1)
        refresh.assert_called_once()

    def test_speaking_state_is_ignored_when_the_hud_is_hidden(self):
        app = _jarvis_app()
        with mock.patch.object(app.hud, "isVisible", return_value=False):
            app._on_state("speaking", "")
        self.assertEqual(app.hud.state, "idle")

    def test_speaking_state_updates_when_the_hud_is_visible(self):
        app = _jarvis_app()
        with mock.patch.object(app.hud, "isVisible", return_value=True):
            app._on_state("speaking", "")
        self.assertEqual(app.hud.state, "speaking")

    def test_notify_state_shows_a_tray_message(self):
        app = _jarvis_app()
        with mock.patch.object(app.hud, "show_hud"):
            app._on_state("notify", "Promemoria: chiama il dentista")
        app.tray.showMessage.assert_called_once()

    def test_idle_state_schedules_a_short_hide_without_a_response(self):
        app = _jarvis_app()
        app.hud.input_mode = False
        with mock.patch.object(app.hud, "isVisible", return_value=True):
            with mock.patch.object(app.hud, "schedule_hide") as schedule_hide:
                app._on_state("idle", "")
        schedule_hide.assert_called_once_with(1.5)

    def test_idle_state_does_nothing_while_in_input_mode(self):
        app = _jarvis_app()
        app.hud.input_mode = True
        with mock.patch.object(app.hud, "isVisible", return_value=True):
            with mock.patch.object(app.hud, "schedule_hide") as schedule_hide:
                app._on_state("idle", "")
        schedule_hide.assert_not_called()


class _SynchronousThread:
    """Sostituisce threading.Thread per far girare il worker nello stesso thread del test, in
    modo deterministico: sender e receiver del segnale Qt restano cosi' nello stesso thread,
    quindi Qt consegna il segnale in modo diretto e sincrono, senza bisogno di un vero event
    loop o di attese/join su un vero thread in background."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class TextCommandTests(unittest.TestCase):
    def test_on_text_command_runs_the_core_and_emits_the_response(self):
        core = _fake_core()
        core.answer.return_value = "Sono le dieci."
        app = _jarvis_app(core=core)
        received = []
        app.bridge.response.connect(lambda text, response: received.append((text, response)))
        with mock.patch.object(app.hud, "show_hud"):
            with mock.patch("threading.Thread", _SynchronousThread):
                app._on_text_command("che ore sono")
        self.assertEqual(received, [("che ore sono", "Sono le dieci.")])

    def test_a_failing_core_answer_reports_a_generic_error(self):
        core = _fake_core()
        core.answer.side_effect = RuntimeError("boom")
        app = _jarvis_app(core=core)
        received = []
        app.bridge.response.connect(lambda text, response: received.append((text, response)))
        with mock.patch.object(app.hud, "show_hud"):
            with mock.patch("threading.Thread", _SynchronousThread):
                app._on_text_command("comando che fallisce")
        self.assertEqual(received, [("comando che fallisce", "Si è verificato un errore.")])

    def test_on_text_response_with_the_exit_sentinel_quits(self):
        app = _jarvis_app()
        with mock.patch.object(app, "quit") as quit_method:
            app._on_text_response("esci", app.core.EXIT_SENTINEL)
        quit_method.assert_called_once()

    def test_on_text_response_speaks_through_the_session_when_present(self):
        session = mock.MagicMock()
        app = _jarvis_app(session=session)
        with mock.patch.object(app, "_refresh_quick_actions"):
            app._on_text_response("che ore sono", "Sono le dieci.")
        session.speak.assert_called_once_with("Sono le dieci.")

    def test_on_text_response_in_input_mode_refocuses_the_command_bar(self):
        app = _jarvis_app()
        app.hud.input_mode = True
        with mock.patch.object(app, "_refresh_quick_actions"):
            with mock.patch.object(app.hud.command_bar.input, "setFocus") as set_focus:
                app._on_text_response("che ore sono", "Sono le dieci.")
        set_focus.assert_called_once()


class OpenSettingsAndCleanupTests(unittest.TestCase):
    def test_open_settings_starts_the_config_file(self):
        core = _fake_core()
        core.skill_registry.config.path = "C:\\settings.json"
        app = _jarvis_app(core=core)
        with mock.patch("os.startfile", create=True) as startfile:
            app._open_settings()
        startfile.assert_called_once_with("C:\\settings.json")

    def test_open_settings_swallows_an_os_error(self):
        core = _fake_core()
        core.skill_registry.config.path = "C:\\settings.json"
        app = _jarvis_app(core=core)
        with mock.patch("os.startfile", side_effect=OSError("boom"), create=True):
            app._open_settings()  # non deve sollevare

    def test_cleanup_stops_the_session_and_the_tray(self):
        session = mock.MagicMock()
        app = _jarvis_app(session=session)
        with mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()}):
            app._cleanup()
        session.stop.assert_called_once()
        app.tray.hide.assert_called_once()

    def test_a_failing_core_shutdown_is_logged_not_silently_swallowed(self):
        """F1.8.4 (stesso principio gia' applicato a JakeCore.shutdown/TaskAgent.on_step in
        questa sessione): un fallimento durante la chiusura del HUD non deve sparire senza
        log."""
        core = _fake_core()
        core.shutdown.side_effect = RuntimeError("boom")
        app = _jarvis_app(core=core)
        app.logger = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()}):
            app._cleanup()  # non deve sollevare
        app.logger.exception.assert_called_once()
        app.tray.hide.assert_called_once(), "il resto della pulizia deve comunque completarsi"

    def test_a_failing_session_stop_does_not_prevent_core_shutdown(self):
        session = mock.MagicMock()
        session.stop.side_effect = RuntimeError("boom")
        core = _fake_core()
        app = _jarvis_app(session=session, core=core)
        app.logger = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()}):
            app._cleanup()  # non deve sollevare
        core.shutdown.assert_called_once()
        app.logger.exception.assert_called_once()

    def test_quit_calls_app_quit(self):
        app = _jarvis_app()
        with mock.patch.object(app.app, "quit") as quit_method:
            app.quit()
        quit_method.assert_called_once()


if __name__ == "__main__":
    unittest.main()
