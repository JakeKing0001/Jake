"""Test unitari per core/resource_lock.py (F1.8.1, fase 7/10 del piano multi-device). Thread
VERI, non solo letti a codice - un readers-writer lock che "sembra" corretto puo' comunque
lasciar passare due scrittori insieme per un dettaglio di implementazione, verificabile solo
forzando per davvero la concorrenza (stesso principio gia' usato altrove in questa sessione per
le race condition, es. tests/test_device_registry.py)."""
import threading
import time
import unittest

from core.resource_lock import ResourceLockManager


class _ConcurrencyTracker:
    """Registra il numero MASSIMO di esecuzioni sovrapposte osservate - se un lock lascia
    passare due scrittori insieme anche solo per un istante, il picco lo rivela."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def enter(self):
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def exit(self):
        with self._lock:
            self.current -= 1


class WriterSerializationTests(unittest.TestCase):
    def test_two_writers_on_the_same_key_never_overlap(self):
        manager = ResourceLockManager()
        tracker = _ConcurrencyTracker()

        def _work():
            with manager.acquire_write("filesystem:/a.txt"):
                tracker.enter()
                time.sleep(0.05)
                tracker.exit()

        threads = [threading.Thread(target=_work) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(tracker.peak, 1, "due scrittori sulla stessa resource key si sono sovrapposti")

    def test_writers_on_different_keys_do_not_block_each_other(self):
        manager = ResourceLockManager()
        barrier = threading.Barrier(2, timeout=5)
        errors = []

        def _work(key):
            try:
                with manager.acquire_write(key):
                    barrier.wait()  # entrambi devono essere DENTRO insieme, o questo va in timeout
            except threading.BrokenBarrierError:
                errors.append(key)

        threads = [threading.Thread(target=_work, args=(key,)) for key in ("filesystem:/a", "filesystem:/b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], "scrittori su resource key diverse si sono bloccati a vicenda")

    def test_write_lock_is_released_even_if_the_body_raises(self):
        manager = ResourceLockManager()
        with self.assertRaises(ValueError):
            with manager.acquire_write("k"):
                raise ValueError("boom")

        acquired_again = manager._writer_lock_for("k").acquire(timeout=2)
        self.assertTrue(acquired_again, "il lock non e' stato rilasciato dopo un'eccezione nel blocco with")
        manager._writer_lock_for("k").release()


class ReaderConcurrencyTests(unittest.TestCase):
    def test_multiple_readers_on_the_same_key_can_run_concurrently(self):
        manager = ResourceLockManager()
        barrier = threading.Barrier(3, timeout=5)
        errors = []

        def _read():
            try:
                with manager.acquire_read("filesystem:/a"):
                    barrier.wait()  # tutti e tre devono essere DENTRO insieme
            except threading.BrokenBarrierError:
                errors.append(True)

        threads = [threading.Thread(target=_read) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], "tre lettori sulla stessa resource key non sono riusciti a procedere insieme")

    def test_read_lock_is_released_even_if_the_body_raises(self):
        manager = ResourceLockManager()
        with self.assertRaises(ValueError):
            with manager.acquire_read("k"):
                raise ValueError("boom")

        acquired_write = manager._writer_lock_for("k").acquire(timeout=2)
        self.assertTrue(acquired_write, "il contatore lettori non e' stato decrementato dopo un'eccezione")
        manager._writer_lock_for("k").release()


class ReaderWriterExclusionTests(unittest.TestCase):
    def test_a_writer_waits_for_an_in_progress_reader_to_finish(self):
        manager = ResourceLockManager()
        order = []
        reader_started = threading.Event()
        release_reader = threading.Event()

        def _reader():
            with manager.acquire_read("k"):
                order.append("reader_start")
                reader_started.set()
                release_reader.wait(timeout=5)
                order.append("reader_end")

        def _writer():
            reader_started.wait(timeout=5)
            with manager.acquire_write("k"):
                order.append("writer_start")

        reader_thread = threading.Thread(target=_reader)
        writer_thread = threading.Thread(target=_writer)
        reader_thread.start()
        reader_started.wait(timeout=5)
        writer_thread.start()
        time.sleep(0.1)  # da' tempo al writer di tentare acquire_write e restare bloccato

        self.assertNotIn("writer_start", order, "lo scrittore e' entrato mentre un lettore era ancora dentro")

        release_reader.set()
        reader_thread.join(timeout=5)
        writer_thread.join(timeout=5)

        self.assertEqual(order, ["reader_start", "reader_end", "writer_start"])

    def test_a_writer_waits_for_all_readers_not_just_the_first(self):
        """Il caso che una implementazione ingenua (un solo contatore senza attendere OGNI
        lettore) potrebbe far passare per errore: due lettori, lo scrittore deve aspettare che
        ENTRAMBI abbiano finito, non solo il primo ad arrivare."""
        manager = ResourceLockManager()
        order = []
        both_readers_in = threading.Barrier(2, timeout=5)
        release_readers = threading.Event()

        def _reader(name):
            with manager.acquire_read("k"):
                order.append(f"{name}_start")
                both_readers_in.wait()
                release_readers.wait(timeout=5)
                order.append(f"{name}_end")

        def _writer():
            with manager.acquire_write("k"):
                order.append("writer_start")

        r1 = threading.Thread(target=_reader, args=("r1",))
        r2 = threading.Thread(target=_reader, args=("r2",))
        r1.start()
        r2.start()
        # both_readers_in.wait() dentro _reader() sblocca solo quando ENTRAMBI sono passati da
        # acquire_read() - da quel momento in poi e' sicuro far partire lo scrittore, sapendo che
        # non puo' essersi intrufolato tra il primo lettore e il secondo.
        deadline = time.time() + 5
        while len([entry for entry in order if entry.endswith("_start")]) < 2:
            if time.time() > deadline:
                self.fail("i due lettori non sono mai entrati entrambi")
            time.sleep(0.01)

        writer_thread = threading.Thread(target=_writer)
        writer_thread.start()
        time.sleep(0.1)

        self.assertNotIn("writer_start", order, "lo scrittore e' entrato mentre un secondo lettore era ancora dentro")

        release_readers.set()
        r1.join(timeout=5)
        r2.join(timeout=5)
        writer_thread.join(timeout=5)

        self.assertIn("writer_start", order)
        self.assertLess(order.index("r1_end"), order.index("writer_start"))
        self.assertLess(order.index("r2_end"), order.index("writer_start"))

    def test_a_reader_waits_for_an_in_progress_writer_to_finish(self):
        manager = ResourceLockManager()
        order = []
        writer_started = threading.Event()
        release_writer = threading.Event()

        def _writer():
            with manager.acquire_write("k"):
                order.append("writer_start")
                writer_started.set()
                release_writer.wait(timeout=5)
                order.append("writer_end")

        def _reader():
            writer_started.wait(timeout=5)
            with manager.acquire_read("k"):
                order.append("reader_start")

        writer_thread = threading.Thread(target=_writer)
        reader_thread = threading.Thread(target=_reader)
        writer_thread.start()
        writer_started.wait(timeout=5)
        reader_thread.start()
        time.sleep(0.1)

        self.assertNotIn("reader_start", order, "il lettore e' entrato mentre uno scrittore era ancora dentro")

        release_writer.set()
        writer_thread.join(timeout=5)
        reader_thread.join(timeout=5)

        self.assertEqual(order, ["writer_start", "writer_end", "reader_start"])

    def test_readers_can_proceed_after_a_writer_releases(self):
        manager = ResourceLockManager()
        with manager.acquire_write("k"):
            pass  # rilasciato subito uscendo dal blocco with

        acquired = []

        def _reader():
            with manager.acquire_read("k"):
                acquired.append(True)

        thread = threading.Thread(target=_reader)
        thread.start()
        thread.join(timeout=2)

        self.assertEqual(acquired, [True])


class DifferentKeysAreIndependentTests(unittest.TestCase):
    def test_a_write_lock_on_one_key_does_not_exist_for_another(self):
        manager = ResourceLockManager()
        with manager.acquire_write("filesystem:/a"):
            acquired = []

            def _other_key_writer():
                with manager.acquire_write("filesystem:/b"):
                    acquired.append(True)

            thread = threading.Thread(target=_other_key_writer)
            thread.start()
            thread.join(timeout=2)

            self.assertEqual(acquired, [True], "una resource key diversa non deve mai essere bloccata")


if __name__ == "__main__":
    unittest.main()
