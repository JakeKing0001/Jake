"""F7.1.4/F7.2 (Companion Mobile MVP): certificato TLS locale del server companion. Crittografia VERA
(`cryptography`), un vero handshake TLS end-to-end con un client reale che pinna l'impronta (lo stesso modello di
fiducia trust-on-first-use che l'app companion userebbe), non solo la generazione del certificato in isolamento."""
import shutil
import socket
import ssl
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec

from core.companion_tls import (
    build_server_context, current_fingerprint, ensure_certificate, fingerprint_sha256, generate_self_signed,
)


class GenerateSelfSignedTests(unittest.TestCase):
    def test_produces_a_usable_pem_certificate_and_key(self):
        cert_pem, key_pem = generate_self_signed()
        self.assertIn(b"BEGIN CERTIFICATE", cert_pem)
        self.assertIn(b"BEGIN PRIVATE KEY", key_pem)

    def test_two_calls_never_produce_the_same_key_or_fingerprint(self):
        cert_a, _ = generate_self_signed()
        cert_b, _ = generate_self_signed()
        self.assertNotEqual(fingerprint_sha256(cert_a), fingerprint_sha256(cert_b))

    def test_carries_the_requested_hostnames_and_ip_addresses_as_subject_alternative_names(self):
        from cryptography import x509
        cert_pem, _ = generate_self_signed(hostnames=("jake.local", "pc-di-davide"), ip_addresses=("127.0.0.1", "192.168.1.50"))
        certificate = x509.load_pem_x509_certificate(cert_pem)
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual(set(san.get_values_for_type(x509.DNSName)), {"jake.local", "pc-di-davide"})
        self.assertEqual({str(ip) for ip in san.get_values_for_type(x509.IPAddress)}, {"127.0.0.1", "192.168.1.50"})

    def test_an_injected_key_is_reused_verbatim_test_only(self):
        """`key` e' solo per i test (determinismo): il materiale della chiave privata torna IDENTICO, anche se il
        certificato che la avvolge resta comunque unico ogni volta (numero di serie/validita' calcolati ora)."""
        key = ec.generate_private_key(ec.SECP256R1())
        _, key_a = generate_self_signed(key=key)
        _, key_b = generate_self_signed(key=key)
        self.assertEqual(key_a, key_b)


class FingerprintTests(unittest.TestCase):
    def test_the_fingerprint_is_a_colon_separated_uppercase_hex_sha256(self):
        cert_pem, _ = generate_self_signed()
        fingerprint = fingerprint_sha256(cert_pem)
        parts = fingerprint.split(":")
        self.assertEqual(len(parts), 32)  # SHA-256 = 32 byte
        self.assertTrue(all(len(p) == 2 and p == p.upper() for p in parts))

    def test_the_same_certificate_always_gives_the_same_fingerprint(self):
        cert_pem, _ = generate_self_signed()
        self.assertEqual(fingerprint_sha256(cert_pem), fingerprint_sha256(cert_pem))


class EnsureCertificateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_companion_tls_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cert_path = self.tmp / "cert.pem"
        self.key_path = self.tmp / "key.pem"

    def test_generates_both_files_on_first_call(self):
        self.assertIsNone(current_fingerprint(self.cert_path))
        ensure_certificate(self.cert_path, self.key_path)
        self.assertTrue(self.cert_path.is_file())
        self.assertTrue(self.key_path.is_file())
        self.assertIsNotNone(current_fingerprint(self.cert_path))

    def test_a_second_call_never_regenerates_the_certificate(self):
        """Persistente tra riavvii - un'app companion che ha gia' fissato l'impronta non deve vederne una
        diversa la prossima volta che Jake riparte, altrimenti il trust-on-first-use si romperebbe a ogni
        riavvio del PC."""
        ensure_certificate(self.cert_path, self.key_path)
        first = current_fingerprint(self.cert_path)
        ensure_certificate(self.cert_path, self.key_path)
        second = current_fingerprint(self.cert_path)
        self.assertEqual(first, second)

    def test_never_leaves_a_half_written_file_behind(self):
        ensure_certificate(self.cert_path, self.key_path)
        self.assertEqual(list(self.tmp.glob("*.tmp")), [], "nessun file temporaneo deve restare dopo la scrittura atomica")


class RealHandshakeTests(unittest.TestCase):
    """Un vero server TLS (socket reale, non un doppio) e un vero client che pinna l'impronta - il modello di
    fiducia che l'app companion userebbe davvero durante il pairing (F7.2)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_companion_tls_handshake_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cert_path, self.key_path = ensure_certificate(self.tmp / "cert.pem", self.tmp / "key.pem")
        self.fingerprint = current_fingerprint(self.cert_path)

    def _serve_once(self, context: ssl.SSLContext) -> int:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        import threading

        def _accept_one():
            raw, _ = listener.accept()
            with context.wrap_socket(raw, server_side=True) as tls:
                request_bytes = tls.recv(4096)
                if request_bytes:
                    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            listener.close()

        threading.Thread(target=_accept_one, daemon=True).start()
        return port

    def test_a_client_that_pins_the_exact_fingerprint_connects_successfully(self):
        server_context = build_server_context(self.cert_path, self.key_path)
        port = self._serve_once(server_context)

        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE  # trust-on-first-use: si verifica l'IMPRONTA, non la catena CA
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with client_context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                presented = ssl.DER_cert_to_PEM_cert(tls.getpeercert(binary_form=True)).encode()
                self.assertEqual(fingerprint_sha256(presented), self.fingerprint)
                tls.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                response = tls.recv(4096)
        self.assertIn(b"200 OK", response)

    def test_a_client_pinning_a_different_fingerprint_would_reject_the_connection(self):
        """Non un test dell'API urllib (che non espone un pinning cosi' semplice) - dimostra invece che
        l'impronta presentata da un secondo certificato (di un server DIVERSO, magari malevolo) non
        coincide con quella fissata durante il pairing: e' la STESSA verifica che un client reale farebbe
        prima di fidarsi della connessione."""
        other_cert_path, other_key_path = ensure_certificate(self.tmp / "other_cert.pem", self.tmp / "other_key.pem")
        server_context = build_server_context(other_cert_path, other_key_path)
        port = self._serve_once(server_context)

        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with client_context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                presented = ssl.DER_cert_to_PEM_cert(tls.getpeercert(binary_form=True)).encode()
                self.assertNotEqual(fingerprint_sha256(presented), self.fingerprint)
                # Manda comunque la richiesta (come il test gemello sopra) invece di chiudere subito dopo
                # l'handshake: evita la stessa corsa di chiusura gia' documentata altrove nel progetto (un FIN
                # invece di un RST quando il server e' ancora in attesa di leggere), qui puramente cosmetica.
                tls.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                tls.recv(4096)


class BuildServerContextTests(unittest.TestCase):
    def test_uses_at_least_tls_1_2(self):
        with tempfile.TemporaryDirectory(prefix="jake_companion_tls_ctx_test_") as tmp:
            cert_path, key_path = ensure_certificate(Path(tmp) / "cert.pem", Path(tmp) / "key.pem")
            context = build_server_context(cert_path, key_path)
            self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)


if __name__ == "__main__":
    unittest.main()
