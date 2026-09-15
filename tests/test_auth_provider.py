"""Test unitari per core/auth_provider.py (F1.4.4, fase 9/10 del piano multi-device). Mai
importare/chiamare winsdk per davvero (vedi core/windows_hello.py sul perche' quella API mostra
un prompt reale e blocca la suite se toccata per sbaglio) - WindowsHelloProvider e' verificato
mockando core.windows_hello, mai chiamando l'adapter senza mock."""
import unittest
from unittest import mock

from core.auth_provider import AuthProvider, PasskeyProvider, WindowsHelloProvider


class WindowsHelloProviderTests(unittest.TestCase):
    def test_name_is_windows_hello(self):
        self.assertEqual(WindowsHelloProvider.name, "windows_hello")

    def test_is_a_real_auth_provider(self):
        self.assertIsInstance(WindowsHelloProvider(), AuthProvider)

    def test_is_available_forwards_to_the_real_module(self):
        with mock.patch("core.windows_hello.is_available", return_value=True) as fake:
            self.assertTrue(WindowsHelloProvider().is_available())
        fake.assert_called_once()

    def test_is_available_reflects_a_false_result_too(self):
        with mock.patch("core.windows_hello.is_available", return_value=False):
            self.assertFalse(WindowsHelloProvider().is_available())

    def test_verify_forwards_the_reason_to_the_real_module(self):
        with mock.patch("core.windows_hello.verify", return_value=True) as fake:
            result = WindowsHelloProvider().verify("Spegni il computer")
        fake.assert_called_once_with("Spegni il computer")
        self.assertTrue(result)

    def test_verify_reflects_a_false_result_too(self):
        with mock.patch("core.windows_hello.verify", return_value=False):
            self.assertFalse(WindowsHelloProvider().verify("qualunque cosa"))


class PasskeyProviderTests(unittest.TestCase):
    """F1.4.4: dichiaratamente inerte finche' non esiste un vero registro di passkey - vedi il
    docstring del modulo sul perche' is_available() non deve mai mentire dicendo 'si''."""

    def test_name_is_passkey(self):
        self.assertEqual(PasskeyProvider.name, "passkey")

    def test_is_a_real_auth_provider(self):
        self.assertIsInstance(PasskeyProvider(), AuthProvider)

    def test_is_available_is_honestly_false(self):
        self.assertFalse(PasskeyProvider().is_available())

    def test_verify_is_honestly_false_even_if_called_directly(self):
        """Anche bypassando is_available() (un chiamante che non lo controllasse prima), verify()
        non deve MAI dichiarare un successo che non ha potuto verificare per davvero."""
        self.assertFalse(PasskeyProvider().verify("qualunque cosa"))


class AuthProviderContractTests(unittest.TestCase):
    def test_cannot_instantiate_the_abstract_base_directly(self):
        with self.assertRaises(TypeError):
            AuthProvider()  # type: ignore[abstract]


if __name__ == "__main__":
    unittest.main()
