"""Test per core/daily_brief.py (F6.6.1-F6.6.3, F6.6.7) e core/meeting_copilot.py (F6.6.4-F6.6.7).
Fonti finte con orologio finto: il punto e' provare che NESSUN dato viene inventato e che il consenso non
si aggira, non che una API risponda."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.daily_brief import (
    Brief, BriefBuilder, BriefItem, Redactor, RemindersSource, TodosSource,
)
from core.meeting_copilot import (
    ApprovalRequiredError, ConsentError, MeetingCopilot, MeetingState, PendingSend,
)

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Source:
    def __init__(self, name, items=None, error=None):
        self.name = name
        self._items = items or []
        self._error = error

    def fetch(self, now):
        if self._error:
            raise self._error
        return list(self._items)


def item(section="agenda", text="Riunione con il team", source="calendario", age=60.0, **kwargs):
    return BriefItem(section, text, source, NOW - age, **kwargs)


def builder(*sources, **kwargs):
    return BriefBuilder(list(sources), clock=Clock(), **kwargs)


class ProvenanceTests(unittest.TestCase):
    def test_every_line_carries_its_source_and_when_it_was_read(self):
        brief = builder(Source("calendario", [item(age=600)])).build("detailed")
        (entry,) = brief.provenance()
        self.assertEqual((entry["source"], entry["fetched_at"], entry["section"]), ("calendario", NOW - 600, "agenda"))
        self.assertIn("[calendario, 10 min fa]", brief.text)

    def test_an_item_without_a_source_is_rejected_not_shown(self):
        brief = builder(Source("x", [item(source=""), item(source="   "), item(text="Riga valida")])).build("detailed")
        self.assertEqual([r.text for r in brief.sections["agenda"]], ["Riga valida"])
        self.assertEqual(len(brief.rejected), 2)
        self.assertIn("manca la fonte", brief.rejected[0][1])
        self.assertIn("2 elementi scartati", brief.text)

    def test_an_item_read_in_the_future_is_rejected_as_not_credible(self):
        future = BriefItem("agenda", "Qualcosa dal futuro", "calendario", NOW + 3600)
        brief = builder(Source("x", [future])).build()
        self.assertEqual(brief.sections, {})
        self.assertIn("futuro", brief.rejected[0][1])

    def test_empty_text_and_unknown_sensitivity_are_rejected(self):
        brief = builder(Source("x", [item(text="  "), item(sensitivity="segretissimo")])).build()
        self.assertEqual(len(brief.rejected), 2)

    def test_a_source_that_fails_is_reported_and_never_replaced_with_made_up_data(self):
        brief = builder(Source("calendario", error=ConnectionError("offline")), Source("todo", [item("open_tasks", "Comprare il latte", "todo")])).build()
        self.assertEqual(list(brief.sections), ["open_tasks"])
        self.assertEqual(brief.unavailable[0][0], "calendario")
        self.assertIn("La fonte 'calendario' non ha risposto", brief.text)
        self.assertNotIn("Agenda", brief.text)  # nessuna sezione agenda inventata

    def test_when_every_source_fails_the_brief_says_so_instead_of_filling_in(self):
        brief = builder(Source("a", error=RuntimeError("x")), Source("b", error=RuntimeError("y"))).build()
        self.assertEqual(brief.sections, {})
        self.assertIn("Non ho dati aggiornati da nessuna fonte", brief.text)
        self.assertEqual(len(brief.unavailable), 2)

    def test_with_no_sources_and_no_failures_the_brief_admits_it_has_nothing(self):
        self.assertIn("Non ho dati", builder().build().text)

    def test_the_brief_contains_no_text_that_did_not_come_from_a_source(self):
        brief = builder(Source("calendario", [item(text="Dentista alle 15")])).build("detailed")
        provenance_texts = {e["text"] for e in brief.provenance()}
        content_lines = [line[2:].split(" [")[0] for line in brief.text.splitlines() if line.startswith("- ")]
        self.assertTrue(set(content_lines) <= provenance_texts)


class FreshnessAndFormatTests(unittest.TestCase):
    def test_a_stale_item_is_flagged_in_the_detailed_format(self):
        brief = builder(Source("meteo", [item("weather", "Sole, 22 gradi", "meteo", age=7200, max_age_s=1800)])).build("detailed")
        self.assertIn("DATO VECCHIO", brief.text)
        self.assertIn("2 h fa", brief.text)
        self.assertTrue(brief.sections["weather"][0].stale)

    def test_the_short_format_omits_stale_items_and_says_how_many(self):
        brief = builder(Source("meteo", [item("weather", "Sole", "meteo", age=7200, max_age_s=1800), item("weather", "Vento", "meteo")])).build("short")
        self.assertEqual([r.text for r in brief.sections["weather"]], ["Vento"])
        self.assertEqual(brief.omitted_stale, 1)
        self.assertIn("1 dati non aggiornati non sono mostrati", brief.text)

    def test_short_keeps_only_the_top_items_per_section_by_priority(self):
        items = [item("agenda", f"Evento {i}", priority=i) for i in range(6)]
        brief = builder(Source("calendario", items)).build("short")
        self.assertEqual([r.text for r in brief.sections["agenda"]], ["Evento 5", "Evento 4", "Evento 3"])
        self.assertEqual(len(builder(Source("calendario", items)).build("detailed").sections["agenda"]), 6)

    def test_short_has_no_freshness_clutter_and_detailed_has_it(self):
        short = builder(Source("c", [item()])).build("short")
        detailed = builder(Source("c", [item()])).build("detailed")
        self.assertNotIn("[calendario", short.text)
        self.assertIn("[calendario", detailed.text)

    def test_silent_format_produces_text_for_the_screen_and_nothing_to_say_aloud(self):
        brief = builder(Source("c", [item()])).build("silent")
        self.assertIn("Riunione con il team", brief.text)
        self.assertIsNone(brief.spoken_text)

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            builder().build("lunghissimo")

    def test_sections_come_out_in_a_fixed_order(self):
        brief = builder(Source("x", [item("weather", "Sole"), item("agenda", "Riunione"), item("deadlines", "Scade il 30")])).build("short")
        self.assertEqual(list(brief.sections), ["agenda", "deadlines", "weather"])

    def test_an_unknown_section_lands_in_other(self):
        brief = builder(Source("x", [item("astrologia", "Oroscopo")])).build("short")
        self.assertEqual(list(brief.sections), ["other"])


class SpeakerTests(unittest.TestCase):
    def test_on_a_shared_speaker_only_public_items_are_read_aloud(self):
        items = [item("weather", "Sole, 22 gradi", "meteo", sensitivity="public"), item("agenda", "Visita medica", sensitivity="private")]
        brief = builder(Source("x", items), speaker_shared=True).build("short")
        self.assertIn("Sole, 22 gradi", brief.spoken_text)
        self.assertNotIn("medica", brief.spoken_text)
        self.assertIn("1 elementi privati", brief.spoken_text)
        self.assertIn("Visita medica", brief.text)  # lo schermo mostra tutto

    def test_on_a_personal_device_everything_is_spoken(self):
        brief = builder(Source("x", [item(sensitivity="private")]), speaker_shared=False).build("short")
        self.assertIn("Riunione con il team", brief.spoken_text)

    def test_a_shared_speaker_with_only_private_items_says_only_that_there_are_some(self):
        brief = builder(Source("x", [item(sensitivity="private")]), speaker_shared=True).build("short")
        self.assertEqual(brief.spoken_text, "Ci sono 1 elementi privati: li trovi sullo schermo.")


class RedactionTests(unittest.TestCase):
    def test_people_and_organizations_get_consistent_labels_within_one_brief(self):
        items = [
            item("agenda", "Call con Marco Rossi di Acme Srl", people=("Marco Rossi",), organizations=("Acme Srl",)),
            item("deadlines", "Rispondere a Marco Rossi entro venerdi", people=("Marco Rossi",)),
            item("agenda", "Pranzo con Anna Bianchi", people=("Anna Bianchi",)),
        ]
        brief = builder(Source("x", items)).build("detailed", redact=True)
        text = brief.text
        self.assertNotIn("Marco", text)
        self.assertNotIn("Acme", text)
        self.assertNotIn("Anna", text)
        self.assertEqual(text.count("Persona A"), 2)  # la stessa persona, la stessa etichetta
        self.assertIn("Persona B", text)
        self.assertIn("Azienda A", text)

    def test_emails_and_phone_numbers_are_always_covered(self):
        redactor = Redactor()
        result = redactor.apply("Scrivi a marco.rossi@acme.com o chiama +39 333 123 4567")
        self.assertNotIn("acme", result)
        self.assertNotIn("4567", result)
        self.assertIn("[email]", result)
        self.assertIn("[numero]", result)

    def test_the_longer_name_is_replaced_before_a_shorter_one_contained_in_it(self):
        result = Redactor().apply("Marco Rossi e Marco", people=("Marco", "Marco Rossi"))
        self.assertNotIn("Rossi", result)
        self.assertEqual(result.count("Persona"), 2)

    def test_without_redaction_the_text_is_untouched(self):
        brief = builder(Source("x", [item(text="Call con Marco", people=("Marco",))])).build("short", redact=False)
        self.assertIn("Marco", brief.text)

    def test_redaction_can_be_limited_to_people_or_organizations(self):
        only_people = Redactor(redact_organizations=False).apply("Marco di Acme", ("Marco",), ("Acme",))
        self.assertIn("Acme", only_people)
        self.assertNotIn("Marco", only_people)


class LocalSourcesTests(unittest.TestCase):
    def test_reminders_source_reports_only_upcoming_ones_within_the_horizon(self):
        now = datetime.fromtimestamp(NOW)

        class Reminders:
            def list_upcoming(self, limit=10, kind=None):
                return [
                    {"id": 1, "text": "Medicina", "due_at": (now + timedelta(hours=2)).isoformat(), "recur_time": None, "kind": "reminder"},
                    {"id": 2, "text": "Tra una settimana", "due_at": (now + timedelta(days=7)).isoformat(), "recur_time": None, "kind": "reminder"},
                ]

        brief = builder(RemindersSource(Reminders(), horizon_hours=24)).build("detailed")
        self.assertEqual([r.text.split(" - ")[1] for r in brief.sections["deadlines"]], ["Medicina"])
        self.assertEqual(brief.sections["deadlines"][0].item.source, "promemoria")

    def test_todos_source_marks_the_forgotten_ones_and_ranks_them_first(self):
        class Todos:
            def list_pending(self, limit=20):
                return [{"id": 1, "text": "Comprare il latte"}, {"id": 2, "text": "Chiamare l'idraulico"}]

            def list_stale_pending(self, days=3):
                return [{"id": 2, "text": "Chiamare l'idraulico", "created_at": "x"}]

        brief = builder(TodosSource(Todos())).build("short")
        texts = [r.text for r in brief.sections["open_tasks"]]
        self.assertEqual(texts[0], "Chiamare l'idraulico (aperta da giorni)")

    def test_a_reminder_source_over_a_real_reminder_manager(self):
        from core.reminder_manager import ReminderManager

        with tempfile.TemporaryDirectory() as tmp:
            manager = ReminderManager(Path(tmp) / "r.db")
            try:
                manager.add("Prendere la medicina", datetime.fromtimestamp(NOW) + timedelta(hours=3))
                brief = builder(RemindersSource(manager)).build("short")
            finally:
                manager.close()
        self.assertIn("Prendere la medicina", brief.text)

    def test_a_todo_source_over_a_real_todo_manager(self):
        from core.todo_manager import TodoManager

        with tempfile.TemporaryDirectory() as tmp:
            manager = TodoManager(Path(tmp) / "t.db")
            try:
                manager.add("Scrivere il report")
                brief = builder(TodosSource(manager)).build("short")
            finally:
                manager.close()
        self.assertIn("Scrivere il report", brief.text)

    def test_brief_dataclass_is_exposed(self):
        self.assertIsInstance(builder(Source("x", [item()])).build(), Brief)


# ---- meeting copilot --------------------------------------------------------------------------------------------------------


class DocSource:
    def __init__(self, name, titles=None, error=None):
        self.name = name
        self._titles = titles or []
        self._error = error

    def related(self, title, participants):
        if self._error:
            raise self._error
        return list(self._titles)


def meeting(indicator=None, **kwargs):
    state = {"on": True} if indicator is None else indicator
    clock = kwargs.pop("clock", Clock())
    return MeetingCopilot("Revisione trimestrale", ["Anna", "Marco"], lambda: state["on"], clock=clock, **kwargs), state, clock


class PreparationTests(unittest.TestCase):
    def test_related_documents_and_decisions_carry_their_source(self):
        m, _, _ = meeting()
        prep = m.prepare([DocSource("NEST", ["Bilancio Q2.xlsx"])], [DocSource("memoria", ["Si e' deciso di rimandare il lancio"])])
        self.assertEqual(prep.documents[0].source.name, "NEST")
        self.assertEqual(prep.decisions[0].source.name, "memoria")
        self.assertIn("Bilancio Q2.xlsx [NEST]", prep.text())

    def test_with_nothing_found_it_says_so_and_invents_nothing(self):
        m, _, _ = meeting()
        prep = m.prepare([DocSource("NEST", [])], [])
        self.assertEqual((prep.documents, prep.decisions), ((), ()))
        self.assertIn("nessun documento correlato trovato", prep.text())
        self.assertIn("nessuna decisione correlata trovata", prep.text())

    def test_a_failing_source_is_reported_and_the_others_still_contribute(self):
        m, _, _ = meeting()
        prep = m.prepare([DocSource("NEST", error=TimeoutError("lento")), DocSource("Drive", ["Agenda.docx"])], [])
        self.assertEqual([d.title for d in prep.documents], ["Agenda.docx"])
        self.assertIn("'NEST' non ha risposto", prep.text())


class ConsentTests(unittest.TestCase):
    def test_transcription_cannot_start_without_consent(self):
        m, _, _ = meeting()
        with self.assertRaises(ConsentError):
            m.start_transcription()
        self.assertEqual(m.state, MeetingState.PLANNED)

    def test_an_incomplete_consent_is_not_enough(self):
        cases = [
            ("", True, "ho avvisato tutti i partecipanti"),
            ("davide", False, "ho avvisato tutti i partecipanti"),
            ("davide", True, "ok"),
        ]
        for granted_by, informed, statement in cases:
            with self.subTest(granted_by=granted_by, informed=informed):
                m, _, _ = meeting()
                m.record_consent(granted_by, informed, statement)
                with self.assertRaises(ConsentError):
                    m.start_transcription()

    def test_with_evident_consent_and_an_active_indicator_it_starts(self):
        m, _, _ = meeting()
        m.record_consent("davide", True, "Ho avvisato tutti all'inizio della call")
        m.start_transcription()
        self.assertEqual(m.state, MeetingState.TRANSCRIBING)

    def test_an_inactive_indicator_blocks_the_start_even_with_consent(self):
        m, state, _ = meeting({"on": False})
        m.record_consent("davide", True, "Ho avvisato tutti all'inizio della call")
        with self.assertRaises(ConsentError):
            m.start_transcription()

    def test_if_the_indicator_goes_off_mid_meeting_transcription_stops_by_itself(self):
        m, state, _ = meeting()
        m.record_consent("davide", True, "Ho avvisato tutti all'inizio della call")
        m.start_transcription()
        self.assertTrue(m.add_segment("Iniziamo dal punto uno"))
        state["on"] = False
        self.assertFalse(m.add_segment("Questa frase non deve essere registrata"))
        self.assertEqual(m.state, MeetingState.PAUSED)
        self.assertIn("indicatore", m.paused_reason)
        self.assertEqual(m.transcript(), ["Iniziamo dal punto uno"])

    def test_resuming_needs_the_indicator_back_on(self):
        m, state, _ = meeting()
        m.record_consent("davide", True, "Ho avvisato tutti all'inizio della call")
        m.start_transcription()
        state["on"] = False
        m.add_segment("x")
        with self.assertRaises(ConsentError):
            m.resume()
        state["on"] = True
        m.resume()
        self.assertEqual(m.state, MeetingState.TRANSCRIBING)

    def test_resume_when_not_paused_is_an_error(self):
        m, _, _ = meeting()
        with self.assertRaises(ConsentError):
            m.resume()

    def test_segments_are_not_recorded_unless_transcribing(self):
        m, _, _ = meeting()
        self.assertFalse(m.add_segment("una frase"))
        self.assertEqual(m.transcript(), [])

    def test_the_transcript_expires(self):
        m, _, clock = meeting(transcript_ttl_s=3600)
        m.record_consent("davide", True, "Ho avvisato tutti all'inizio della call")
        m.start_transcription()
        m.add_segment("vecchia frase")
        clock.t += 7200
        m.add_segment("frase recente")
        self.assertEqual(m.transcript(), ["frase recente"])

    def test_ending_discards_the_transcript_unless_asked_and_keeps_only_chosen_notes(self):
        m, _, _ = meeting()
        m.record_consent("davide", True, "Ho avvisato tutti all'inizio della call")
        m.start_transcription()
        m.add_segment("contenuto trascritto")
        m.end(keep_notes=["Deciso: rimandare il lancio"])
        self.assertEqual((m.state, m.transcript(), m.retained_notes), (MeetingState.ENDED, [], ["Deciso: rimandare il lancio"]))

    def test_the_user_can_choose_to_keep_the_transcript(self):
        m, _, _ = meeting()
        m.record_consent("davide", True, "Ho avvisato tutti all'inizio della call")
        m.start_transcription()
        m.add_segment("contenuto trascritto")
        m.end(keep_transcript=True)
        self.assertEqual(m.transcript(), ["contenuto trascritto"])


class FollowUpTests(unittest.TestCase):
    NOTES = """Riunione del 12.
AZIONE: preparare il bilancio (a Marco)
TODO - aggiornare il piano @anna
DECISIONE: rimandare il lancio a ottobre
Abbiamo parlato molto del budget e forse bisognerebbe sentire il fornitore.
"""

    def test_actions_are_extracted_only_from_explicit_markers(self):
        follow_ups = MeetingCopilot.extract_follow_ups(self.NOTES)
        self.assertEqual([(f.kind, f.text, f.owner) for f in follow_ups], [
            ("action", "preparare il bilancio", "Marco"),
            ("action", "aggiornare il piano", "anna"),
            ("decision", "rimandare il lancio a ottobre", None),
        ])
        self.assertTrue(all(f.status == "proposed" for f in follow_ups))
        self.assertNotIn("fornitore", " ".join(f.text for f in follow_ups))  # nessuna deduzione da frasi qualunque

    def test_notes_without_markers_yield_nothing(self):
        self.assertEqual(MeetingCopilot.extract_follow_ups("Abbiamo parlato del budget."), [])

    def test_a_follow_up_is_never_sent_without_approval(self):
        sent = []
        follow_up = MeetingCopilot.extract_follow_ups("AZIONE: mandare il riepilogo")[0]
        pending = PendingSend(follow_up, "marco@example.com", "email", lambda c, r, t: sent.append((c, r, t)))
        with self.assertRaises(ApprovalRequiredError):
            pending.send()
        self.assertEqual(sent, [])
        self.assertEqual(follow_up.status, "proposed")

    def test_approval_is_for_this_specific_message_and_sends_exactly_once(self):
        sent = []
        follow_up = MeetingCopilot.extract_follow_ups("AZIONE: mandare il riepilogo")[0]
        pending = PendingSend(follow_up, "marco@example.com", "email", lambda c, r, t: sent.append((c, r, t)))
        pending.approve("davide")
        pending.send()
        self.assertEqual(sent, [("email", "marco@example.com", "mandare il riepilogo")])
        self.assertEqual(follow_up.status, "sent")
        with self.assertRaises(ApprovalRequiredError):
            pending.send()
        self.assertEqual(len(sent), 1)

    def test_an_anonymous_approval_is_refused(self):
        follow_up = MeetingCopilot.extract_follow_ups("AZIONE: x y z")[0]
        pending = PendingSend(follow_up, "a", "email", lambda c, r, t: None)
        with self.assertRaises(ApprovalRequiredError):
            pending.approve("  ")

    def test_a_rejected_follow_up_cannot_be_sent_even_if_it_was_approved_before(self):
        sent = []
        follow_up = MeetingCopilot.extract_follow_ups("AZIONE: mandare il riepilogo")[0]
        pending = PendingSend(follow_up, "a", "email", lambda c, r, t: sent.append(1))
        pending.approve("davide")
        pending.reject()
        with self.assertRaises(ApprovalRequiredError):
            pending.send()
        self.assertEqual((sent, follow_up.status), ([], "rejected"))

    def test_notes_shared_outside_the_meeting_have_participants_and_organizations_covered(self):
        text = MeetingCopilot.redact_notes("Marco di Acme ha proposto di rimandare; Anna era d'accordo", ["Marco", "Anna"], ["Acme"])
        for name in ("Marco", "Anna", "Acme"):
            self.assertNotIn(name, text)
        self.assertIn("Persona A", text)
        self.assertIn("Azienda A", text)


if __name__ == "__main__":
    unittest.main()
