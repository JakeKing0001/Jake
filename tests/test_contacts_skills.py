"""Test unitari per skills/contacts.py: nessuna suite esisteva finora. ContactBook usa un
MemoryManager vero su file temporaneo; os.startfile/webbrowser.open sono sempre mockati (un
test che li chiamasse per davvero aprirebbe WhatsApp/il client di posta durante la suite).

F5 (indiretto, gia' verificato con lettura del codice in una sessione precedente): SendWhatsAppSkill/
SendEmailSkill non inviano MAI in autonomia, aprono solo un messaggio precompilato nel client
dell'utente - qui si verifica quella garanzia con un test esplicito, non solo leggendo il codice."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.memory_manager import MemoryManager
from skills.contacts import (
    ContactBook, ListContactsSkill, SaveContactSkill, SendEmailSkill, SendWhatsAppSkill, _digits,
)


class DigitsNormalizationTests(unittest.TestCase):
    def test_italian_number_without_prefix_gets_39_prepended(self):
        self.assertEqual(_digits("333 1234567"), "393331234567")

    def test_number_with_00_international_prefix_is_normalized(self):
        self.assertEqual(_digits("0039 333 1234567"), "393331234567")

    def test_number_with_a_plus_prefix_keeps_the_country_code(self):
        self.assertEqual(_digits("+1 555 1234567"), "15551234567")

    def test_empty_input_returns_empty(self):
        self.assertEqual(_digits(""), "")
        self.assertEqual(_digits(None), "")

    def test_non_digit_characters_are_stripped(self):
        self.assertEqual(_digits("333-123.4567"), "393331234567")


class _WithContactBook(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_contacts_test_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.contact_book = ContactBook(self.memory_manager)


class ContactBookTests(_WithContactBook):
    def test_saving_a_new_contact_with_a_phone(self):
        contact = self.contact_book.save("marco", phone="333 1234567")
        self.assertEqual(contact["phone"], "333 1234567")
        self.assertEqual(contact["name"], "marco")

    def test_get_is_case_insensitive_on_the_name(self):
        self.contact_book.save("Marco", phone="333 1234567")
        self.assertIsNotNone(self.contact_book.get("MARCO"))

    def test_saving_again_merges_instead_of_overwriting(self):
        self.contact_book.save("marco", phone="333 1234567")
        self.contact_book.save("marco", email="marco@example.com")
        contact = self.contact_book.get("marco")
        self.assertEqual(contact["phone"], "333 1234567")
        self.assertEqual(contact["email"], "marco@example.com")

    def test_get_unknown_contact_returns_none(self):
        self.assertIsNone(self.contact_book.get("non esiste"))

    def test_list_all_returns_every_saved_contact(self):
        self.contact_book.save("marco", phone="1")
        self.contact_book.save("giulia", phone="2")
        names = {c["name"] for c in self.contact_book.list_all()}
        self.assertEqual(names, {"marco", "giulia"})


class SaveContactSkillTests(_WithContactBook):
    def test_missing_name_fails(self):
        result = SaveContactSkill(self.contact_book).execute({"phone": "1"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_name_without_phone_or_email_fails(self):
        result = SaveContactSkill(self.contact_book).execute({"name": "marco"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_saves_a_real_contact(self):
        result = SaveContactSkill(self.contact_book).execute({"name": "marco", "phone": "333 1234567"})
        self.assertTrue(result.success)
        self.assertIsNotNone(self.contact_book.get("marco"))


class ListContactsSkillTests(_WithContactBook):
    def test_no_contacts_reports_not_found(self):
        result = ListContactsSkill(self.contact_book).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_saved_contacts(self):
        self.contact_book.save("marco", phone="1")
        result = ListContactsSkill(self.contact_book).execute({})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["contacts"]), 1)


class SendWhatsAppTests(_WithContactBook):
    def test_missing_parameters_fails(self):
        skill = SendWhatsAppSkill(self.contact_book)
        self.assertEqual(skill.execute({"message": "ciao"}).error, "MISSING_PARAMETERS")
        self.assertEqual(skill.execute({"contact": "marco"}).error, "MISSING_PARAMETERS")

    def test_unknown_contact_reports_contact_not_found(self):
        result = SendWhatsAppSkill(self.contact_book).execute({"contact": "sconosciuto", "message": "ciao"})
        self.assertEqual(result.error, "CONTACT_NOT_FOUND")

    def test_a_bare_phone_number_is_used_directly_without_a_contact_lookup(self):
        with mock.patch("os.startfile") as startfile:
            result = SendWhatsAppSkill(self.contact_book).execute({"contact": "3331234567", "message": "ciao"})
        self.assertTrue(result.success)
        startfile.assert_called_once()

    def test_never_sends_on_its_own_only_opens_a_prefilled_draft(self):
        """La garanzia chiave (F5): non deve mai inviare in autonomia, solo aprire un messaggio
        precompilato che l'utente deve ancora confermare a mano."""
        self.contact_book.save("marco", phone="333 1234567")
        with mock.patch("os.startfile") as startfile:
            result = SendWhatsAppSkill(self.contact_book).execute({"contact": "marco", "message": "ciao"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["via"], "app")
        sent_uri = startfile.call_args[0][0]
        self.assertTrue(sent_uri.startswith("whatsapp://send"))

    def test_falls_back_to_the_web_when_the_app_is_not_available(self):
        self.contact_book.save("marco", phone="333 1234567")
        with mock.patch("os.startfile", side_effect=OSError("app non installata")):
            with mock.patch("webbrowser.open", return_value=True) as browser_open:
                result = SendWhatsAppSkill(self.contact_book).execute({"contact": "marco", "message": "ciao"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["via"], "web")
        self.assertIn("wa.me", browser_open.call_args[0][0])

    def test_both_channels_failing_is_reported(self):
        self.contact_book.save("marco", phone="333 1234567")
        with mock.patch("os.startfile", side_effect=OSError("boom")):
            with mock.patch("webbrowser.open", side_effect=Exception("boom")):
                result = SendWhatsAppSkill(self.contact_book).execute({"contact": "marco", "message": "ciao"})
        self.assertEqual(result.error, "OPERATION_FAILED")


class SendEmailTests(_WithContactBook):
    def test_missing_to_fails(self):
        result = SendEmailSkill(self.contact_book).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_bare_email_address_is_used_directly(self):
        with mock.patch("os.startfile") as startfile:
            result = SendEmailSkill(self.contact_book).execute({"to": "marco@example.com", "subject": "ciao"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["to"], "marco@example.com")
        startfile.assert_called_once()

    def test_unknown_contact_name_reports_contact_not_found(self):
        result = SendEmailSkill(self.contact_book).execute({"to": "sconosciuto"})
        self.assertEqual(result.error, "CONTACT_NOT_FOUND")

    def test_resolves_a_contact_name_to_its_saved_email(self):
        self.contact_book.save("marco", email="marco@example.com")
        with mock.patch("os.startfile") as startfile:
            result = SendEmailSkill(self.contact_book).execute({"to": "marco"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["to"], "marco@example.com")
        self.assertIn("mailto:marco@example.com", startfile.call_args[0][0])

    def test_never_sends_on_its_own_only_opens_a_prefilled_draft(self):
        """La stessa garanzia di SendWhatsAppSkill: mailto: apre solo il client di posta con il
        messaggio precompilato, non invia mai da solo."""
        with mock.patch("os.startfile") as startfile:
            SendEmailSkill(self.contact_book).execute({"to": "marco@example.com", "subject": "Ciao", "body": "Come stai?"})
        sent_url = startfile.call_args[0][0]
        self.assertTrue(sent_url.startswith("mailto:"))

    def test_falls_back_to_the_browser_when_startfile_fails(self):
        with mock.patch("os.startfile", side_effect=OSError("nessun client")):
            with mock.patch("webbrowser.open", return_value=True) as browser_open:
                result = SendEmailSkill(self.contact_book).execute({"to": "marco@example.com"})
        self.assertTrue(result.success)
        browser_open.assert_called_once()

    def test_both_channels_failing_is_reported(self):
        with mock.patch("os.startfile", side_effect=OSError("boom")):
            with mock.patch("webbrowser.open", side_effect=Exception("boom")):
                result = SendEmailSkill(self.contact_book).execute({"to": "marco@example.com"})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
