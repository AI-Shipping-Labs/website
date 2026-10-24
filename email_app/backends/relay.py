"""Django email backend delivering messages through Relay (A1.1 / AC 3).

Replaces Django's default SMTP / SES backend so all core mail paths
(send_mail, mail_admins, EmailMessage) go through Relay.
Uses Relay's 'raw_transactional' template to deliver rendered emails with
parity, fail-loud error handling, and idempotency preservation.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Sequence
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage

from integrations.config import get_config

logger = logging.getLogger(__name__)

RAW_TRANSACTIONAL_TEMPLATE_KEY = "raw_transactional"
DEFAULT_TIMEOUT_SECONDS = 15


class RelayEmailBackendError(RuntimeError):
    """Raised when Relay returns an error or rejects a message."""


class RelayEmailBackend(BaseEmailBackend):
    """Django EMAIL_BACKEND sending core Django mail via Relay transactional API."""

    def __init__(
        self,
        fail_silently: bool = False,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        **kwargs,
    ):
        super().__init__(fail_silently=fail_silently, **kwargs)
        raw_url = base_url or get_config("RELAY_BASE_URL", getattr(settings, "RELAY_BASE_URL", "")) or ""
        self.base_url = str(raw_url).rstrip("/")
        self.api_key = str(api_key or get_config("RELAY_API_KEY", getattr(settings, "RELAY_API_KEY", "")) or "")
        self.timeout = timeout

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        if not email_messages:
            return 0

        self._validate_configuration()

        sent_count = 0
        for message in email_messages:
            try:
                sent = self._send_one(message)
                if sent:
                    sent_count += 1
            except Exception as exc:
                logger.error("RelayEmailBackend failed to send message '%s': %s", getattr(message, "subject", ""), exc)
                if not self.fail_silently:
                    raise

        return sent_count

    def _validate_configuration(self) -> None:
        if not self.base_url:
            raise ImproperlyConfigured("RELAY_BASE_URL must be configured for RelayEmailBackend")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ImproperlyConfigured("RELAY_BASE_URL must be an absolute HTTP(S) URL")
        if not self.api_key:
            raise ImproperlyConfigured("RELAY_API_KEY must be configured for RelayEmailBackend")

    def _resolve_sender_id(self, from_email: str) -> str:
        addr = from_email.lower()
        if "welcome@" in addr:
            return "welcome"
        if "content@" in addr:
            return "content"
        if "noreply@" in addr:
            return "noreply"
        if addr in {"welcome", "content", "noreply"}:
            return addr
        return "noreply"

    def _send_one(self, message: EmailMessage) -> bool:
        recipients = message.recipients()
        if not recipients:
            return False

        text_body = message.body or ""
        html_body = ""
        if hasattr(message, "alternatives") and message.alternatives:
            for alt_content, alt_mimetype in message.alternatives:
                if alt_mimetype == "text/html":
                    html_body = alt_content
                    break

        if not html_body and getattr(message, "content_subtype", "plain") == "html":
            html_body = message.body
            text_body = ""

        from_email = message.from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@aishippinglabs.com")
        sender_id = self._resolve_sender_id(from_email)

        for recipient in recipients:
            idempotency_key = self._extract_idempotency_key(message, recipient)
            payload = {
                "email": recipient,
                "template_key": RAW_TRANSACTIONAL_TEMPLATE_KEY,
                "template_version": 1,
                "idempotency_key": idempotency_key,
                "context": {
                    "subject": message.subject or "",
                    "text_body": text_body,
                    "html_body": html_body,
                },
                "from_email": sender_id,
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            url = f"{self.base_url}/api/transactional/send"
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                raise RelayEmailBackendError(f"Relay is unreachable: {exc}") from exc

            if response.status_code not in (200, 202):
                logger.error(
                    "Relay returned HTTP %s for recipient %s (template=%s): %s",
                    response.status_code,
                    recipient,
                    RAW_TRANSACTIONAL_TEMPLATE_KEY,
                    response.text[:200],
                )
                raise RelayEmailBackendError(
                    f"Relay rejected email with status {response.status_code}: {response.text[:200]}"
                )

        return True

    def _extract_idempotency_key(self, message: EmailMessage, recipient: str) -> str:
        if hasattr(message, "extra_headers") and message.extra_headers:
            for k, v in message.extra_headers.items():
                if k.lower() in ("idempotency-key", "x-idempotency-key"):
                    return str(v)

        digest = hashlib.sha256(
            f"{recipient}:{message.subject}:{message.body}".encode("utf-8")
        ).hexdigest()[:32]
        return f"core:{digest}"
