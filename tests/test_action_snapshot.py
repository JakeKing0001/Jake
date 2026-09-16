"""Test unitari per core/action_snapshot.py (F1.3.4, "salvare snapshot minimo prima dell'azione,
rispettando privacy e dimensione" - vedi il docstring del modulo per come si distingue da
F1.3.5/core/undo_store.py)."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from core.action_snapshot import ActionSnapshot, SnapshotStore, capture_snapshot


class CaptureSnapshotTests(unittest.TestCase):
    def test_captures_content_and_size_of_a_small_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nota.txt"
            path.write_bytes(b"contenuto vero")

            snapshot = capture_snapshot("action-1", str(path))

            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.action_id, "action-1")
            self.assertEqual(snapshot.content, b"contenuto vero")
            self.assertEqual(snapshot.size_bytes, len(b"contenuto vero"))
            self.assertEqual(snapshot.path, str(path))

    def test_a_nonexistent_path_returns_none(self):
        self.assertIsNone(capture_snapshot("action-1", "C:\\non\\esiste\\davvero.txt"))

    def test_a_directory_is_never_captured(self):
        """Fuori scope dichiarato nel docstring del modulo: 'minimo' esclude una copia ricorsiva
        di una cartella di dimensione non limitata."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(capture_snapshot("action-1", tmp))

    def test_a_file_over_the_size_cap_is_not_captured(self):
        """Onesto None, mai un valore TRONCATO - uno snapshot corrotto sarebbe peggio di nessuno
        snapshot se mai usato per un ripristino."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grande.bin"
            path.write_bytes(b"x" * 100)

            self.assertIsNone(capture_snapshot("action-1", str(path), max_bytes=50))

    def test_a_file_exactly_at_the_cap_is_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "esatto.bin"
            path.write_bytes(b"x" * 50)

            snapshot = capture_snapshot("action-1", str(path), max_bytes=50)

            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.size_bytes, 50)

    def test_a_file_just_over_the_cap_is_not_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sopra.bin"
            path.write_bytes(b"x" * 51)

            self.assertIsNone(capture_snapshot("action-1", str(path), max_bytes=50))

    def test_private_mode_returns_none_without_touching_the_filesystem(self):
        """Stessa garanzia gia' data altrove per la modalita' privata (core/action_ledger.py::
        record, JakeCore._answer_inner): esce PRIMA di leggere qualunque byte, non solo prima di
        persistere il risultato - verificato che Path.stat()/Path.read_bytes() non vengono MAI
        chiamati, non solo che il risultato e' None."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "segreto.txt"
            path.write_bytes(b"dato sensibile")

            with mock.patch.object(Path, "stat", side_effect=AssertionError("non deve leggere il filesystem")):
                with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("non deve leggere il filesystem")):
                    result = capture_snapshot("action-1", str(path), private=True)

            self.assertIsNone(result)

    def test_captured_at_defaults_to_the_real_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_bytes(b"x")

            snapshot = capture_snapshot("action-1", str(path))

            self.assertGreater(snapshot.captured_at, 0)

    def test_an_explicit_now_is_used_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_bytes(b"x")

            snapshot = capture_snapshot("action-1", str(path), now=1000.0)

            self.assertEqual(snapshot.captured_at, 1000.0)

    def test_an_unreadable_file_returns_none_not_a_crash(self):
        """Un file cancellato/spostato per una race tra il controllo e la lettura vera non deve
        mai propagare un'eccezione tecnica a un chiamante che si aspetta un ActionSnapshot o None."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_bytes(b"x")

            with mock.patch.object(Path, "read_bytes", side_effect=OSError("simulato")):
                result = capture_snapshot("action-1", str(path))

            self.assertIsNone(result)


class SnapshotStoreTests(unittest.TestCase):
    def test_a_saved_snapshot_can_be_retrieved_by_action_id(self):
        store = SnapshotStore()
        snapshot = ActionSnapshot(action_id="action-1", path="x", content=b"y", size_bytes=1, captured_at=1.0)

        store.save(snapshot)

        self.assertIs(store.get("action-1"), snapshot)

    def test_an_unknown_action_id_returns_none(self):
        self.assertIsNone(SnapshotStore().get("non-esiste"))

    def test_saving_a_new_snapshot_for_the_same_action_id_replaces_the_old_one(self):
        store = SnapshotStore()
        store.save(ActionSnapshot(action_id="action-1", path="x", content=b"primo", size_bytes=5, captured_at=1.0))
        store.save(ActionSnapshot(action_id="action-1", path="x", content=b"secondo", size_bytes=7, captured_at=2.0))

        self.assertEqual(store.get("action-1").content, b"secondo")

    def test_concurrent_saves_and_reads_from_real_threads_never_corrupt_the_store(self):
        store = SnapshotStore()
        errors: list = []
        errors_lock = threading.Lock()

        def _save_and_check(index: int):
            action_id = f"action-{index}"
            content = f"file-{index}".encode()
            store.save(ActionSnapshot(action_id=action_id, path="x", content=content, size_bytes=len(content), captured_at=1.0))
            retrieved = store.get(action_id)
            if retrieved is None or retrieved.content != content:
                with errors_lock:
                    errors.append(index)

        threads = [threading.Thread(target=_save_and_check, args=(i,)) for i in range(100)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
