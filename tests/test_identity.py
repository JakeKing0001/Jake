"""Test unitari per core/identity.py (F1.4.2, prima fetta - "distinguere identita' Windows...
dispositivo..."). current_windows_user() non e' mockato qui: getpass.getuser() legge lo stesso
account Windows sotto cui gira la suite, un valore reale e stabile per tutta l'esecuzione."""
import getpass
import unittest

from core.identity import current_windows_user


class CurrentWindowsUserTests(unittest.TestCase):
    def test_returns_a_non_empty_string(self):
        self.assertIsInstance(current_windows_user(), str)
        self.assertTrue(current_windows_user())

    def test_matches_getpass_getuser(self):
        self.assertEqual(current_windows_user(), getpass.getuser())

    def test_is_stable_across_calls(self):
        """Costante per tutta la vita del processo - a differenza di current_device_id, non
        c'e' nessun contextvar/stato da propagare."""
        self.assertEqual(current_windows_user(), current_windows_user())


if __name__ == "__main__":
    unittest.main()
