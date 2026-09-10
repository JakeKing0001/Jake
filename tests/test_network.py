"""Test unitari per core/network.py: nessuna suite esisteva finora. socket.create_connection e'
mockato, nessuna vera connessione di rete in un test automatico."""
import socket
import unittest
from unittest import mock

from core.network import is_online


class IsOnlineTests(unittest.TestCase):
    def test_returns_true_when_the_connection_succeeds(self):
        with mock.patch("socket.create_connection", return_value=mock.MagicMock(__enter__=mock.Mock(return_value=mock.Mock()), __exit__=mock.Mock(return_value=False))):
            self.assertTrue(is_online())

    def test_returns_false_on_connection_refused(self):
        with mock.patch("socket.create_connection", side_effect=ConnectionRefusedError()):
            self.assertFalse(is_online())

    def test_returns_false_on_timeout(self):
        with mock.patch("socket.create_connection", side_effect=socket.timeout()):
            self.assertFalse(is_online())

    def test_returns_false_on_a_generic_os_error(self):
        with mock.patch("socket.create_connection", side_effect=OSError("rete non raggiungibile")):
            self.assertFalse(is_online())

    def test_forwards_host_port_and_timeout_to_create_connection(self):
        with mock.patch("socket.create_connection") as create_connection:
            create_connection.return_value.__enter__ = mock.Mock(return_value=mock.Mock())
            create_connection.return_value.__exit__ = mock.Mock(return_value=False)
            is_online(host="1.1.1.1", port=443, timeout=5.0)
        create_connection.assert_called_once_with(("1.1.1.1", 443), timeout=5.0)


if __name__ == "__main__":
    unittest.main()
