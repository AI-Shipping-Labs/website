"""Relay campaign and newsletter delivery service (Path B).

Campaigns and newsletters are rendered per recipient to preserve personalized
unsubscribe links, email verification CTAs, and re-permission tokens. Delivery
is executed via Relay's transactional send endpoint (/api/transactional/send)
with category="newsletter", headers carrying List-Unsubscribe / List-Unsubscribe-Post,
and a stable idempotency_key to ensure strict deduplication across retries and waves.
"""

from __future__ import annotations

import logging
from email.utils import parseaddr
from typing import Any

import requests
from django.conf import settings

from email_app.services.email_classification import get_sender_for_email_type
from integrations.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TEMPLATE_KEY = "campaign-broadcast"
DEFAULT_CATEGORY = "newsletter"
DEFAULT_TEMPLATE_VERSION = 1


class RelayCampaignError(Exception):
    """Base exception for Relay campaign delivery issues."""


class RelayCampaignDeliveryRejected(RelayCampaignError):
    """Definitive rejection by Relay (e.g. contact suppressed, opt-out, bad request)."""

    def __init__(self, message: str, *, status_code: int = 409, reason: str = ""):
        self.status_code = status_code
        self.reason = reason
        super().__init__(message)


class RelayCampaignDeliveryAmbiguous(RelayCampaignError):
    """Indeterminate transport outcome (e.g. 5xx, timeout, dropped connection)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class RelayCampaignConfigError(RelayCampaignError):
    """Missing or invalid Relay configuration."""


def get_relay_base_url() -> str:
    url = get_config("RELAY_BASE_URL", getattr(settings, "RELAY_BASE_URL", ""))
    if not url:
        cb_conf = getattr(settings, "COMMUNITY_BASE", {})
        url = cb_conf.get("RELAY_BASE_URL", "")
    return (url or "").strip().rstrip("/")


def get_relay_api_key() -> str:
    key = get_config("RELAY_API_KEY", getattr(settings, "RELAY_API_KEY", ""))
    if not key:
        cb_conf = getattr(settings, "COMMUNITY_BASE", {})
        key = cb_conf.get("RELAY_API_KEY", "")
    return (key or "").strip()


def get_relay_template_key() -> str:
    key = get_config("RELAY_CAMPAIGN_TEMPLATE_KEY", DEFAULT_TEMPLATE_KEY)
    return (key or DEFAULT_TEMPLATE_KEY).strip()


def get_relay_from_email() -> str:
    """Resolve the campaign's configured mailbox to its registered Relay ID."""
    sender = get_sender_for_email_type("campaign")
    _name, address = parseaddr(sender)
    return (address or sender).strip().split("@", 1)[0].lower()


def build_relay_unsubscribe_headers(unsubscribe_url: str | None) -> dict[str, str]:
    if not unsubscribe_url:
        return {}
    parts = [f"<{unsubscribe_url}>"]
    mailto = get_config("SES_UNSUBSCRIBE_EMAIL", "").strip()
    if mailto:
        parts.append(f"<mailto:{mailto}>")
    return {
        "List-Unsubscribe": ", ".join(parts),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def ensure_relay_campaign_template(
    base_url: str | None = None,
    api_key: str | None = None,
    template_key: str | None = None,
    session: Any = None,
) -> bool:
    """Ensure that the campaign template exists and is published on Relay."""
    base_url = base_url or get_relay_base_url()
    api_key = api_key or get_relay_api_key()
    template_key = template_key or get_relay_template_key()

    if not base_url or not api_key:
        return False

    client = session or requests.Session()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{base_url}/api/transactional/templates/{template_key}"
    draft_payload = {
        "name": "Campaign Broadcast",
        "description": "Broadcast campaigns and newsletters to members",
        "category": DEFAULT_CATEGORY,
        "subject": "{{ subject }}",
        "html_body": "{{ html_body }}",
        "text_body": "{{ text_body }}",
        "required_context": [],
    }
    try:
        put_resp = client.request("PUT", url, json=draft_payload, headers=headers, timeout=10.0)
        if put_resp.status_code in (200, 201):
            pub_url = f"{base_url}/api/transactional/templates/{template_key}/publish"
            pub_resp = client.request("POST", pub_url, json={}, headers=headers, timeout=10.0)
            return pub_resp.status_code in (200, 201)
        return False
    except Exception as exc:
        logger.warning("Failed to auto-ensure Relay campaign template: %s", exc)
        return False


def send_campaign_delivery_to_relay(
    recipient_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    *,
    idempotency_key: str,
    unsubscribe_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    category: str = DEFAULT_CATEGORY,
    template_version: int = DEFAULT_TEMPLATE_VERSION,
    dry_run: bool | None = None,
    session: Any = None,
    timeout: float | None = None,
) -> str:
    """Send one rendered campaign email via Relay transactional send API.

    Returns the Relay message_id string.
    Raises RelayCampaignDeliveryRejected or RelayCampaignDeliveryAmbiguous on error.
    """
    base_url = get_relay_base_url()
    api_key = get_relay_api_key()
    if not base_url or not api_key:
        raise RelayCampaignConfigError("RELAY_BASE_URL and RELAY_API_KEY must be configured")

    template_key = get_relay_template_key()
    headers = build_relay_unsubscribe_headers(unsubscribe_url)

    if dry_run is None:
        dry_run = bool(get_config("RELAY_TRANSACTIONAL_DRY_RUN", False))

    payload: dict[str, Any] = {
        "email": recipient_email.strip(),
        "template_key": template_key,
        "template_version": template_version,
        "idempotency_key": idempotency_key,
        "context": {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body,
        },
        "category": category,
    }
    if headers:
        payload["headers"] = headers
    if metadata:
        payload["metadata"] = metadata
    from_email = get_relay_from_email()
    if from_email:
        payload["from_email"] = from_email
    if dry_run:
        payload["dry_run"] = True

    http_client = session or requests.Session()
    req_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    request_timeout = timeout or get_config("RELAY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    try:
        request_timeout = float(request_timeout)
    except (TypeError, ValueError):
        request_timeout = DEFAULT_TIMEOUT_SECONDS

    send_url = f"{base_url}/api/transactional/send"
    try:
        response = http_client.request(
            "POST",
            send_url,
            json=payload,
            headers=req_headers,
            timeout=request_timeout,
        )
    except requests.exceptions.Timeout as exc:
        raise RelayCampaignDeliveryAmbiguous(
            f"Relay request timed out after {request_timeout}s: {exc}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RelayCampaignDeliveryAmbiguous(
            f"Relay network connection failed: {exc}"
        ) from exc

    if response.status_code == 404:
        # Check if template not found, attempt to ensure and retry once
        try:
            err_doc = response.json()
        except Exception:
            err_doc = {}
        if isinstance(err_doc, dict) and err_doc.get("template_key") == "not_found":
            logger.info("Relay template %s not found; ensuring and retrying", template_key)
            if ensure_relay_campaign_template(base_url, api_key, template_key, session=http_client):
                try:
                    response = http_client.request(
                        "POST",
                        send_url,
                        json=payload,
                        headers=req_headers,
                        timeout=request_timeout,
                    )
                except requests.exceptions.RequestException as exc:
                    raise RelayCampaignDeliveryAmbiguous(
                        f"Relay network connection failed on retry: {exc}"
                    ) from exc

    if response.status_code in (200, 202):
        try:
            data = response.json()
        except Exception as exc:
            raise RelayCampaignDeliveryAmbiguous(
                f"Relay returned 200/202 with non-JSON content: {exc}"
            ) from exc

        msg_obj = data.get("message") or {}
        raw_id = msg_obj.get("id")
        if raw_id is not None:
            return str(raw_id)
        if dry_run:
            return f"relay-dry-run-{idempotency_key}"
        raise RelayCampaignDeliveryAmbiguous("Relay send succeeded but returned no message ID")

    if response.status_code == 409:
        try:
            err_data = response.json()
        except Exception:
            err_data = {}
        err_obj = err_data.get("error") or {}
        reason = err_obj.get("reason") or err_obj.get("code") or "suppressed"
        raise RelayCampaignDeliveryRejected(
            f"Relay suppressed send: {reason}",
            status_code=409,
            reason=str(reason),
        )

    if response.status_code in (400, 422):
        try:
            err_data = response.json()
        except Exception:
            err_data = response.text[:200]
        raise RelayCampaignDeliveryRejected(
            f"Relay rejected payload (HTTP {response.status_code}): {err_data}",
            status_code=response.status_code,
        )

    # 408, 425, 429, 500, 502, 503, 504 or any other unexpected code
    raise RelayCampaignDeliveryAmbiguous(
        f"Relay returned HTTP {response.status_code}: {getattr(response, 'text', '')[:200]}",
        status_code=response.status_code,
    )
