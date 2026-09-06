"""Test per la ricerca file limitata nel tempo e in ampiezza (v3.1, vedi skills/find_file.py).

Usa una gerarchia temporanea isolata (non l'home reale) cosi' i test restano deterministici
e veloci indipendentemente da quanto sia affollato il Desktop di chi li esegue."""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from skills import find_file
from skills.find_file import FindFileSkill


class FindFileLevelOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "shallow.txt").write_text("x", encoding="utf-8")
        deep_dir = self.root / "a" / "b" / "c"
        deep_dir.mkdir(parents=True)
        (deep_dir / "deep.txt").write_text("x", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "should_not_be_found.txt").write_text("x", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "also_skipped.txt").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_shallow_file_with_explicit_path(self):
        result = FindFileSkill().execute({"name": "shallow.txt", "path": str(self.root)})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["results"]), 1)
        self.assertTrue(result.data["results"][0].endswith("shallow.txt"))

    def test_finds_deeper_file_when_no_shallow_match(self):
        result = FindFileSkill().execute({"name": "deep.txt", "path": str(self.root)})
        self.assertTrue(result.success)
        self.assertTrue(result.data["results"][0].endswith(str(Path("a", "b", "c", "deep.txt"))))

    def test_stops_at_shallowest_matching_level(self):
        """Se un file con lo stesso 'ingrediente' nel nome esiste sia in superficie che in
        profondita', la ricerca si ferma al livello piu' superficiale: non scende oltre."""
        (self.root / "a" / "match_shallow.txt").write_text("x", encoding="utf-8")
        (self.root / "a" / "b" / "c" / "match_deep.txt").write_text("x", encoding="utf-8")
        result = FindFileSkill().execute({"name": "match_", "path": str(self.root)})
        self.assertTrue(result.success)
        names = [Path(p).name for p in result.data["results"]]
        self.assertIn("match_shallow.txt", names)
        self.assertNotIn("match_deep.txt", names)

    def test_skips_noise_directories(self):
        result = FindFileSkill().execute({"name": "should_not_be_found", "path": str(self.root)})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")
        result2 = FindFileSkill().execute({"name": "also_skipped", "path": str(self.root)})
        self.assertFalse(result2.success)

    def test_missing_parameters(self):
        result = FindFileSkill().execute({"path": str(self.root)})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_bad_explicit_path(self):
        result = FindFileSkill().execute({"name": "x", "path": str(self.root / "does_not_exist")})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "PATH_NOT_FOUND")


class FindFileTimeBudgetTests(unittest.TestCase):
    """Il caso peggiore (file davvero assente ovunque) deve restare limitato nel tempo, mai
    bloccarsi indefinitamente: qui si abbassa il budget a poche centesimi di secondo cosi' il
    test resta veloce, verificando solo che il limite venga davvero rispettato."""

    def test_never_exceeds_time_budget_by_much(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(30):
                (root / f"folder{i}").mkdir()
                (root / f"folder{i}" / f"file{i}.txt").write_text("x", encoding="utf-8")

            with mock.patch.object(find_file, "TIME_BUDGET_SECONDS", 0.05):
                start = time.monotonic()
                result = FindFileSkill().execute({"name": "nonexistent_needle", "path": str(root)})
                elapsed = time.monotonic() - start

            self.assertFalse(result.success)
            self.assertEqual(result.error, "NOT_FOUND")
            self.assertLess(elapsed, 2.0)  # ben oltre il budget (0.05s) ma lontanissimo da un blocco


if __name__ == "__main__":
    unittest.main()
