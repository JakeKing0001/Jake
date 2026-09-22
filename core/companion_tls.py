"""Certificato TLS locale per il server companion (F7.1.4/F7.2, Companion Mobile MVP): un dispositivo companion
VERO (un telefono, una macchina diversa dal PC) non puo' raggiungere Jake su un'interfaccia diversa da 127.0.0.1
senza TLS - `core/companion_guard.py::check_bind_policy` lo impone gia' ("un bind LAN senza TLS non deve esistere
nemmeno per un istante"). Fino a questo incremento pero' NULLA in `JakeCore` costruiva mai un `tls_context`: il
server companion poteva quindi SOLO ascoltare in loopback - un limite reale, non solo teorico, per un MVP
"Android-first" (il telefono e il PC sono macchine diverse per definizione).

Nessuna PKI, nessuna CA: un certificato AUTOFIRMATO generato UNA volta e persistito
(data/jake_companion_cert.pem/.key). Una chiave privata TLS NON e' un segreto nello stesso senso di
admin_passphrase/un token per-dispositivo - il suo scopo e' proprio essere presentata in chiaro a chiunque si
connetta durante l'handshake, cifrarla a riposo con DPAPI non la proteggerebbe da niente (chi legge il file la
legge comunque, chi intercetta la rete non la vede mai: solo la chiave PUBBLICA/il certificato viaggiano
sull'handshake). Il modello di fiducia e' invece "trust on first use", lo stesso gia' familiare da SSH o da
qualunque altra app di pairing locale: `current_fingerprint()` da' l'impronta SHA-256 che l'app companion mostra
durante il pairing, l'utente la confronta a schermo (il PC la stampa/la mostra nello stesso momento) - non un
certificato firmato da una CA pubblica, impraticabile per un indirizzo di rete locale."""
from __future__ import annotations

import datetime
import ipaddress
import ssl
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.x509.oid import NameOID

DEFAULT_CERT_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_companion_cert.pem"
DEFAULT_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_companion_key.pem"
# 825 giorni: sotto il tetto di 828 che Chrome/Safari/Android impongono a un certificato foglia, cosi' un client
# che (in futuro) validasse anche la scadenza oltre all'impronta non la trovi mai rifiutata per questo motivo.
CERT_VALIDITY_DAYS = 825
_lock = threading.Lock()


def generate_self_signed(
    hostnames: tuple[str, ...] = ("jake.local",), ip_addresses: tuple[str, ...] = ("127.0.0.1",),
    key: EllipticCurvePrivateKey | None = None,
) -> tuple[bytes, bytes]:
    """(pem_cert, pem_key). EC P-256: piu' leggero di RSA, supportato da ogni client TLS moderno (Android incluso
    da anni). `key` e' iniettabile solo per i test (determinismo/velocita'), mai passato da chi chiama per davvero."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jake Companion Server")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName(host) for host in hostnames] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ip_addresses]
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))  # margine per un orologio client leggermente indietro
        .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return cert_pem, key_pem


def fingerprint_sha256(cert_pem: bytes) -> str:
    """Impronta esadecimale a coppie separate da ':' ("AA:BB:...") - lo stesso formato che un browser o `ssh-
    keygen -lf` mostrerebbero, quella che l'utente confronta a schermo durante il pairing (trust-on-first-use,
    vedi il docstring del modulo)."""
    certificate = x509.load_pem_x509_certificate(cert_pem)
    digest = certificate.fingerprint(hashes.SHA256())
    return ":".join(f"{byte:02X}" for byte in digest)


def ensure_certificate(
    cert_path: Path = DEFAULT_CERT_PATH, key_path: Path = DEFAULT_KEY_PATH,
    hostnames: tuple[str, ...] = ("jake.local",), ip_addresses: tuple[str, ...] = ("127.0.0.1",),
) -> tuple[Path, Path]:
    """Genera il certificato SOLO se non esiste ancora: persistente tra riavvii, cosi' un'app companion che ha
    gia' fissato l'impronta non ne vede una diversa ogni volta che Jake riparte (romperebbe il trust-on-first-use
    a ogni riavvio del PC). Scrittura atomica (stesso schema gia' usato altrove nel progetto: file temporaneo poi
    `replace()`, mai un file a meta' scritto se il processo si interrompe a meta')."""
    with _lock:
        if cert_path.is_file() and key_path.is_file():
            return cert_path, key_path
        cert_pem, key_pem = generate_self_signed(hostnames, ip_addresses)
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        for path, data in ((cert_path, cert_pem), (key_path, key_pem)):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return cert_path, key_path


def build_server_context(cert_path: Path = DEFAULT_CERT_PATH, key_path: Path = DEFAULT_KEY_PATH) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    return context


def current_fingerprint(cert_path: Path = DEFAULT_CERT_PATH) -> str | None:
    """None se il certificato non e' ancora stato generato - non un errore, e' lo stato prima del primo avvio con
    TLS attivo."""
    if not cert_path.is_file():
        return None
    return fingerprint_sha256(cert_path.read_bytes())
