"""Test unitari per core/gui/hud/overlay.py: nessuna suite esisteva finora, nessun bug trovato.

ATTENZIONE alla sicurezza dei test: HudOverlay e' una finestra REALE, senza bordi, sempre in
primo piano, grande quanto lo schermo primario. Costruirla (QWidget.__init__) non la rende mai
visibile di per se' (Qt non mostra un widget finche' non si chiama .show()), quindi e' sicuro
crearne istanze qui. Ma show_hud() chiamerebbe .show() per davvero E applicherebbe stili nativi
Windows reali (win_effects, incluso disattivare il click-through) su un HWND vero: se lasciato
correre per davvero durante la suite, apparirebbe per un istante una finestra vera, sempre in
primo piano, sopra lo schermo reale dell'utente. Percio' in OGNI test che tocca show_hud/
hide_hud/_apply_native_effects, self.show()/win_effects sono sempre mockati; self.isVisible()
e' mockato dove serve simulare uno stato "gia' visibile" senza mai mostrare nulla per davvero."""
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from core.gui.hud.overlay import HudOverlay, PillPanel

_app = QApplication.instance() or QApplication([])


def _overlay(**kwargs):
    with mock.patch("core.gui.hud.overlay.win_effects"):
        return HudOverlay(**kwargs)


class PillPanelTests(unittest.TestCase):
    def test_start_and_stop_control_orb_and_waveform_timers(self):
        pill = PillPanel(backdrop=None)
        pill.start()
        self.assertTrue(pill.orb._timer.isActive())
        self.assertTrue(pill.waveform._timer.isActive())
        pill.stop()
        self.assertFalse(pill.orb._timer.isActive())
        self.assertFalse(pill.waveform._timer.isActive())


class InitTests(unittest.TestCase):
    def test_defaults_to_full_mode_and_clear_backdrop(self):
        overlay = _overlay()
        self.assertEqual(overlay.mode, "full")
        self.assertEqual(overlay.backdrop_mode, "clear")
        self.assertEqual(overlay.state, "idle")

    def test_an_unknown_mode_falls_back_to_full(self):
        overlay = _overlay(mode="qualcosa_di_strano")
        self.assertEqual(overlay.mode, "full")

    def test_compact_mode_is_recognized(self):
        overlay = _overlay(mode="compact")
        self.assertEqual(overlay.mode, "compact")

    def test_immersive_backdrop_is_recognized(self):
        overlay = _overlay(backdrop_mode="immersive")
        self.assertEqual(overlay.backdrop_mode, "immersive")

    def test_starts_neither_pinned_nor_in_input_mode(self):
        overlay = _overlay()
        self.assertFalse(overlay.pinned)
        self.assertFalse(overlay.input_mode)


class ModeVisibilityTests(unittest.TestCase):
    # isVisible() riflette la visibilita' EFFETTIVA (richiede l'intera catena di antenati visibile
    # sullo schermo reale, che qui non lo e' mai di proposito): isVisibleTo(overlay) verifica
    # invece il flag di visibilita' relativo al genitore diretto, quello che _apply_mode_visibility()
    # imposta davvero con setVisible().
    def test_full_mode_shows_the_full_panels_and_hides_the_pill(self):
        overlay = _overlay(mode="full")
        self.assertTrue(overlay.top_bar.isVisibleTo(overlay))
        self.assertTrue(overlay.stage.isVisibleTo(overlay))
        self.assertFalse(overlay.pill.isVisibleTo(overlay))

    def test_compact_mode_shows_only_the_pill(self):
        overlay = _overlay(mode="compact")
        self.assertFalse(overlay.top_bar.isVisibleTo(overlay))
        self.assertFalse(overlay.stage.isVisibleTo(overlay))
        self.assertTrue(overlay.pill.isVisibleTo(overlay))

    def test_set_mode_switches_visibility(self):
        overlay = _overlay(mode="full")
        overlay.set_mode("compact")
        self.assertFalse(overlay.stage.isVisibleTo(overlay))
        self.assertTrue(overlay.pill.isVisibleTo(overlay))


class LayoutTests(unittest.TestCase):
    def test_panels_get_a_positive_geometry_after_resizing(self):
        overlay = _overlay()
        overlay.resize(1600, 900)
        for panel in [overlay.top_bar, overlay.conversation, overlay.context_panel, overlay.stage,
                      overlay.quick_actions, overlay.command_bar, overlay.pill]:
            self.assertGreater(panel.width(), 0)
            self.assertGreater(panel.height(), 0)


class UpdateMaskTests(unittest.TestCase):
    def test_immersive_backdrop_clears_the_mask(self):
        overlay = _overlay(backdrop_mode="immersive")
        with mock.patch.object(overlay, "clearMask") as clear_mask:
            overlay._update_mask()
        clear_mask.assert_called_once()

    def test_clear_backdrop_sets_a_mask_without_raising(self):
        overlay = _overlay(backdrop_mode="clear")
        overlay.resize(1600, 900)
        overlay._update_mask()  # non deve sollevare eccezioni


class ApplyNativeEffectsTests(unittest.TestCase):
    def test_clear_mode_does_not_apply_acrylic(self):
        overlay = _overlay(backdrop_mode="clear")
        with mock.patch("core.gui.hud.overlay.win_effects") as win_effects:
            overlay._apply_native_effects()
        win_effects.apply_acrylic.assert_not_called()
        win_effects.set_click_through.assert_called_once_with(mock.ANY, False)

    def test_immersive_mode_applies_acrylic(self):
        overlay = _overlay(backdrop_mode="immersive")
        with mock.patch("core.gui.hud.overlay.win_effects") as win_effects:
            overlay._apply_native_effects()
        win_effects.apply_acrylic.assert_called_once()

    def test_input_mode_disables_no_activate(self):
        overlay = _overlay()
        overlay.input_mode = True
        with mock.patch("core.gui.hud.overlay.win_effects") as win_effects:
            overlay._apply_native_effects()
        win_effects.set_no_activate.assert_called_once_with(mock.ANY, False)


class ShowHideTests(unittest.TestCase):
    def _overlay_ready_to_show(self, **kwargs):
        overlay = _overlay(**kwargs)
        overlay.show = mock.MagicMock()
        overlay.backdrop.refresh = mock.MagicMock()
        overlay._apply_native_effects = mock.MagicMock()
        overlay._opacity_anim.start = mock.MagicMock()
        overlay.refresh_snapshot = mock.MagicMock()
        return overlay

    def test_show_hud_marks_the_overlay_as_visible_and_refreshes_the_backdrop(self):
        overlay = self._overlay_ready_to_show()
        overlay.show_hud()
        self.assertTrue(overlay._visible_target)
        overlay.backdrop.refresh.assert_called_once()
        overlay.show.assert_called_once()
        overlay._apply_native_effects.assert_called_once()

    def test_show_hud_with_input_mode_focuses_the_command_bar(self):
        overlay = self._overlay_ready_to_show()
        with mock.patch.object(overlay, "raise_"), mock.patch.object(overlay, "activateWindow"):
            with mock.patch.object(overlay.command_bar.input, "setFocus") as set_focus:
                overlay.show_hud(input_mode=True)
        self.assertTrue(overlay.input_mode)
        set_focus.assert_called_once()

    def test_show_hud_switches_mode_when_requested(self):
        overlay = self._overlay_ready_to_show(mode="full")
        overlay.show_hud(mode="compact")
        self.assertEqual(overlay.mode, "compact")

    def test_hide_hud_does_nothing_when_not_visible(self):
        overlay = _overlay()
        with mock.patch.object(overlay, "isVisible", return_value=False):
            overlay._opacity_anim.start = mock.MagicMock()
            overlay.hide_hud()
        overlay._opacity_anim.start.assert_not_called()

    def test_hide_hud_starts_the_fade_out_when_visible(self):
        overlay = _overlay()
        overlay._visible_target = True
        with mock.patch.object(overlay, "isVisible", return_value=True):
            overlay._opacity_anim.start = mock.MagicMock()
            overlay.hide_hud()
        self.assertFalse(overlay._visible_target)
        overlay._opacity_anim.start.assert_called_once()

    def test_finish_hide_resets_transcript_and_response_when_fully_hidden(self):
        overlay = _overlay()
        overlay._visible_target = False
        overlay.stage.set_transcript("qualcosa")
        overlay.pill.transcript.setText("qualcosa")
        with mock.patch.object(overlay, "windowOpacity", return_value=0.0):
            with mock.patch.object(overlay, "hide") as hide:
                overlay._finish_hide()
        hide.assert_called_once()
        self.assertEqual(overlay.stage.transcript.text(), "")
        self.assertEqual(overlay.pill.transcript.text(), "")

    def test_finish_hide_does_nothing_while_still_fading_or_re_shown(self):
        overlay = _overlay()
        overlay._visible_target = True
        with mock.patch.object(overlay, "windowOpacity", return_value=0.0):
            with mock.patch.object(overlay, "hide") as hide:
                overlay._finish_hide()
        hide.assert_not_called()


class PinAndAutoHideTests(unittest.TestCase):
    def test_set_pinned_stops_the_hide_timer(self):
        overlay = _overlay()
        with mock.patch.object(overlay._hide_timer, "stop") as stop:
            overlay._set_pinned(True)
        self.assertTrue(overlay.pinned)
        stop.assert_called_once()

    def test_schedule_hide_does_nothing_when_pinned(self):
        overlay = _overlay()
        overlay.pinned = True
        with mock.patch.object(overlay._hide_timer, "start") as start:
            overlay.schedule_hide()
        start.assert_not_called()

    def test_schedule_hide_starts_the_timer_with_the_configured_delay(self):
        overlay = _overlay(auto_hide_seconds=5.0)
        with mock.patch.object(overlay._hide_timer, "start") as start:
            overlay.schedule_hide()
        start.assert_called_once_with(5000)

    def test_auto_hide_reschedules_when_the_user_is_still_interacting(self):
        overlay = _overlay()
        overlay.pinned = True
        with mock.patch.object(overlay, "hide_hud") as hide_hud, mock.patch.object(overlay._hide_timer, "start") as start:
            overlay._auto_hide()
        hide_hud.assert_not_called()
        start.assert_called_once_with(3000)

    def test_auto_hide_hides_when_nothing_is_holding_it_open(self):
        overlay = _overlay()
        with mock.patch.object(overlay, "underMouse", return_value=False):
            with mock.patch.object(overlay.command_bar.input, "hasFocus", return_value=False):
                with mock.patch.object(overlay, "hide_hud") as hide_hud:
                    overlay._auto_hide()
        hide_hud.assert_called_once()


class KeyAndMouseTests(unittest.TestCase):
    def test_escape_closes_the_overlay(self):
        overlay = _overlay()
        with mock.patch.object(overlay, "_close_by_user") as close_by_user:
            overlay.keyPressEvent(_key_event(Qt.Key_Escape))
        close_by_user.assert_called_once()

    def test_close_by_user_hides_and_emits_the_signal(self):
        overlay = _overlay()
        closed = []
        overlay.closed_by_user.connect(lambda: closed.append(True))
        with mock.patch.object(overlay, "hide_hud") as hide_hud:
            overlay._close_by_user()
        hide_hud.assert_called_once()
        self.assertEqual(len(closed), 1)


class DelegationTests(unittest.TestCase):
    def test_set_state_propagates_to_stage_pill_and_top_bar(self):
        overlay = _overlay()
        overlay.set_state("listening")
        self.assertEqual(overlay.stage.orb.state, "listening")
        self.assertEqual(overlay.pill.orb.state, "listening")
        self.assertIn("Ti ascolto", overlay.top_bar.status.text())

    def test_an_unknown_state_falls_back_to_idle(self):
        overlay = _overlay()
        overlay.set_state("stato_inesistente")
        self.assertEqual(overlay.state, "idle")

    def test_notify_state_with_detail_sets_the_response(self):
        overlay = _overlay()
        overlay.set_state("notify", "Promemoria: chiama il dentista")
        self.assertEqual(overlay.pill.response._full, "Promemoria: chiama il dentista")

    def test_set_transcript_updates_stage_and_pill(self):
        overlay = _overlay()
        overlay.set_transcript("che ore sono")
        self.assertEqual(overlay.stage.transcript.text(), "«che ore sono»")
        self.assertEqual(overlay.pill.transcript.text(), "«che ore sono»")

    def test_add_turn_delegates_to_the_conversation_panel(self):
        overlay = _overlay()
        overlay.add_turn("user", "ciao")
        self.assertEqual(overlay.conversation.list.count(), 1)

    def test_set_quick_actions_delegates(self):
        overlay = _overlay()
        overlay.set_quick_actions([("Apri Chrome", "apri chrome", False)])
        self.assertEqual(overlay.quick_actions.flow.count(), 1)

    def test_set_mic_enabled_does_not_emit_mic_toggled(self):
        overlay = _overlay()
        toggled = []
        overlay.mic_toggled.connect(toggled.append)
        overlay.set_mic_enabled(False)
        self.assertEqual(toggled, [])
        self.assertFalse(overlay.command_bar.mic.isChecked())

    def test_on_submit_sets_transcript_state_and_emits_submitted(self):
        overlay = _overlay()
        submitted = []
        overlay.submitted.connect(submitted.append)
        overlay._on_submit("apri chrome")
        self.assertEqual(overlay.state, "thinking")
        self.assertEqual(submitted, ["apri chrome"])
        self.assertEqual(overlay.conversation.list.count(), 1)


class RefreshSnapshotTests(unittest.TestCase):
    def test_does_nothing_without_a_snapshot_provider(self):
        overlay = _overlay()
        overlay.refresh_snapshot()  # nessuna eccezione, nessun aggiornamento

    def test_does_nothing_when_not_visible(self):
        provider = mock.MagicMock()
        overlay = _overlay(snapshot_provider=provider)
        overlay.refresh_snapshot()
        provider.assert_not_called()

    def test_a_failing_provider_does_not_raise(self):
        provider = mock.MagicMock(side_effect=RuntimeError("boom"))
        overlay = _overlay(snapshot_provider=provider)
        with mock.patch.object(overlay, "isVisible", return_value=True):
            overlay.refresh_snapshot()  # non deve sollevare


def _key_event(key):
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.NoModifier)


if __name__ == "__main__":
    unittest.main()
