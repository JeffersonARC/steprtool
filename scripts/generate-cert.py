"""Generate a self-signed certificate for steprtool's HTTPS server.

Used automatically on first run if the configured cert/key don't exist,
and runnable standalone to regenerate.
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_cert(cert_path: Path, key_path: Path) -> None:
    """Write a self-signed cert and key. Overwrites if either exists."""
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    hostname = socket.gethostname()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jefferson ARC"),
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
    ])

    sans: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    try:
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except ValueError:
                continue
    except socket.gaierror:
        pass
    # Deduplicate while preserving order.
    seen = set()
    unique_sans: list[x509.GeneralName] = []
    for s in sans:
        key_repr = (type(s).__name__, str(getattr(s, "value", s)))
        if key_repr not in seen:
            seen.add(key_repr)
            unique_sans.append(s)

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(unique_sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def ensure_cert(cert_path: Path, key_path: Path) -> None:
    """Generate cert/key only if either file is missing."""
    if cert_path.exists() and key_path.exists():
        return
    generate_cert(cert_path, key_path)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Generate steprtool self-signed cert.")
    p.add_argument("--cert", default="certs/cert.pem")
    p.add_argument("--key", default="certs/key.pem")
    args = p.parse_args()
    generate_cert(Path(args.cert), Path(args.key))
    print(f"Wrote {args.cert} and {args.key}")
