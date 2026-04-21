"""
AES-256 encryption for WEEX API keys.
The ENCRYPTION_KEY env var is the only secret needed to decrypt keys.
It never touches the database.
"""

import os
import base64
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY environment variable is not set")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _get_fernet().decrypt(value.encode()).decode()


def generate_key() -> str:
    """Run once to generate a key — paste output into Railway env vars."""
    return Fernet.generate_key().decode()
