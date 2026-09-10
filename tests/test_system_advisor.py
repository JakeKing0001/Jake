"""Test per gli avvisi proattivi (vedi core/system_advisor.py): un avviso scatta una sola
volta per 'episodio' sotto soglia, non ad ogni controllo, e si riarma quando si torna sopra
soglia."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.system_advisor import SystemAdvisor


class SystemAdvisorBatteryTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.advisor = SystemAdvisor(on_advisory=self.messages.append, enabled=False)

    def _battery(self, percent, plugged):
        return SimpleNamespace(percent=percent, power_plugged=plugged)

    def test_warns_once_when_low_and_unplugged(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, False)):
            self.advisor._check_battery()
            self.advisor._check_battery()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("10%", self.messages[0])

    def test_does_not_warn_when_plugged_in(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, True)):
            self.advisor._check_battery()
        self.assertEqual(self.messages, [])

    def test_does_not_warn_above_threshold(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(80, False)):
            self.advisor._check_battery()
        self.assertEqual(self.messages, [])

    def test_rearms_after_recovering_above_threshold(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, False)):
            self.advisor._check_battery()
        with mock.patch("psutil.sensors_battery", return_value=self._battery(50, False)):
            self.advisor._check_battery()
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, False)):
            self.advisor._check_battery()
        self.assertEqual(len(self.messages), 2)

    def test_no_battery_sensor_is_silent(self):
        with mock.patch("psutil.sensors_battery", return_value=None):
            self.advisor._check_battery()
        self.assertEqual(self.messages, [])


class SystemAdvisorDiskTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.advisor = SystemAdvisor(on_advisory=self.messages.append, enabled=False)

    def _usage(self, free_gb):
        return SimpleNamespace(free=free_gb * (1024 ** 3), total=100 * (1024 ** 3), used=0, percent=0)

    def test_warns_once_when_disk_almost_full(self):
        with mock.patch("psutil.disk_usage", return_value=self._usage(1.0)):
            self.advisor._check_disk()
            self.advisor._check_disk()
        self.assertEqual(len(self.messages), 1)

    def test_does_not_warn_with_plenty_of_space(self):
        with mock.patch("psutil.disk_usage", return_value=self._usage(50.0)):
            self.advisor._check_disk()
        self.assertEqual(self.messages, [])


class FakeTodoManager:
    def __init__(self, stale: list = None):
        self._stale = stale or []

    def list_stale_pending(self, days=3):
        return list(self._stale)


class SystemAdvisorStaleTodoTests(unittest.TestCase):
    def setUp(self):
        self.messages = []

    def _advisor(self, stale):
        return SystemAdvisor(on_advisory=self.messages.append, enabled=False, todo_manager=FakeTodoManager(stale))

    def test_no_todo_manager_is_silent(self):
        advisor = SystemAdvisor(on_advisory=self.messages.append, enabled=False)
        advisor._check_stale_todos()
        self.assertEqual(self.messages, [])

    def test_single_stale_todo_is_announced_once(self):
        advisor = self._advisor([{"id": 1, "text": "pagare la bolletta", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("pagare la bolletta", self.messages[0])

    def test_no_stale_todos_is_silent(self):
        advisor = self._advisor([])
        advisor._check_stale_todos()
        self.assertEqual(self.messages, [])

    def test_multiple_stale_todos_are_summarized_in_one_message(self):
        advisor = self._advisor([
            {"id": 1, "text": "vecchia", "created_at": "2020-01-01"},
            {"id": 2, "text": "meno vecchia", "created_at": "2020-06-01"},
        ])
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("2 attivita", self.messages[0])
        self.assertIn("vecchia", self.messages[0])

    def test_new_stale_todo_is_announced_even_after_an_earlier_one_was_already_warned(self):
        advisor = self._advisor([{"id": 1, "text": "prima", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        advisor.todo_manager = FakeTodoManager([
            {"id": 1, "text": "prima", "created_at": "2020-01-01"},
            {"id": 2, "text": "seconda", "created_at": "2020-02-01"},
        ])
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 2)
        self.assertIn("seconda", self.messages[1])

    def test_todo_no_longer_stale_can_be_warned_about_again_later(self):
        """Se una todo esce dalla lista (completata) e un'altra, diversa, diventa stale in
        seguito, non deve restare "silenziata" per sempre solo perche' condivide un vecchio id
        gia' visto in passato con un contesto diverso."""
        advisor = self._advisor([{"id": 1, "text": "prima", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        advisor.todo_manager = FakeTodoManager([])  # completata
        advisor._check_stale_todos()
        advisor.todo_manager = FakeTodoManager([{"id": 1, "text": "prima", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 2)


class SystemAdvisorDownloadsClutterTests(unittest.TestCase):
    """F6 (Proactive Intelligence & Autonomy: "digital housekeeping... prima come suggerimenti"
    - vedi ROADMAP.md). File veri su disco temporaneo (mai la vera cartella Download
    dell'utente): dimensione e data di modifica reali, non mockate, perche' e' esattamente
    cio' che _check_downloads_clutter legge davvero (os.stat, non il contenuto)."""

    def setUp(self):
        self.messages = []
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_downloads_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _advisor(self):
        return SystemAdvisor(on_advisory=self.messages.append, enabled=False, downloads_dir=self.tmp_dir)

    def _write_file(self, name: str, size_bytes: int, age_days: float = 0) -> None:
        path = self.tmp_dir / name
        path.write_bytes(b"0" * size_bytes)
        if age_days:
            old_time = time.time() - age_days * 86400
            os.utime(path, (old_time, old_time))

    def test_missing_downloads_folder_is_silent(self):
        advisor = SystemAdvisor(on_advisory=self.messages.append, enabled=False, downloads_dir=self.tmp_dir / "non_esiste")

        advisor._check_downloads_clutter()

        self.assertEqual(self.messages, [])

    def test_small_recent_folder_is_silent(self):
        self._write_file("appunti.txt", size_bytes=1024)
        advisor = self._advisor()

        advisor._check_downloads_clutter()

        self.assertEqual(self.messages, [])

    def test_warns_once_when_total_size_exceeds_the_threshold(self):
        # Soglia abbassata via mock invece di scrivere davvero un file da 5+ GB su disco.
        self._write_file("grande.bin", size_bytes=2048)
        advisor = self._advisor()

        with mock.patch("core.system_advisor.DOWNLOADS_SIZE_WARN_GB", 2048 / (1024 ** 3)):
            advisor._check_downloads_clutter()
            advisor._check_downloads_clutter()

        self.assertEqual(len(self.messages), 1)
        self.assertIn("GB", self.messages[0])

    def test_warns_when_many_old_files_accumulate_even_if_small(self):
        for i in range(25):
            self._write_file(f"vecchio_{i}.txt", size_bytes=10, age_days=60)
        advisor = self._advisor()

        advisor._check_downloads_clutter()

        self.assertEqual(len(self.messages), 1)
        self.assertIn("vecchi", self.messages[0])

    def test_rearms_after_cleanup(self):
        small_threshold_gb = 2048 / (1024 ** 3)
        with mock.patch("core.system_advisor.DOWNLOADS_SIZE_WARN_GB", small_threshold_gb):
            self._write_file("grande.bin", size_bytes=2048)
            advisor = self._advisor()
            advisor._check_downloads_clutter()

            (self.tmp_dir / "grande.bin").unlink()
            advisor._check_downloads_clutter()  # sotto soglia ora: riarma
            self._write_file("grande2.bin", size_bytes=2048)
            advisor._check_downloads_clutter()

        self.assertEqual(len(self.messages), 2)

    def test_subfolders_are_not_scanned(self):
        """Solo il livello piu' alto: una sottocartella con un file grande non deve far scattare
        l'avviso, ne' far fallire lo scan (entry.is_file() e' False per una directory)."""
        small_threshold_gb = 2048 / (1024 ** 3)
        subfolder = self.tmp_dir / "sottocartella"
        subfolder.mkdir()
        (subfolder / "grande.bin").write_bytes(b"0" * 2048)
        advisor = self._advisor()

        with mock.patch("core.system_advisor.DOWNLOADS_SIZE_WARN_GB", small_threshold_gb):
            advisor._check_downloads_clutter()

        self.assertEqual(self.messages, [])


class SystemAdvisorExpiredMemoryPurgeTests(unittest.TestCase):
    """F5 (Memory 2.0, scadenza): il 'futuro hook di manutenzione' gia' previsto nel docstring di
    MemoryManager.purge_expired() - senza questo, un ricordo con ttl_days (skills/remember.py,
    collegato in questa stessa sessione) smetteva di COMPARIRE in recall() alla scadenza ma
    restava per sempre sul disco, mai davvero rimosso. Usa un MemoryManager vero su file
    temporaneo, non un finto: e' proprio l'interazione reale con purge_expired() a contare."""

    def setUp(self):
        from core.memory_manager import MemoryManager

        self.messages = []
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_system_advisor_memory_test_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")

    def _advisor(self, memory_manager=None) -> SystemAdvisor:
        return SystemAdvisor(on_advisory=self.messages.append, enabled=False, memory_manager=memory_manager)

    def test_without_a_memory_manager_the_hook_is_a_silent_no_op(self):
        advisor = self._advisor(memory_manager=None)
        advisor._purge_expired_memories()  # non deve sollevare
        self.assertEqual(self.messages, [])

    def test_an_expired_memory_is_actually_removed_from_disk(self):
        self.memory_manager.remember("oggi piove", "vero", ttl_days=0.0000001)
        time.sleep(0.01)
        advisor = self._advisor(memory_manager=self.memory_manager)

        advisor._purge_expired_memories()

        self.assertEqual(self.memory_manager.recall(key="oggi piove", include_expired=True), [])

    def test_a_non_expired_memory_is_left_alone(self):
        self.memory_manager.remember("compleanno", "5 marzo")  # nessun ttl
        self.memory_manager.remember("prossima settimana", "vero", ttl_days=7)
        advisor = self._advisor(memory_manager=self.memory_manager)

        advisor._purge_expired_memories()

        self.assertIsNotNone(self.memory_manager.recall(key="compleanno"))
        self.assertNotEqual(self.memory_manager.recall(key="prossima settimana"), [])

    def test_purging_is_silent_not_an_advisory(self):
        """A differenza di batteria/disco/Download, la pulizia dei ricordi scaduti non deve
        interrompere l'utente: e' manutenzione di routine attesa (l'utente stesso ha impostato
        quella scadenza al momento di salvare il ricordo), non 'disordine' da segnalare."""
        self.memory_manager.remember("oggi piove", "vero", ttl_days=0.0000001)
        time.sleep(0.01)
        advisor = self._advisor(memory_manager=self.memory_manager)

        advisor._purge_expired_memories()

        self.assertEqual(self.messages, [])


if __name__ == "__main__":
    unittest.main()
