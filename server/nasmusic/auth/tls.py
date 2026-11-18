"""Self-signed LAN certificate: generated on first start, pinned by the app via its SHA-256."""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class TLSFiles:
    cert: Path
    key: Path


def paths(data_dir: Path) -> TLSFiles:
    d = data_dir / "tls"
    return TLSFiles(cert=d / "cert.pem", key=d / "key.pem")


def ensure_cert(data_dir: Path, lan_host: str) -> tuple[TLSFiles, bool]:
    """Create the cert + key if missing. Returns (files, created)."""
    files = paths(data_dir)
    if files.cert.exists() and files.key.exists():
        return files, False
    files.cert.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        san: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(lan_host))
    except ValueError:
        san = x509.DNSName(lan_host)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"nas-music {lan_host}")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365 * 25))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(files.key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key_pem)
    files.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return files, True


def fingerprint(cert_path: Path) -> str:
    """SHA-256 of the DER certificate, colon-separated upper-case hex (docs/pairing.md)."""
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02X}" for b in digest)
