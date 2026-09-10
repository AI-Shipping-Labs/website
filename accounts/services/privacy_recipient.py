"""Authenticated encryption for short-lived privacy delivery recipients."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PREFIX = "fernet:v1:"


def _fernet() -> Fernet:
    key = hashlib.sha256(
        f"privacy-completion-recipient:{settings.SECRET_KEY}".encode(),
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_recipient(email: str) -> str:
    normalized = (email or "").strip()
    if not normalized:
        raise ValueError("Privacy completion recipient cannot be blank.")
    return PREFIX + _fernet().encrypt(normalized.encode()).decode()


def decrypt_recipient(ciphertext: str) -> str:
    if not ciphertext or not ciphertext.startswith(PREFIX):
        raise ImproperlyConfigured("Privacy completion recipient is not encrypted.")
    try:
        return _fernet().decrypt(ciphertext[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "Privacy completion recipient cannot be decrypted.",
        ) from exc
