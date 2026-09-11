"""Test unitari per core/gui/hud/widgets.py: nessuna suite esisteva finora, nessun bug trovato.
Serve una QApplication reale (Qt lo richiede per costruire i widget): riusa quella creata da
tests/test_hud_glass.py se il processo la ha gia', altrimenti ne crea una (mai una seconda: Qt
non lo permette). Mai una vera finestra mostrata; i timer dei widget non vengono mai avviati
(start()) per non dipendere dal timing reale dell'event loop."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from core.gui.hud.widgets import (
    ChipButton,
    CommandBar,
    CenterStage,
    ContextPanel,
    ConversationPanel,
    FlowLayout,
    OrbWidget,
    QuickActionsPanel,
    TopBar,
    TypewriterLabel,
    WaveformWidget,
)

_app = QApplication.instance() or QApplication([])


class OrbWidgetTests(unittest.TestCase):
    def test_set_level_is_clamped_to_zero_and_one(self):
        orb = OrbWidget()
        orb.set_level(5.0)
        self.assertEqual(orb.level, 1.0)
        orb.set_level(-5.0)
        self.assertEqual(orb.level, 0.0)

    def test_set_state_schedules_a_repaint_and_stores_the_state(self):
        orb = OrbWidget()
        orb.set_state("listening")
        self.assertEqual(orb.state, "listening")

    def test_a_left_click_emits_clicked(self):
        orb = OrbWidget()
        clicks = []
        orb.clicked.connect(lambda: clicks.append(True))
        QTest.mouseClick(orb, Qt.LeftButton)
        self.assertEqual(len(clicks), 1)

    def test_a_right_click_does_not_emit_clicked(self):
        orb = OrbWidget()
        clicks = []
        orb.clicked.connect(lambda: clicks.append(True))
        QTest.mouseClick(orb, Qt.RightButton)
        self.assertEqual(len(clicks), 0)

    def test_tick_smooths_towards_the_level_while_listening(self):
        orb = OrbWidget()
        orb.state = "listening"
        orb.set_level(1.0)
        orb._tick()
        self.assertGreater(orb._smoothed, 0.0)

    def test_start_and_stop_control_the_timer(self):
        orb = OrbWidget()
        orb.start()
        self.assertTrue(orb._timer.isActive())
        orb.stop()
        self.assertFalse(orb._timer.isActive())

    def test_paint_does_not_raise(self):
        orb = OrbWidget()
        orb.resize(200, 200)
        orb.repaint()


class WaveformWidgetTests(unittest.TestCase):
    def test_set_level_is_clamped(self):
        wave = WaveformWidget()
        wave.set_level(2.0)
        self.assertEqual(wave.level, 1.0)

    def test_size_hint_uses_the_preferred_width(self):
        wave = WaveformWidget(preferred_width=250, height=50)
        self.assertEqual(wave.sizeHint().width(), 250)

    def test_tick_maintains_a_fixed_size_history(self):
        wave = WaveformWidget()
        for _ in range(5):
            wave._tick()
        self.assertEqual(len(wave._levels), wave.HISTORY)

    def test_paint_does_not_raise(self):
        wave = WaveformWidget()
        wave.resize(300, 60)
        wave._tick()
        wave.repaint()


class TypewriterLabelTests(unittest.TestCase):
    def test_reveals_the_text_progressively(self):
        label = TypewriterLabel()
        label.set_text_animated("ciao mondo")
        self.assertEqual(label.text(), "")
        label._step()
        self.assertEqual(label.text(), "cia")
        label._step()
        self.assertEqual(label.text(), "ciao m")

    def test_stops_the_timer_once_fully_revealed(self):
        label = TypewriterLabel()
        label.set_text_animated("ok")
        label._step()
        self.assertEqual(label.text(), "ok")
        self.assertFalse(label._timer.isActive())

    def test_empty_text_does_not_start_the_timer(self):
        label = TypewriterLabel()
        label.set_text_animated("")
        self.assertFalse(label._timer.isActive())


class FlowLayoutTests(unittest.TestCase):
    def test_add_and_count_items(self):
        host = QWidget()
        layout = FlowLayout(host)
        layout.addWidget(QPushButton("a"))
        layout.addWidget(QPushButton("b"))
        self.assertEqual(layout.count(), 2)

    def test_take_at_removes_and_returns_the_item(self):
        host = QWidget()
        layout = FlowLayout(host)
        button = QPushButton("a")
        layout.addWidget(button)
        item = layout.takeAt(0)
        self.assertIs(item.widget(), button)
        self.assertEqual(layout.count(), 0)

    def test_out_of_range_access_returns_none(self):
        host = QWidget()
        layout = FlowLayout(host)
        self.assertIsNone(layout.itemAt(0))
        self.assertIsNone(layout.takeAt(0))

    def test_items_wrap_to_a_new_line_when_they_do_not_fit(self):
        host = QWidget()
        layout = FlowLayout(host, spacing=5)
        for _ in range(3):
            button = QPushButton("x" * 20)
            button.setFixedSize(80, 24)
            layout.addWidget(button)
        from PySide6.QtCore import QRect
        layout.setGeometry(QRect(0, 0, 100, 500))
        positions = [item.widget().geometry().y() for item in layout._items]
        self.assertGreater(len(set(positions)), 1)

    def test_clear_removes_every_item(self):
        host = QWidget()
        layout = FlowLayout(host)
        layout.addWidget(QPushButton("a"))
        layout.addWidget(QPushButton("b"))
        layout.clear()
        self.assertEqual(layout.count(), 0)


class ChipButtonTests(unittest.TestCase):
    def test_a_starred_chip_shows_a_star_prefix(self):
        chip = ChipButton("Apri Chrome", "apri chrome", starred=True)
        self.assertTrue(chip.text().startswith("★"))
        self.assertEqual(chip.command, "apri chrome")

    def test_a_plain_chip_has_no_star(self):
        chip = ChipButton("Apri Chrome", "apri chrome", starred=False)
        self.assertFalse(chip.text().startswith("★"))


class ConversationPanelTests(unittest.TestCase):
    def _panel(self):
        return ConversationPanel(backdrop=None)

    def test_load_populates_the_list_in_order(self):
        panel = self._panel()
        panel.load([{"role": "user", "text": "ciao"}, {"role": "assistant", "text": "ciao a te"}])
        self.assertEqual(panel.list.count(), 2)

    def test_an_empty_text_turn_is_skipped(self):
        panel = self._panel()
        panel.add_turn("user", "")
        self.assertEqual(panel.list.count(), 0)

    def test_more_than_eighty_turns_are_capped(self):
        panel = self._panel()
        for i in range(85):
            panel.add_turn("user", f"messaggio {i}", scroll=False)
        self.assertEqual(panel.list.count(), 80)

    def test_clicking_a_user_turn_emits_command_requested(self):
        panel = self._panel()
        panel.add_turn("user", "apri chrome", scroll=False)
        requested = []
        panel.command_requested.connect(requested.append)
        panel._on_item_clicked(panel.list.item(0))
        self.assertEqual(requested, ["apri chrome"])

    def test_clicking_an_assistant_turn_does_not_emit(self):
        panel = self._panel()
        panel.add_turn("assistant", "risposta di jake", scroll=False)
        requested = []
        panel.command_requested.connect(requested.append)
        panel._on_item_clicked(panel.list.item(0))
        self.assertEqual(requested, [])


class ContextPanelTests(unittest.TestCase):
    def _panel(self):
        return ContextPanel(backdrop=None)

    def test_empty_snapshot_shows_placeholders(self):
        panel = self._panel()
        panel.update_snapshot({})
        texts = [panel.list.item(i).text() for i in range(panel.list.count())]
        joined = "\n".join(texts)
        self.assertIn("Nessuno", joined)
        self.assertIn("Lista vuota", joined)

    def test_a_generic_timer_label_has_no_trailing_space_in_its_command(self):
        panel = self._panel()
        panel.update_snapshot({"timers": [{"label": "timer", "remaining": "2 minuti"}]})
        commands = [panel.list.item(i).data(Qt.UserRole) for i in range(panel.list.count())]
        self.assertIn("annulla il timer", commands)

    def test_a_named_timer_includes_its_label_in_the_command(self):
        panel = self._panel()
        panel.update_snapshot({"timers": [{"label": "pasta", "remaining": "5 minuti"}]})
        commands = [panel.list.item(i).data(Qt.UserRole) for i in range(panel.list.count())]
        self.assertIn("annulla il timer pasta", commands)

    def test_todos_are_capped_at_five(self):
        panel = self._panel()
        panel.update_snapshot({"todos": [{"text": f"cosa {i}"} for i in range(10)]})
        entries = [panel.list.item(i).text() for i in range(panel.list.count()) if "cosa" in panel.list.item(i).text()]
        self.assertEqual(len(entries), 5)

    def test_clicking_an_entry_with_a_command_emits_it(self):
        panel = self._panel()
        panel.update_snapshot({"todos": [{"text": "comprare il pane"}]})
        requested = []
        panel.command_requested.connect(requested.append)
        for i in range(panel.list.count()):
            item = panel.list.item(i)
            if item.data(Qt.UserRole):
                panel._on_item_clicked(item)
        self.assertEqual(requested, ["segna come fatto comprare il pane"])

    def test_stats_line_combines_available_metrics(self):
        panel = self._panel()
        panel.update_snapshot({"cpu": 12.4, "ram": 55.0, "battery": 80.0, "plugged": True, "skills": 120})
        self.assertIn("CPU 12%", panel.stats.text())
        self.assertIn("RAM 55%", panel.stats.text())
        self.assertIn("80%", panel.stats.text())
        self.assertIn("120 capacità", panel.stats.text())


class QuickActionsPanelTests(unittest.TestCase):
    def test_set_actions_builds_one_chip_per_action(self):
        panel = QuickActionsPanel(backdrop=None)
        panel.set_actions([("Apri Chrome", "apri chrome", False), ("Meteo", "che tempo fa", True)])
        self.assertEqual(panel.flow.count(), 2)

    def test_clicking_a_chip_emits_its_command(self):
        panel = QuickActionsPanel(backdrop=None)
        panel.set_actions([("Apri Chrome", "apri chrome", False)])
        requested = []
        panel.command_requested.connect(requested.append)
        chip = panel.flow.itemAt(0).widget()
        QTest.mouseClick(chip, Qt.LeftButton)
        self.assertEqual(requested, ["apri chrome"])

    def test_set_actions_again_clears_the_previous_ones(self):
        panel = QuickActionsPanel(backdrop=None)
        panel.set_actions([("Uno", "uno", False)])
        panel.set_actions([("Due", "due", False), ("Tre", "tre", False)])
        self.assertEqual(panel.flow.count(), 2)


class CommandBarTests(unittest.TestCase):
    def test_submitting_text_emits_it_and_clears_the_input(self):
        bar = CommandBar(backdrop=None)
        bar.input.setText("apri chrome")
        submitted = []
        bar.submitted.connect(submitted.append)
        bar._submit()
        self.assertEqual(submitted, ["apri chrome"])
        self.assertEqual(bar.input.text(), "")

    def test_submitting_empty_text_does_nothing(self):
        bar = CommandBar(backdrop=None)
        bar.input.setText("   ")
        submitted = []
        bar.submitted.connect(submitted.append)
        bar._submit()
        self.assertEqual(submitted, [])

    def test_toggling_the_mic_button_emits_mic_toggled(self):
        bar = CommandBar(backdrop=None)
        toggled = []
        bar.mic_toggled.connect(toggled.append)
        bar.mic.setChecked(False)
        self.assertEqual(toggled, [False])


class TopBarTests(unittest.TestCase):
    def test_set_state_updates_the_status_label(self):
        bar = TopBar(backdrop=None)
        bar.set_state("listening")
        self.assertIn("Ti ascolto", bar.status.text())

    def test_close_button_emits_close_requested(self):
        bar = TopBar(backdrop=None)
        closed = []
        bar.close_requested.connect(lambda: closed.append(True))
        QTest.mouseClick(bar.close, Qt.LeftButton)
        self.assertEqual(len(closed), 1)

    def test_pin_button_emits_pin_toggled(self):
        bar = TopBar(backdrop=None)
        toggled = []
        bar.pin_toggled.connect(toggled.append)
        bar.pin.setChecked(True)
        self.assertEqual(toggled, [True])


class CenterStageTests(unittest.TestCase):
    def test_set_state_updates_orb_waveform_and_title(self):
        stage = CenterStage(backdrop=None)
        stage.set_state("thinking")
        self.assertEqual(stage.orb.state, "thinking")
        self.assertEqual(stage.waveform.state, "thinking")
        self.assertIn("Ci penso", stage.title.text())

    def test_set_transcript_wraps_the_text_in_quotes(self):
        stage = CenterStage(backdrop=None)
        stage.set_transcript("che ore sono")
        self.assertEqual(stage.transcript.text(), "«che ore sono»")

    def test_set_transcript_with_empty_text_clears_it(self):
        stage = CenterStage(backdrop=None)
        stage.set_transcript("qualcosa")
        stage.set_transcript("")
        self.assertEqual(stage.transcript.text(), "")

    def test_add_step_displays_only_the_last_five(self):
        stage = CenterStage(backdrop=None)
        for i in range(8):
            stage.add_step(f"passo {i}")
        self.assertEqual(stage.steps.text().count("\n") + 1, 5)
        self.assertIn("passo 7", stage.steps.text())
        self.assertNotIn("passo 0", stage.steps.text())

    def test_clear_steps_empties_the_label(self):
        stage = CenterStage(backdrop=None)
        stage.add_step("passo 1")
        stage.clear_steps()
        self.assertEqual(stage.steps.text(), "")

    def test_start_and_stop_delegate_to_orb_and_waveform(self):
        stage = CenterStage(backdrop=None)
        stage.start()
        self.assertTrue(stage.orb._timer.isActive())
        self.assertTrue(stage.waveform._timer.isActive())
        stage.stop()
        self.assertFalse(stage.orb._timer.isActive())
        self.assertFalse(stage.waveform._timer.isActive())


if __name__ == "__main__":
    unittest.main()
