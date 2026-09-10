"""Test unitari per skills/security_utils.py: nessuna suite esisteva finora. File veri su disco
temporaneo per CHECK_FILE_HASH (l'hash SHA-256 vero, non simulato)."""
import tempfile
import unittest
from pathlib import Path

from skills.security_utils import CheckFileHashSkill, CheckPasswordStrengthSkill


class CheckPasswordStrengthTests(unittest.TestCase):
    def test_missing_password_fails(self):
        result = CheckPasswordStrengthSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_short_simple_password_is_very_weak(self):
        result = CheckPasswordStrengthSkill().execute({"password": "abc"})
        self.assertEqual(result.data["strength"], "molto debole")
        self.assertEqual(result.data["score"], 0)

    def test_a_long_password_with_everything_is_very_strong(self):
        result = CheckPasswordStrengthSkill().execute({"password": "Abcdefghijkl1!"})
        self.assertEqual(result.data["strength"], "molto forte")
        self.assertEqual(result.data["score"], 5)

    def test_length_alone_without_variety_scores_low(self):
        result = CheckPasswordStrengthSkill().execute({"password": "aaaaaaaaaaaaaaaa"})
        # >=8 e >=12 caratteri contano (2 punti), ma nessuna maiuscola/cifra/simbolo
        self.assertEqual(result.data["score"], 2)

    def test_score_increases_monotonically_with_more_variety(self):
        scores = [
            CheckPasswordStrengthSkill().execute({"password": pw}).data["score"]
            for pw in ("abc", "abcdefgh", "abcdefghijkl", "abcdefghijklA", "abcdefghijklA1", "abcdefghijklA1!")
        ]
        self.assertEqual(scores, sorted(scores))


class CheckFileHashTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_security_utils_test_"))

    def test_missing_path_fails(self):
        result = CheckFileHashSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = CheckFileHashSkill().execute({"path": str(self.tmp_dir / "non_esiste.txt")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_computes_the_real_sha256_of_a_file(self):
        import hashlib
        file_path = self.tmp_dir / "documento.txt"
        content = b"contenuto di prova"
        file_path.write_bytes(content)

        result = CheckFileHashSkill().execute({"path": str(file_path)})

        self.assertTrue(result.success)
        self.assertEqual(result.data["sha256"], hashlib.sha256(content).hexdigest())

    def test_identical_content_produces_the_same_hash(self):
        (self.tmp_dir / "a.txt").write_bytes(b"stesso contenuto")
        (self.tmp_dir / "b.txt").write_bytes(b"stesso contenuto")
        hash_a = CheckFileHashSkill().execute({"path": str(self.tmp_dir / "a.txt")}).data["sha256"]
        hash_b = CheckFileHashSkill().execute({"path": str(self.tmp_dir / "b.txt")}).data["sha256"]
        self.assertEqual(hash_a, hash_b)

    def test_different_content_produces_a_different_hash(self):
        (self.tmp_dir / "a.txt").write_bytes(b"contenuto a")
        (self.tmp_dir / "b.txt").write_bytes(b"contenuto b")
        hash_a = CheckFileHashSkill().execute({"path": str(self.tmp_dir / "a.txt")}).data["sha256"]
        hash_b = CheckFileHashSkill().execute({"path": str(self.tmp_dir / "b.txt")}).data["sha256"]
        self.assertNotEqual(hash_a, hash_b)


if __name__ == "__main__":
    unittest.main()
