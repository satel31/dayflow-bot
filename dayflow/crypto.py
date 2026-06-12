from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


ENCRYPTED_PREFIX = "fernet:"


def encrypt_text(value: str, key: str) -> str:
    if not key:
        return value
    token = Fernet(key.encode("ascii")).encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_text(value: str, key: str) -> str:
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    if not key:
        raise RuntimeError("DATA_ENCRYPTION_KEY is required to decrypt stored data.")
    try:
        return Fernet(key.encode("ascii")).decrypt(
            value.removeprefix(ENCRYPTED_PREFIX).encode("ascii")
        ).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Failed to decrypt stored data.") from exc
