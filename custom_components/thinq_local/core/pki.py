"""Certificate authority for the local ThinQ "cloud".

A ThinQ2 Wi-Fi module ships with no trust root. During provisioning it fetches
one from the API server and asks that server to sign its own client CSR, so a
CA we generate ourselves is all it takes to become its cloud.

Losing the CA means every provisioned appliance must be re-provisioned: they
pin it. It is therefore created once and never silently regenerated.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_COMMON_NAME = "ThinQ Local CA"
VALIDITY = timedelta(days=3650)
# Every device certificate gets this serial; the firmware does not care and
# the reference implementation (rethink) has always used it.
DEVICE_CERT_SERIAL = 0x64

_HOSTNAME = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$", re.I
)


def is_plausible_hostname(name: str) -> bool:
    return 0 < len(name) <= 253 and bool(_HOSTNAME.match(name))


def _now() -> datetime:
    # Naive UTC: accepted by every cryptography release, old and new.
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _not_after(cert: x509.Certificate) -> datetime:
    # `not_valid_after` is deprecated in newer cryptography releases
    aware = getattr(cert, "not_valid_after_utc", None)
    return aware.replace(tzinfo=None) if aware else cert.not_valid_after


def _key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def normalize_csr(csr: str) -> bytes:
    """Return a parseable PEM for whatever the modem put in the JSON body."""
    text = csr.replace("\\n", "\n").strip()
    if "BEGIN" not in text:
        body = "".join(text.split())
        lines = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
        text = (
            "-----BEGIN CERTIFICATE REQUEST-----\n"
            f"{lines}\n-----END CERTIFICATE REQUEST-----"
        )
    return (text + "\n").encode()


class CertAuthority:
    """Blocking (CPU and file I/O) - call from an executor, never the loop."""

    def __init__(self, key: rsa.RSAPrivateKey, cert: x509.Certificate, leaf_dir: Path):
        self._key = key
        self._cert = cert
        self._leaf_dir = leaf_dir
        self.cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()

    @classmethod
    def load_or_create(cls, directory: Path) -> "CertAuthority":
        directory.mkdir(parents=True, exist_ok=True)
        key_file = directory / "ca.key"
        cert_file = directory / "ca.cert"
        leaf_dir = directory / "leaf"
        leaf_dir.mkdir(exist_ok=True)

        if key_file.exists() != cert_file.exists():
            raise RuntimeError(
                f"{directory} holds only half of the CA key/certificate pair; "
                "refusing to create a new CA over it"
            )

        if key_file.exists():
            key = serialization.load_pem_private_key(key_file.read_bytes(), None)
            cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
            if key.public_key().public_numbers() != cert.public_key().public_numbers():
                raise RuntimeError("the CA private key does not match its certificate")
            return cls(key, cert, leaf_dir)

        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + VALIDITY)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        _write_private(key_file, _key_pem(key))
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return cls(key, cert, leaf_dir)

    def server_cert_file(self, hostname: str) -> Path:
        """Path of a combined key+certificate PEM for `hostname`, minted on demand.

        The CA itself is never served as the TLS certificate: some modems
        reject a leaf that is also their trust anchor.
        """
        if not is_plausible_hostname(hostname):
            raise ValueError(f"not a hostname: {hostname!r}")
        path = self._leaf_dir / f"{hostname.lower()}.pem"
        if path.exists():
            try:
                cert = x509.load_pem_x509_certificate(path.read_bytes())
                if _not_after(cert) - _now() > timedelta(days=30):
                    return path
            except ValueError:
                pass

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
            )
            .issuer_name(self._cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + VALIDITY)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self._key.public_key()),
                critical=False,
            )
            .sign(self._key, hashes.SHA256())
        )
        _write_private(
            path, _key_pem(key) + cert.public_bytes(serialization.Encoding.PEM)
        )
        return path

    def sign_csr(self, csr: str) -> str:
        """Sign a modem's CSR. Only its subject and public key are honoured."""
        request = x509.load_pem_x509_csr(normalize_csr(csr))
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(request.subject)
            .issuer_name(self._cert.subject)
            .public_key(request.public_key())
            .serial_number(DEVICE_CERT_SERIAL)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + VALIDITY)
            .sign(self._key, hashes.SHA256())
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode()
