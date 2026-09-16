"""Test unitari per tools/generate_hud_event_types.py (F4.1.2, "niente enum mantenuti a mano" -
vedi il docstring del modulo per il buco reale che chiude: JakeClient.cpp confrontava stringhe
letterali scritte a mano, senza garanzia di sincronia con core/hud_protocol.py::EventType)."""
import tempfile
import unittest
from pathlib import Path

from core.hud_protocol import EventType, HUD_PAYLOAD_RULES, HUD_VERIFICATION_VALUES
from tools.generate_hud_event_types import generate_header, main


class GenerateHeaderTests(unittest.TestCase):
    def test_every_event_type_member_becomes_a_constant(self):
        header = generate_header([member.name for member in EventType])
        for member in EventType:
            self.assertIn(f'inline constexpr const char *{member.name} = "{member.name}";', header)

    def test_a_new_or_renamed_member_shows_up_automatically(self):
        """Il buco reale: prima di questo generatore, un tipo aggiunto/rinominato lato Python
        poteva disallinearsi in silenzio dal lato C++ - qui la fonte e' l'enum vero, non un
        elenco copiato a mano, quindi un membro con un nome mai visto finisce comunque nel
        risultato senza bisogno di aggiornare questo test."""
        header = generate_header(["UN_TIPO_MAI_VISTO_PRIMA"])
        self.assertIn('inline constexpr const char *UN_TIPO_MAI_VISTO_PRIMA = "UN_TIPO_MAI_VISTO_PRIMA";', header)

    def test_header_has_include_guard_and_namespace(self):
        header = generate_header(["IDLE"])
        self.assertIn("#pragma once", header)
        self.assertIn("namespace JakeHudEventType", header)

    def test_header_declares_it_is_generated_not_hand_written(self):
        header = generate_header(["IDLE"])
        self.assertIn("GENERATO AUTOMATICAMENTE", header)
        self.assertIn("NON MODIFICARE A MANO", header)

    def test_known_types_and_payload_rules_come_from_the_python_contract(self):
        header = generate_header([member.name for member in EventType])
        self.assertIn(f"std::array<const char *, {len(EventType)}> ALL", header)
        for event, fields in HUD_PAYLOAD_RULES.items():
            for key, rule in fields.items():
                self.assertIn(f'{{"{event}", "{key}", "{rule}"}}', header)
        for value in HUD_VERIFICATION_VALUES:
            self.assertIn(f'"{value}"', header)


class MainWritesTheFileTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def test_writes_a_header_containing_every_real_event_type(self):
        output_path = Path(self._tmpdir.name) / "sub" / "HudEventTypes.h"

        exit_code = _run_main_with_args(["--output", str(output_path)])

        self.assertEqual(exit_code, 0)
        content = output_path.read_text(encoding="utf-8")
        for member in EventType:
            self.assertIn(member.name, content)

    def test_creates_missing_parent_directories(self):
        output_path = Path(self._tmpdir.name) / "does" / "not" / "exist" / "yet.h"

        _run_main_with_args(["--output", str(output_path)])

        self.assertTrue(output_path.exists())


def _run_main_with_args(args: list) -> int:
    import sys
    original_argv = sys.argv
    sys.argv = ["generate_hud_event_types.py", *args]
    try:
        return main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
