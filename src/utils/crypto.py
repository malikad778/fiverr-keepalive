"""
src/utils/crypto.py - Encrypt/decrypt sensitive data (cookies, tokens)
using Fernet symmetric encryption keyed from SECRET_KEY env var.
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def _get_fernet() -> Fernet:
    """Derive a Fernet key from SECRET_KEY env variable."""
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise EnvironmentError(
            "SECRET_KEY is not set. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    # If already a valid Fernet key (44 base64 chars), use directly
    try:
        key = secret.encode() if isinstance(secret, str) else secret
        return Fernet(key)
    except Exception:
        pass

    # Derive key from passphrase using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"fiverr-keepalive-salt",  # static salt - OK for non-auth use
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return Fernet(key)


def encrypt(data: str) -> str:
    """Encrypt a plaintext string, return base64 ciphertext."""
    f = _get_fernet()
    return f.encrypt(data.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a ciphertext string back to plaintext."""
    f = _get_fernet()
    return f.decrypt(token.encode()).decode()
