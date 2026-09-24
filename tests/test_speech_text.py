"""Test per core/voice/speech_text.py (F2.5.1, F2.5.4): puro testo, nessun audio."""
import unittest

from core.voice.speech_text import (
    CODE_OMITTED, STYLES, apply_style, clean_for_speech, prepare_for_speech, split_prosodic, split_sentences,
)


class CleanForSpeechTests(unittest.TestCase):
    def test_bold_italic_and_strike_markers_are_removed_keeping_the_words(self):
        self.assertEqual(clean_for_speech("Questo e' **molto** *importante* e ~~falso~~."), "Questo e' molto importante e falso.")

    def test_nested_emphasis(self):
        self.assertEqual(clean_for_speech("***attenzione*** qui"), "attenzione qui")

    def test_a_snake_case_identifier_keeps_its_underscores(self):
        self.assertEqual(clean_for_speech("Apri il file mio_file_di_test adesso"), "Apri il file mio_file_di_test adesso")

    def test_headings_lists_and_quotes_lose_their_markers(self):
        text = "# Titolo\n- primo\n- secondo\n1. terzo\n> citazione"
        self.assertEqual(clean_for_speech(text), "Titolo\nprimo\nsecondo\nterzo\ncitazione")

    def test_links_are_read_by_their_label_and_bare_urls_become_a_link(self):
        self.assertEqual(clean_for_speech("Guarda [la guida](https://example.com/x) ora"), "Guarda la guida ora")
        self.assertEqual(clean_for_speech("Vai su https://example.com/a?b=1 subito"), "Vai su un link subito")

    def test_a_code_block_is_never_read_and_is_announced_once(self):
        text = "Ecco lo script:\n```python\nprint('ciao')\nx = 1\n```\nFunziona."
        cleaned = clean_for_speech(text)
        self.assertNotIn("print", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertEqual(cleaned.count(CODE_OMITTED), 1)
        self.assertIn("Ecco lo script:", cleaned)
        self.assertIn("Funziona.", cleaned)

    def test_two_code_blocks_still_announce_once(self):
        cleaned = clean_for_speech("```a\n1\n```\ntesto\n```b\n2\n```")
        self.assertEqual(cleaned.count(CODE_OMITTED), 1)

    def test_an_unclosed_code_block_from_a_truncated_answer_is_dropped(self):
        cleaned = clean_for_speech("Prova:\n```python\nimport os\nprint(")
        self.assertNotIn("import", cleaned)
        self.assertIn(CODE_OMITTED, cleaned)

    def test_file_gets_an_english_pronunciation_hint(self):
        spoken = prepare_for_speech("Apri il file.")
        self.assertEqual(spoken, "Apri il fail.")

    def test_inline_code_keeps_its_content_without_backticks(self):
        self.assertEqual(clean_for_speech("Usa il comando `git status` per vedere"), "Usa il comando git status per vedere")

    def test_tables_become_readable_rows_without_separator_lines(self):
        cleaned = clean_for_speech("| Nome | Eta' |\n|---|---|\n| Anna | 30 |")
        self.assertNotIn("---", cleaned)
        self.assertNotIn("|", cleaned)
        self.assertIn("Anna", cleaned)

    def test_emoji_and_html_tags_are_dropped(self):
        self.assertEqual(clean_for_speech("Fatto \U0001F389 <b>bene</b>"), "Fatto bene")

    def test_plain_text_is_left_untouched(self):
        plain = "Sono le 10.30. Il volume e' al 50%."
        self.assertEqual(clean_for_speech(plain), plain)

    def test_never_adds_code_notice_without_code(self):
        self.assertNotIn(CODE_OMITTED, clean_for_speech("Nessun codice qui"))


class SplitSentencesTests(unittest.TestCase):
    def test_basic_split_keeps_terminal_punctuation(self):
        self.assertEqual(split_sentences("Ciao. Come stai? Bene!"), ["Ciao.", "Come stai?", "Bene!"])

    def test_decimals_and_thousands_are_not_sentence_ends(self):
        self.assertEqual(split_sentences("Costa 3.5 euro, ne ho 10.000."), ["Costa 3.5 euro, ne ho 10.000."])

    def test_file_names_are_not_split(self):
        self.assertEqual(split_sentences("Apri file.txt subito."), ["Apri file.txt subito."])

    def test_abbreviations_do_not_end_a_sentence(self):
        self.assertEqual(split_sentences("Ho visto il dott. Rossi ieri."), ["Ho visto il dott. Rossi ieri."])
        self.assertEqual(split_sentences("Servono pane, latte ecc. e altro."), ["Servono pane, latte ecc. e altro."])

    def test_initials_and_ellipsis_do_not_split(self):
        self.assertEqual(split_sentences("Ha scritto J. K. Rowling."), ["Ha scritto J. K. Rowling."])
        self.assertEqual(split_sentences("Non so... forse si."), ["Non so... forse si."])

    def test_a_newline_always_separates(self):
        self.assertEqual(split_sentences("primo\nsecondo"), ["primo", "secondo"])

    def test_empty_text_has_no_sentences(self):
        self.assertEqual(split_sentences("  \n "), [])


class SplitProsodicTests(unittest.TestCase):
    def test_short_fragments_merge_with_the_next_sentence(self):
        units = split_prosodic("Ok. Ho aperto Spotify per te adesso.")
        self.assertEqual(units, ["Ok. Ho aperto Spotify per te adesso."])

    def test_a_very_long_sentence_is_cut_at_a_comma_not_mid_word(self):
        sentence = ("Ho controllato tutte le cartelle che mi hai indicato, ho trovato quattro documenti "
                    "duplicati e due file vuoti, quindi ti consiglio di rivederli prima di cancellare qualsiasi cosa.")
        units = split_prosodic(sentence, max_chars=100)
        self.assertGreater(len(units), 1)
        self.assertTrue(all(len(u) <= 110 for u in units), units)
        self.assertEqual(" ".join(units).replace("  ", " "), sentence)  # nessuna parola persa o spezzata
        self.assertTrue(units[0].endswith(","))

    def test_no_word_is_ever_lost_when_there_is_no_punctuation(self):
        text = " ".join(f"parola{i}" for i in range(80))
        units = split_prosodic(text, max_chars=60)
        self.assertEqual(" ".join(units).split(), text.split())

    def test_units_preserve_the_original_text_in_order(self):
        text = "Prima frase abbastanza lunga da restare sola. Seconda frase altrettanto lunga qui. Terza frase finale, lunga uguale."
        self.assertEqual(" ".join(split_prosodic(text)), text)

    def test_a_trailing_short_fragment_attaches_to_the_previous_unit(self):
        units = split_prosodic("Questa e' una frase completa e lunga abbastanza. Ok.")
        self.assertEqual(len(units), 1)
        self.assertTrue(units[0].endswith("Ok."))

    def test_only_a_short_fragment_is_still_spoken(self):
        self.assertEqual(split_prosodic("Ok."), ["Ok."])

    def test_empty_text_gives_no_units(self):
        self.assertEqual(split_prosodic(""), [])


class StyleTests(unittest.TestCase):
    UNITS = ["Prima.", "Seconda.", "Terza.", "Quarta."]

    def test_all_five_modes_exist(self):
        self.assertEqual(set(STYLES), {"normal", "brief", "detailed", "whisper", "night"})

    def test_normal_and_detailed_say_everything(self):
        self.assertEqual(apply_style(self.UNITS, STYLES["normal"]), self.UNITS)
        self.assertEqual(apply_style(self.UNITS, STYLES["detailed"]), self.UNITS)

    def test_brief_truncates_and_says_the_rest_is_on_screen(self):
        result = apply_style(self.UNITS, STYLES["brief"])
        self.assertEqual(result[:2], ["Prima.", "Seconda."])
        self.assertEqual(result[2], "Il resto e' a schermo.")
        self.assertEqual(len(result), 3)

    def test_a_short_answer_is_not_truncated_and_gets_no_note(self):
        self.assertEqual(apply_style(["Sono le dieci."], STYLES["brief"]), ["Sono le dieci."])

    def test_whisper_and_night_are_quieter_than_normal(self):
        self.assertLess(STYLES["whisper"].volume, STYLES["normal"].volume)
        self.assertLess(STYLES["night"].volume, STYLES["whisper"].volume + 0.2)
        self.assertLess(STYLES["night"].volume, 0.5)

    def test_night_speaks_a_single_unit(self):
        self.assertEqual(apply_style(self.UNITS, STYLES["night"])[0], "Prima.")
        self.assertEqual(len(apply_style(self.UNITS, STYLES["night"])), 2)  # unita' + nota

    def test_input_list_is_not_mutated(self):
        units = list(self.UNITS)
        apply_style(units, STYLES["brief"])
        self.assertEqual(units, self.UNITS)


class PrepareForSpeechTests(unittest.TestCase):
    LONG = "Prima frase abbastanza lunga qui. Seconda frase altrettanto lunga qui. Terza frase ancora piu' lunga qui."

    def test_plain_text_passes_through_unchanged(self):
        self.assertEqual(prepare_for_speech("Sono le dieci e mezza."), "Sono le dieci e mezza.")

    def test_markdown_is_cleaned_and_the_result_is_one_single_text(self):
        self.assertEqual(prepare_for_speech("Questo e' **molto** importante, davvero."), "Questo e' molto importante, davvero.")

    def test_a_style_limits_the_units_and_adds_the_note(self):
        result = prepare_for_speech(self.LONG, STYLES["brief"])
        self.assertTrue(result.startswith("Prima frase"))
        self.assertNotIn("Terza", result)
        self.assertTrue(result.endswith("Il resto e' a schermo."))

    def test_empty_or_markup_only_text_gives_an_empty_string(self):
        self.assertEqual(prepare_for_speech(""), "")
        self.assertEqual(prepare_for_speech("   \n  "), "")
        self.assertEqual(prepare_for_speech("---"), "")

    def test_a_code_only_answer_becomes_the_single_notice(self):
        self.assertEqual(prepare_for_speech("```python\nprint(1)\n```"), CODE_OMITTED)


if __name__ == "__main__":
    unittest.main()
