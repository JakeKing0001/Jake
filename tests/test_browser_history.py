"""Test unitari per core/browser_history.py::read_recent_history. Il modulo legge dati privati
dell'utente (cronologia di navigazione) da un file sqlite reale di Chrome/Edge e non aveva
ancora nessun test - in particolare la conversione dei timestamp WebKit (microsecondi dal
1601-01-01, non l'epoca Unix), una classica fonte di bug silenziosi da un giorno/anno sbagliato."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import browser_history


def _write_fake_chrome_history(path: Path, rows: list[tuple]) -> None:
    """rows: liste di (title, url, last_visit_time in microsecondi WebKit)."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, title TEXT, url TEXT, last_visit_time INTEGER)")
    connection.executemany("INSERT INTO urls (title, url, last_visit_time) VALUES (?, ?, ?)", rows)
    connection.commit()
    connection.close()


def _webkit_timestamp(dt: datetime) -> int:
    epoch = datetime(1601, 1, 1)
    return int((dt - epoch).total_seconds() * 1_000_000)


class ReadRecentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.history_path = Path(self.tmp_dir.name) / "History"

    def _use_fake_history_file(self, path: Path = None) -> None:
        mock.patch.object(browser_history, "_find_history_file", return_value=path or self.history_path).start()

    def test_no_browser_installed_returns_none(self):
        mock.patch.object(browser_history, "_find_history_file", return_value=None).start()

        self.assertIsNone(browser_history.read_recent_history())

    def test_parses_title_url_and_converts_the_webkit_timestamp(self):
        visited_at = datetime(2024, 6, 19, 12, 0, 0)
        _write_fake_chrome_history(self.history_path, [
            ("Esempio", "https://example.com", _webkit_timestamp(visited_at)),
        ])
        self._use_fake_history_file()

        results = browser_history.read_recent_history()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Esempio")
        self.assertEqual(results[0]["url"], "https://example.com")
        self.assertEqual(results[0]["visited_at"], visited_at.isoformat())

    def test_missing_title_falls_back_to_the_url(self):
        _write_fake_chrome_history(self.history_path, [
            (None, "https://example.com", _webkit_timestamp(datetime(2024, 1, 1))),
        ])
        self._use_fake_history_file()

        results = browser_history.read_recent_history()

        self.assertEqual(results[0]["title"], "https://example.com")

    def test_zero_last_visit_time_yields_none_visited_at_instead_of_the_webkit_epoch(self):
        _write_fake_chrome_history(self.history_path, [("Mai visitato", "https://x.test", 0)])
        self._use_fake_history_file()

        results = browser_history.read_recent_history()

        self.assertIsNone(results[0]["visited_at"])

    def test_results_are_ordered_most_recent_first_and_respect_the_limit(self):
        _write_fake_chrome_history(self.history_path, [
            ("Vecchio", "https://old.test", _webkit_timestamp(datetime(2020, 1, 1))),
            ("Recente", "https://new.test", _webkit_timestamp(datetime(2024, 1, 1))),
            ("Medio", "https://mid.test", _webkit_timestamp(datetime(2022, 1, 1))),
        ])
        self._use_fake_history_file()

        results = browser_history.read_recent_history(limit=2)

        self.assertEqual([r["title"] for r in results], ["Recente", "Medio"])

    def test_corrupted_database_returns_none_instead_of_raising(self):
        self.history_path.write_bytes(b"non e' un database sqlite valido")
        self._use_fake_history_file()

        self.assertIsNone(browser_history.read_recent_history())

    def test_reads_a_copy_not_the_original_file(self):
        """Il browser tiene il file History bloccato mentre gira: read_recent_history deve
        copiarlo prima di leggerlo, mai aprirlo in place (vedi il docstring del modulo)."""
        _write_fake_chrome_history(self.history_path, [("Esempio", "https://example.com", 0)])
        self._use_fake_history_file()
        original_bytes = self.history_path.read_bytes()

        browser_history.read_recent_history()

        self.assertEqual(self.history_path.read_bytes(), original_bytes, "l'originale non deve essere modificato")


class FindHistoryFileTests(unittest.TestCase):
    def test_no_candidate_paths_exist_returns_none(self):
        with mock.patch.object(browser_history, "_CANDIDATE_PATHS", [Path("Z:/non/esiste/History")]):
            self.assertIsNone(browser_history._find_history_file())

    def test_picks_the_most_recently_modified_candidate(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            older = Path(tmp_dir) / "older_history"
            newer = Path(tmp_dir) / "newer_history"
            older.write_bytes(b"x")
            newer.write_bytes(b"x")
            os.utime(older, (1000, 1000))
            os.utime(newer, (2000, 2000))

            with mock.patch.object(browser_history, "_CANDIDATE_PATHS", [older, newer]):
                found = browser_history._find_history_file()

        self.assertEqual(found, newer)


if __name__ == "__main__":
    unittest.main()
