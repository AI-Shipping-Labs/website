"""Authenticated encryption for trigger signing secrets and snapshots."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PREFIX = "fernet:v1:"


def _fernet() -> Fernet:
    key = hashlib.sha256(f"trigger-secrets:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(secret: str) -> str:
    if not secret:
        raise ValueError("Signing secret cannot be blank.")
    return PREFIX + _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext or not ciphertext.startswith(PREFIX):
        raise ImproperlyConfigured("Trigger signing secret is not encrypted.")
    try:
        return _fernet().decrypt(ciphertext[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured("Trigger signing secret cannot be decrypted.") from exc
