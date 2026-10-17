"""Symmetric encryption for stored secrets (v12) — Fernet, never logged.

SMTP/IMAP passwords, Forpay credentials and portal passwords are stored
ENCRYPTED in a ``*_ciphertext`` column and are write-only in the API (responses
expose ``has_secret: bool`` only). This module is the single seam for that.

Key: ``settings.CREDENTIALS_KEY`` (a urlsafe-base64 32-byte Fernet key) when
set; otherwise a key DERIVED from ``SECRET_KEY`` (``urlsafe_b64(SHA-256(...))``)
so local dev works with zero configuration. Production must set
``CREDENTIALS_KEY`` explicitly and back it up — losing it makes every stored
secret unreadable, and rotating ``SECRET_KEY`` without it would too.

A value may be a ``str`` or a JSON-serialisable ``dict`` (Forpay's secret
bundle). The type survives the round trip: the plaintext carries a one-char
tag (``s:`` / ``j:``) so ``decrypt_secret`` returns what was encrypted.

Nothing here ever logs or echoes a value; errors name the problem, not the data.
"""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_STR_TAG = "s:"
_JSON_TAG = "j:"


class SecretDecryptionError(ValueError):
    """The token is not decryptable with the configured key (wrong key / tampered)."""


def derive_key(secret: str) -> bytes:
    """A valid Fernet key derived from an arbitrary string (SHA-256, urlsafe-b64)."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


@lru_cache(maxsize=4)
def _fernet_for(credentials_key: str, secret_key: str) -> Fernet:
    key = credentials_key.strip().encode("ascii") if credentials_key.strip() else derive_key(secret_key)
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:  # malformed CREDENTIALS_KEY
        raise ValueError(
            "CREDENTIALS_KEY is not a valid Fernet key (32 url-safe base64-encoded bytes)"
        ) from exc


def _fernet() -> Fernet:
    # Read the settings on every call (cached per value pair) so tests that
    # monkeypatch ``settings.CREDENTIALS_KEY`` see the new key.
    return _fernet_for(settings.CREDENTIALS_KEY or "", settings.SECRET_KEY or "")


def encrypt_secret(value: str | dict[str, Any]) -> str:
    """Encrypt a string or a JSON-able dict; returns an ASCII Fernet token."""
    if isinstance(value, str):
        plaintext = _STR_TAG + value
    elif isinstance(value, dict):
        plaintext = _JSON_TAG + json.dumps(value, separators=(",", ":"), sort_keys=True)
    else:
        raise TypeError("encrypt_secret accepts str or dict only")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str | dict[str, Any]:
    """Decrypt a token from :func:`encrypt_secret`, returning the original type."""
    if not token:
        raise SecretDecryptionError("empty secret token")
    try:
        plaintext = _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SecretDecryptionError("secret could not be decrypted with the configured key") from exc
    if plaintext.startswith(_JSON_TAG):
        return json.loads(plaintext[len(_JSON_TAG):])
    if plaintext.startswith(_STR_TAG):
        return plaintext[len(_STR_TAG):]
    # Not produced by this module (no tag): hand it back verbatim.
    return plaintext


def has_secret(token: str | None) -> bool:
    """What the API exposes instead of the secret: is one stored?"""
    return bool(token)


__all__ = [
    "SecretDecryptionError",
    "decrypt_secret",
    "derive_key",
    "encrypt_secret",
    "has_secret",
]
