"""Test per core/voice/language_normalizer.py (F2.6.1, F2.6.5): numeri in lettere, sigle, nomi propri,
ellissi e riferimenti ordinali. Puro testo."""
import unittest

from core.voice.language_normalizer import (
    correct_proper_names, italian_number_to_int, join_spoken_acronyms, normalize_transcript,
    numbers_to_digits, resolve_ellipsis, resolve_ordinals,
)


class NumberWordTests(unittest.TestCase):
    def test_known_values(self):
        cases = {
            "zero": 0, "dieci": 10, "diciannove": 19, "venti": 20, "ventuno": 21, "ventidue": 22, "ventotto": 28,
            "trentotto": 38, "novantasei": 96, "cento": 100, "centouno": 101, "centoventitre": 123, "duecento": 200,
            "novecentonovantanove": 999, "mille": 1000, "tremila": 3000, "tremilaquattrocento": 3400,
            "milleduecento": 1200, "duemilaventi": 2020, "CINQUANTA": 50,
        }
        for word, value in cases.items():
            with self.subTest(word=word):
                self.assertEqual(italian_number_to_int(word), value)

    def test_not_numbers(self):
        for word in ("pippo", "ventiotto", "", "cinquantaa", "123", "mila", "centocento"):
            with self.subTest(word=word):
                self.assertIsNone(italian_number_to_int(word))


class NumbersToDigitsTests(unittest.TestCase):
    def test_replaces_numbers_inside_a_command(self):
        self.assertEqual(numbers_to_digits("metti un timer di dieci minuti"), "metti un timer di 10 minuti")
        self.assertEqual(numbers_to_digits("volume al cinquanta"), "volume al 50")

    def test_articles_are_never_numbers(self):
        self.assertEqual(numbers_to_digits("un cane e una gatta e uno zaino"), "un cane e una gatta e uno zaino")

    def test_per_cento_is_left_alone(self):
        self.assertEqual(numbers_to_digits("volume al cinquanta per cento"), "volume al 50 per cento")

    def test_sei_is_a_number_only_before_a_unit_of_measure(self):
        self.assertEqual(numbers_to_digits("tu sei pronto"), "tu sei pronto")
        self.assertEqual(numbers_to_digits("tra sei minuti"), "tra 6 minuti")
        self.assertEqual(numbers_to_digits("ho sei file"), "ho 6 file")

    def test_multi_word_numbers_are_merged_only_when_plausible(self):
        self.assertEqual(numbers_to_digits("duecento cinquanta euro"), "250 euro")
        self.assertEqual(numbers_to_digits("venti due gradi"), "22 gradi")
        self.assertEqual(numbers_to_digits("due tre"), "2 3")  # non si sommano: sono due numeri

    def test_a_comma_stops_merging_and_punctuation_is_kept(self):
        self.assertEqual(numbers_to_digits("cinquanta, sessanta"), "50, 60")
        self.assertEqual(numbers_to_digits("sono le dieci."), "sono le 10.")

    def test_text_without_numbers_is_untouched(self):
        self.assertEqual(numbers_to_digits("apri spotify"), "apri spotify")

    def test_is_idempotent(self):
        once = numbers_to_digits("metti un timer di dieci minuti")
        self.assertEqual(numbers_to_digits(once), once)


class AcronymTests(unittest.TestCase):
    def test_spaced_letters_become_an_acronym(self):
        self.assertEqual(join_spoken_acronyms("apri la g p t"), "apri la GPT")
        self.assertEqual(join_spoken_acronyms("controlla la c p u"), "controlla la CPU")

    def test_plain_italian_with_single_letter_words_is_untouched(self):
        self.assertEqual(join_spoken_acronyms("vado a Roma e a Milano"), "vado a Roma e a Milano")
        self.assertEqual(join_spoken_acronyms("a e i o u"), "a e i o u")

    def test_two_letters_are_enough(self):
        self.assertEqual(join_spoken_acronyms("il t v"), "il TV")


class ProperNameTests(unittest.TestCase):
    VOCAB = ["Spotify", "Blender", "Discord", "Visual Studio Code", "Whatsapp", "Chrome", "Marco", "Giuseppe"]

    def test_a_mishearing_within_one_edit_is_corrected(self):
        self.assertEqual(correct_proper_names("apri spotifi", self.VOCAB), "apri Spotify")
        self.assertEqual(correct_proper_names("apri blendar", self.VOCAB), "apri Blender")

    def test_a_word_already_in_the_vocabulary_is_left_alone(self):
        self.assertEqual(correct_proper_names("apri spotify", self.VOCAB), "apri spotify")

    def test_english_technical_words_are_never_italianised(self):
        self.assertEqual(correct_proper_names("fai il download della playlist", ["Downlod", "Playlist2"]), "fai il download della playlist")
        self.assertEqual(correct_proper_names("fai uno screenshot", ["Screenshoot"]), "fai uno screenshot")

    def test_short_words_are_never_touched(self):
        self.assertEqual(correct_proper_names("apri chat", ["Chart"]), "apri chat")

    def test_an_ambiguous_correction_is_not_made(self):
        # "marca" e' a distanza 1 sia da "Marco" sia da "Marca2": nel dubbio niente correzione
        self.assertEqual(correct_proper_names("chiama marcx", ["Marco", "Marci"]), "chiama marcx")

    def test_a_far_word_is_not_corrected(self):
        self.assertEqual(correct_proper_names("apri qualcosa", self.VOCAB), "apri qualcosa")

    def test_case_style_of_the_original_is_kept_for_capitalised_words(self):
        self.assertEqual(correct_proper_names("Apri Spotifi", ["spotify"]), "Apri Spotify")

    def test_long_words_allow_two_edits(self):
        self.assertEqual(correct_proper_names("apri giuseppino", ["Giuseppina"]), "apri Giuseppina")
        self.assertEqual(correct_proper_names("apri giuseppinoxx", ["Giuseppina"]), "apri giuseppinoxx")

    def test_empty_vocabulary_changes_nothing(self):
        self.assertEqual(correct_proper_names("apri spotifi", []), "apri spotifi")


class NormalizeTranscriptTests(unittest.TestCase):
    def test_the_full_chain(self):
        text = "apri spotifi e metti un timer di dieci minuti sulla g p t"
        self.assertEqual(
            normalize_transcript(text, ["Spotify"]),
            "apri Spotify e metti un timer di 10 minuti sulla GPT",
        )

    def test_numbers_can_be_switched_off(self):
        self.assertEqual(normalize_transcript("dieci minuti", numbers=False), "dieci minuti")

    def test_is_idempotent(self):
        once = normalize_transcript("apri spotifi tra dieci minuti", ["Spotify"])
        self.assertEqual(normalize_transcript(once, ["Spotify"]), once)

    def test_code_switching_text_survives(self):
        text = "apri il browser e fai il download di quel file"
        self.assertEqual(normalize_transcript(text, ["Spotify", "Chrome"]), text)


class EllipsisTests(unittest.TestCase):
    def test_a_followup_fills_the_single_text_slot(self):
        result = resolve_ellipsis("e a Milano?", "GET_WEATHER", {"city": "Roma"})
        assert result is not None
        self.assertEqual((result.intent, result.parameters, result.filled_param, result.value), ("GET_WEATHER", {"city": "Milano"}, "city", "Milano"))

    def test_other_connectives(self):
        for text, value in (("anche per domani", "domani"), ("invece la capitale", "capitale"), ("e poi Torino", "Torino")):
            with self.subTest(text=text):
                result = resolve_ellipsis(text, "GET_WEATHER", {"city": "Roma"})
                assert result is not None
                self.assertEqual(result.value, value)

    def test_non_text_parameters_are_preserved(self):
        result = resolve_ellipsis("e a Milano", "GET_WEATHER", {"city": "Roma", "days": 3})
        assert result is not None
        self.assertEqual(result.parameters, {"city": "Milano", "days": 3})

    def test_two_text_slots_are_ambiguous_so_no_guess(self):
        self.assertIsNone(resolve_ellipsis("e a Milano", "SEND_MESSAGE", {"contact": "Marco", "text": "ciao"}))

    def test_a_new_command_is_not_an_ellipsis(self):
        self.assertIsNone(resolve_ellipsis("e apri Spotify", "GET_WEATHER", {"city": "Roma"}))

    def test_no_previous_command_no_ellipsis(self):
        self.assertIsNone(resolve_ellipsis("e a Milano", None, None))
        self.assertIsNone(resolve_ellipsis("e a Milano", "GET_TIME", {}))

    def test_a_sentence_that_does_not_start_like_an_ellipsis_is_ignored(self):
        self.assertIsNone(resolve_ellipsis("che tempo fa a Milano", "GET_WEATHER", {"city": "Roma"}))


class OrdinalTests(unittest.TestCase):
    def test_multiple_references(self):
        self.assertEqual(resolve_ordinals("apri il primo e il terzo", 5), [0, 2])

    def test_last_and_second_to_last(self):
        self.assertEqual(resolve_ordinals("l'ultimo", 4), [3])
        self.assertEqual(resolve_ordinals("il penultimo", 4), [2])

    def test_first_n_and_last_n(self):
        self.assertEqual(resolve_ordinals("i primi due", 5), [0, 1])
        self.assertEqual(resolve_ordinals("gli ultimi tre", 5), [2, 3, 4])

    def test_all(self):
        self.assertEqual(resolve_ordinals("apri tutti", 3), [0, 1, 2])

    def test_numeric_references(self):
        self.assertEqual(resolve_ordinals("apri il 2", 3), [1])
        self.assertEqual(resolve_ordinals("il 1 e il 3", 3), [0, 2])

    def test_a_reference_beyond_the_list_returns_none_rather_than_guessing(self):
        self.assertIsNone(resolve_ordinals("il quinto", 3))
        self.assertIsNone(resolve_ordinals("i primi quattro", 3))
        self.assertIsNone(resolve_ordinals("il 7", 3))

    def test_no_reference_or_empty_list(self):
        self.assertIsNone(resolve_ordinals("apri spotify", 3))
        self.assertIsNone(resolve_ordinals("il primo", 0))

    def test_duplicates_are_removed_keeping_order(self):
        self.assertEqual(resolve_ordinals("il primo e poi ancora il primo e il secondo", 3), [0, 1])


if __name__ == "__main__":
    unittest.main()
