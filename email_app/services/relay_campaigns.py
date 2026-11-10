"""Relay campaign queueing, aggregate stats, and transactional delivery."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import uuid
from dataclasses import replace
from email.utils import parseaddr
from typing import Any

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email

from email_app.services.email_classification import get_sender_for_email_type
from email_app.services.relay_payload import build_campaign_payload
from integrations.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TEMPLATE_KEY = "campaign-broadcast"
DEFAULT_CATEGORY = "newsletter"
DEFAULT_TEMPLATE_VERSION = 2
RELAY_CAMPAIGN_TIMEOUT_SECONDS = 30.0
RELAY_CAMPAIGN_AUDIENCE = "aisl"
RELAY_CAMPAIGN_CLIENT = "aisl-website"
RELAY_CAMPAIGN_MEMBER_BATCH_SIZE = 50
RELAY_TEST_CAMPAIGN_MIN_RECIPIENTS = 200
RELAY_TEST_CAMPAIGN_MAX_RECIPIENTS = 500
RELAY_TEST_CAMPAIGN_ALLOWED_DOMAINS = frozenset({
    "aishippinglabs.com", "datatalks.club", "dtcdev.click",
})
RELAY_TEST_CAMPAIGN_RESERVED_SUFFIXES = (".test", ".example", ".invalid", ".localhost")


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


class RelayCampaignEdgeBlocked(RelayCampaignError):
    """The edge (CDN/WAF) refused the call with a non-Relay 403 page.

    Relay answers its own rejections with JSON. An HTML 403 means the request
    never reached Relay, so nothing was accepted and the send is retryable.
    """

    def __init__(self, message: str, *, status_code: int = 403):
        self.status_code = status_code
        super().__init__(message)


class RelayCampaignConfigError(RelayCampaignError):
    """Missing or invalid Relay configuration."""


class RelayCampaignApiError(RelayCampaignError):
    """Relay campaign API failed or returned an invalid response."""


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
    mailto = get_config("RELAY_UNSUBSCRIBE_EMAIL", "").strip()
    if mailto:
        parts.append(f"<mailto:{mailto}>")
    return {
        "List-Unsubscribe": ", ".join(parts),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def send_campaign_delivery_to_relay(
    recipient_email: str,
    subject: str,
    body_markdown: str,
    *,
    idempotency_key: str,
    unsubscribe_url: str | None = None,
    verify_email_url: str | None = None,
    footer_note: str | None = None,
    metadata: dict[str, Any] | None = None,
    category: str = DEFAULT_CATEGORY,
    template_version: int = DEFAULT_TEMPLATE_VERSION,
    dry_run: bool | None = None,
    session: Any = None,
    timeout: float | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
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

    payload = build_campaign_payload(
        recipient_email, template_key, template_version, idempotency_key, category,
        context={
            "subject": subject,
            "body_markdown": body_markdown,
            "footer_note": footer_note or "",
            "verify_email_url": verify_email_url or "",
            "unsubscribe_url": unsubscribe_url or "",
        },
        options={
            "headers": headers, "metadata": metadata,
            "cc": cc, "bcc": bcc, "from_email": get_relay_from_email(),
            "dry_run": dry_run,
        },
    )

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

    if response.status_code == 403 and _is_edge_html_page(response):
        raise RelayCampaignEdgeBlocked(
            "Relay edge returned an HTML 403; request never reached Relay",
        )

    # 408, 425, 429, 500, 502, 503, 504 or any other unexpected code
    raise RelayCampaignDeliveryAmbiguous(
        f"Relay returned HTTP {response.status_code}: {getattr(response, 'text', '')[:200]}",
        status_code=response.status_code,
    )


def _is_edge_html_page(response) -> bool:
    """True when a response is an edge error page instead of a Relay JSON body."""
    content_type = str((getattr(response, "headers", None) or {}).get("Content-Type", ""))
    if "json" in content_type.lower():
        return False
    try:
        response.json()
    except Exception:
        return True
    return False


def _campaign_api_request(
    method, path, *, payload=None, params=None, session=None, allow_not_found=False,
):
    """Call Relay's campaign API and fail without a provider fallback."""
    base_url = get_relay_base_url()
    api_key = get_relay_api_key()
    if not base_url or not api_key:
        raise RelayCampaignConfigError("RELAY_BASE_URL and RELAY_API_KEY must be configured")
    timeout = get_config("RELAY_TIMEOUT_SECONDS", RELAY_CAMPAIGN_TIMEOUT_SECONDS)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = RELAY_CAMPAIGN_TIMEOUT_SECONDS
    client = session or requests.Session()
    try:
        response = client.request(
            method,
            f"{base_url}{path}",
            json=payload,
            params=params,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RelayCampaignApiError(
            f"Relay campaign API {method} {path} timed out or was unavailable: {type(exc).__name__}"
        ) from exc
    if allow_not_found and response.status_code == 404:
        return None
    if not 200 <= response.status_code < 300:
        raise RelayCampaignApiError(
            f"Relay campaign API {method} {path} returned HTTP {response.status_code}"
        )
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise RelayCampaignApiError(
            f"Relay campaign API {method} {path} returned invalid JSON"
        ) from exc


def _member_batches(members):
    for offset in range(0, len(members), RELAY_CAMPAIGN_MEMBER_BATCH_SIZE):
        yield members[offset:offset + RELAY_CAMPAIGN_MEMBER_BATCH_SIZE]


def validate_native_campaign_recipient_counts(expected_count, recipient_count, skipped_count, *, stage):
    if (
        not isinstance(recipient_count, int)
        or isinstance(recipient_count, bool)
        or not isinstance(skipped_count, int)
        or isinstance(skipped_count, bool)
        or recipient_count != expected_count
        or skipped_count != 0
    ):
        raise RelayCampaignApiError(
            f"Relay campaign {stage} recipient count mismatch: expected {expected_count}, "
            f"Relay reported {recipient_count!r} recipients and {skipped_count!r} skipped."
        )


def _confirmed_delivery_ids_for_queued_campaign(key, scope, expected_delivery_ids, *, session):
    response = _campaign_api_request(
        "GET",
        f"/api/campaigns/{key}/recipients",
        params=scope,
        session=session,
    )
    recipients = response.get("recipients")
    if not isinstance(recipients, list):
        raise RelayCampaignApiError("Relay campaign recipients response omitted recipients")
    confirmed = {
        str(recipient.get("client_recipient_key"))
        for recipient in recipients
        if isinstance(recipient, dict)
        and recipient.get("client_recipient_key")
        and recipient.get("status") not in {"skipped", "failed", "cancelled"}
    }
    expected = {str(delivery_id) for delivery_id in expected_delivery_ids}
    matched = expected & confirmed
    if matched != expected:
        missing = len(expected - matched)
        raise RelayCampaignApiError(
            f"Relay queued campaign recovery could not confirm {missing} local recipients."
        )
    return [delivery_id for delivery_id in expected_delivery_ids if str(delivery_id) in matched]


def queue_native_campaign(campaign, deliveries, *, external_key=None, session=None):
    """Reconcile one immutable website snapshot and queue it as a Relay campaign.

    ``deliveries`` may be the full campaign snapshot or one monitored wave.
    Per-recipient verification links travel as private Relay list metadata and
    are copied into Relay's immutable campaign-recipient context at queue time.
    """
    from email_app.services.campaign_message import prepare_campaign_message
    from email_app.services.campaign_repermission import (
        REPERMISSION_COPY,
        REPERMISSION_EMAIL_TYPE,
        VERIFY_DEADLINE_CTA,
        VERIFY_DEADLINE_EMAIL_TYPE,
        confirmation_url_for_user,
        verify_deadline_url_for_user,
    )

    key = external_key or f"aishl-campaign-{campaign.pk}"
    list_key = key
    members = []
    included_delivery_ids = []
    source_deliveries = deliveries.select_related("user").order_by("recipient_user_pk")
    for delivery in source_deliveries:
        user = delivery.user
        if user is None or not user.is_active or user.unsubscribed:
            continue
        if user.bounce_state == user.BounceState.PERMANENT:
            continue
        monitored = campaign.audience_verification == "unverified_only"
        if monitored and user.email_verified:
            continue
        if not monitored and campaign.audience_verification == "verified_only" and not user.email_verified:
            continue
        email_type = "campaign"
        if monitored:
            email_type = (
                VERIFY_DEADLINE_EMAIL_TYPE
                if campaign.verify_deadline_days
                else REPERMISSION_EMAIL_TYPE
            )
        prepared = prepare_campaign_message(
            user,
            campaign.subject,
            campaign.body,
            email_type=email_type,
        )
        if prepared.skip_reason:
            continue
        if monitored:
            verification_url = (
                verify_deadline_url_for_user(user, campaign.verify_deadline_days)
                if campaign.verify_deadline_days
                else confirmation_url_for_user(user)
            )
            prepared = replace(prepared, verify_email_url=verification_url)
        members.append({
            "source_object_key": f"website-user-{delivery.recipient_user_pk}",
            "email": delivery.recipient_email,
            "metadata": {
                "relay_campaign_context": {
                    "verify_email_url": prepared.verify_email_url,
                    "client_recipient_key": str(delivery.pk),
                },
            },
            "subscribe_if_missing": True,
        })
        included_delivery_ids.append(delivery.pk)

    scope = {"audience": RELAY_CAMPAIGN_AUDIENCE, "client": RELAY_CAMPAIGN_CLIENT}
    existing = _campaign_api_request(
        "GET",
        f"/api/campaigns/{key}",
        params=scope,
        session=session,
        allow_not_found=True,
    )
    if existing and isinstance(existing.get("campaign"), dict):
        native = existing["campaign"]
        if native.get("status") in {"queued", "snapshotting", "sending", "sent"}:
            confirmed_ids = _confirmed_delivery_ids_for_queued_campaign(
                key, scope, included_delivery_ids, session=session,
            )
            return {
                "campaign": native,
                "queued": True,
                "already_queued": True,
                "recipient_count": native.get("recipient_count"),
                "skipped_count": native.get("skipped_count"),
                "included_delivery_ids": confirmed_ids,
            }
        if native.get("status") != "draft":
            raise RelayCampaignApiError(
                f"Relay campaign {key} is in terminal state {native.get('status')!r}; refusing send."
            )
    list_path = f"/api/recipient-lists/{list_key}/members/reconcile"
    list_defaults = {
        "type": "custom",
        "name": f"AISHL campaign {campaign.pk} snapshot",
    }
    _campaign_api_request(
        "POST",
        list_path,
        payload=scope | {
            "list": list_defaults,
            "members": [],
            "remove_absent": True,
        },
        session=session,
    )
    bulk_upsert_path = f"/api/recipient-lists/{list_key}/members/bulk-upsert"
    for member_batch in _member_batches(members):
        _campaign_api_request(
            "POST",
            bulk_upsert_path,
            payload=scope | {
                "list": list_defaults,
                "members": member_batch,
            },
            session=session,
        )
    campaign_payload = scope | {
        "subject": campaign.subject,
        "preview_text": "",
        "template_key": get_relay_template_key(),
        "template_version": DEFAULT_TEMPLATE_VERSION,
        "context": {
            "subject": campaign.subject,
            "body_markdown": campaign.body,
            "footer_note": (
                VERIFY_DEADLINE_CTA
                if campaign.verify_deadline_days
                else REPERMISSION_COPY
                if campaign.audience_verification == "unverified_only"
                else ""
            ),
            "verify_email_url": "",
            "unsubscribe_url": "",
        },
        "sender_id": get_relay_from_email(),
        "recipient_list_key": list_key,
        "allow_unverified": campaign.audience_verification != "verified_only",
        "metadata": {"website_campaign_id": campaign.pk},
    }
    _campaign_api_request(
        "PUT",
        f"/api/campaigns/{key}",
        payload=campaign_payload,
        session=session,
    )
    recount = _campaign_api_request(
        "POST",
        f"/api/campaigns/{key}/recount",
        payload=scope,
        session=session,
    )
    validate_native_campaign_recipient_counts(
        len(included_delivery_ids),
        recount.get("recipient_count"),
        recount.get("skipped_count"),
        stage="preflight",
    )
    queued = _campaign_api_request(
        "POST",
        f"/api/campaigns/{key}/queue",
        payload=scope,
        session=session,
    )
    validate_native_campaign_recipient_counts(
        len(included_delivery_ids),
        queued.get("recipient_count"),
        queued.get("skipped_count"),
        stage="queue",
    )
    queued["included_delivery_ids"] = included_delivery_ids
    return queued


def _configured_test_campaign_recipients():
    raw = get_config("CAMPAIGN_TEST_RECIPIENTS", "")
    if not isinstance(raw, str):
        raise RelayCampaignApiError("CAMPAIGN_TEST_RECIPIENTS must be a delimited address list.")
    recipients = []
    seen = set()
    for item in re.split(r"[,;\s]+", raw.strip()):
        if not item:
            continue
        address = item.strip().lower()
        try:
            validate_email(address)
        except ValidationError as exc:
            raise RelayCampaignApiError(
                "CAMPAIGN_TEST_RECIPIENTS contains an invalid address; no addresses were sent."
            ) from exc
        if address in seen:
            continue
        domain = address.rsplit("@", 1)[1]
        is_reserved_test_domain = any(
            domain == suffix[1:] or domain.endswith(suffix)
            for suffix in RELAY_TEST_CAMPAIGN_RESERVED_SUFFIXES
        )
        is_internal_domain = domain in RELAY_TEST_CAMPAIGN_ALLOWED_DOMAINS or any(
            domain.endswith(f".{allowed_domain}")
            for allowed_domain in RELAY_TEST_CAMPAIGN_ALLOWED_DOMAINS
        )
        if not is_internal_domain and not is_reserved_test_domain:
            raise RelayCampaignApiError(
                "CAMPAIGN_TEST_RECIPIENTS contains a domain outside the internal/test allowlist."
            )
        recipients.append(address)
        seen.add(address)
    if not RELAY_TEST_CAMPAIGN_MIN_RECIPIENTS <= len(recipients) <= RELAY_TEST_CAMPAIGN_MAX_RECIPIENTS:
        raise RelayCampaignApiError(
            "Native Relay test campaigns require 200–500 unique addresses from the internal/test allowlist."
        )
    return recipients


def preview_native_test_campaign(campaign, *, campaign_key=None):
    """Validate and summarize a configured bulk test without contacting Relay."""
    recipients = _configured_test_campaign_recipients()
    digest = hashlib.sha256("\n".join(sorted(recipients)).encode()).hexdigest()
    nonce = uuid.uuid4().hex
    signature_payload = f"{campaign.pk}:{len(recipients)}:{digest}:{nonce}"
    signature = hmac.new(
        str(settings.SECRET_KEY).encode(), signature_payload.encode(), hashlib.sha256,
    ).hexdigest()[:32]
    expected_key = f"aishl-test-{campaign.pk}-{nonce}-{signature}"
    if campaign_key is None:
        key = expected_key
    else:
        match = re.fullmatch(
            rf"aishl-test-{campaign.pk}-([0-9a-f]{{32}})-([0-9a-f]{{32}})",
            campaign_key,
        )
        if match is None:
            raise RelayCampaignApiError("Test campaign key is invalid or belongs to another campaign.")
        nonce = match.group(1)
        signature_payload = f"{campaign.pk}:{len(recipients)}:{digest}:{nonce}"
        expected_signature = hmac.new(
            str(settings.SECRET_KEY).encode(), signature_payload.encode(), hashlib.sha256,
        ).hexdigest()[:32]
        if not hmac.compare_digest(match.group(2), expected_signature):
            raise RelayCampaignApiError(
                "Configured test recipients changed after preflight; run dry-run again."
            )
        key = campaign_key
    domain_counts = {}
    for address in recipients:
        domain = address.rsplit("@", 1)[1]
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    return {
        "campaign_key": key,
        "recipient_count": len(recipients),
        "domain_counts": domain_counts,
        "recipient_digest": digest,
        "_recipients": recipients,
    }


def queue_native_test_campaign(campaign, *, campaign_key=None, session=None):
    """Queue a test-only Relay campaign from an exact configured address list.

    This deliberately does not call the website campaign audience resolver or
    create local CampaignDelivery rows. It is reachable only through the
    explicit management command and cannot fall back to an audience/list key.
    """
    plan = preview_native_test_campaign(campaign, campaign_key=campaign_key)
    recipients = plan["_recipients"]
    key = plan["campaign_key"]
    scope = {"audience": RELAY_CAMPAIGN_AUDIENCE, "client": RELAY_CAMPAIGN_CLIENT}
    list_defaults = {"type": "custom", "name": f"AISHL test campaign {campaign.pk}"}
    list_key = key
    try:
        existing = _campaign_api_request(
            "GET",
            f"/api/campaigns/{key}",
            params=scope,
            session=session,
            allow_not_found=True,
        )
        if existing:
            raise RelayCampaignApiError(
                "This preflight key has already been used; run dry-run to create a new key."
            )
        _campaign_api_request(
            "POST",
            f"/api/recipient-lists/{list_key}/members/reconcile",
            payload=scope | {"list": list_defaults, "members": [], "remove_absent": True},
            session=session,
        )
        bulk_path = f"/api/recipient-lists/{list_key}/members/bulk-upsert"
        members = []
        for index, address in enumerate(recipients, start=1):
            members.append({
                "source_object_key": f"aishl-test-recipient-{index:03d}",
                "email": address,
                "status": "active",
                "subscribe_if_missing": True,
                "metadata": {
                    "relay_campaign_context": {
                        "verify_email_url": "",
                        "client_recipient_key": f"test-{index:03d}",
                    },
                },
            })
        for member_batch in _member_batches(members):
            _campaign_api_request(
                "POST",
                bulk_path,
                payload=scope | {"list": list_defaults, "members": member_batch},
                session=session,
            )
        payload = scope | {
            "subject": f"[TEST] {campaign.subject}",
            "preview_text": "AISHL Relay tracking test",
            "template_key": get_relay_template_key(),
            "template_version": DEFAULT_TEMPLATE_VERSION,
            "context": {
                "subject": f"[TEST] {campaign.subject}",
                "body_markdown": campaign.body,
                "footer_note": "Test-only Relay campaign tracking check.",
                "verify_email_url": "",
                "unsubscribe_url": "",
            },
            "sender_id": get_relay_from_email(),
            "recipient_list_key": list_key,
            "allow_unverified": True,
            "metadata": {"website_campaign_id": campaign.pk, "is_test_campaign": True},
        }
        _campaign_api_request("PUT", f"/api/campaigns/{key}", payload=payload, session=session)
        recount = _campaign_api_request(
            "POST", f"/api/campaigns/{key}/recount", payload=scope, session=session,
        )
        validate_native_campaign_recipient_counts(
            len(recipients), recount.get("recipient_count"), recount.get("skipped_count"),
            stage="test preflight",
        )
        queued = _campaign_api_request(
            "POST", f"/api/campaigns/{key}/queue", payload=scope, session=session,
        )
        validate_native_campaign_recipient_counts(
            len(recipients), queued.get("recipient_count"), queued.get("skipped_count"),
            stage="test queue",
        )
        return {
            "campaign_key": key,
            "recipient_list_key": list_key,
            "recipient_count": len(recipients),
            "queued": True,
        }
    except Exception as exc:
        alert_relay_campaign_failure(campaign, "test campaign queue", exc)
        raise


def get_native_campaign_stats(external_key, *, session=None):
    """Read aggregate campaign metrics from Relay, failing on any API error."""
    from urllib.parse import urlencode

    query = urlencode({"audience": RELAY_CAMPAIGN_AUDIENCE, "client": RELAY_CAMPAIGN_CLIENT})
    response = _campaign_api_request(
        "GET",
        f"/api/campaigns/{external_key}?{query}",
        session=session,
    )
    campaign = response.get("campaign")
    if not isinstance(campaign, dict):
        raise RelayCampaignApiError("Relay campaign stats response did not contain a campaign")
    required = (
        "sent_count", "delivered_count", "unique_open_count", "open_count",
        "unique_click_count", "click_count", "unsubscribe_count", "bounce_count",
        "complaint_count",
    )
    if any(not isinstance(campaign.get(field), int) for field in required):
        raise RelayCampaignApiError("Relay campaign stats response omitted aggregate metrics")
    return campaign


def retry_native_campaign_delivery(external_key, delivery_id, *, session=None):
    """Retry one recipient through Relay's campaign recipient retry route."""
    from urllib.parse import urlencode

    scope = {"audience": RELAY_CAMPAIGN_AUDIENCE, "client": RELAY_CAMPAIGN_CLIENT}
    query = urlencode(scope)
    response = _campaign_api_request(
        "GET",
        f"/api/campaigns/{external_key}/recipients?{query}",
        session=session,
    )
    recipient = next(
        (
            row for row in response.get("recipients", [])
            if row.get("client_recipient_key") == str(delivery_id)
        ),
        None,
    )
    if recipient is None:
        raise RelayCampaignApiError(
            f"Relay campaign {external_key} has no recipient for local delivery {delivery_id}"
        )
    return _campaign_api_request(
        "POST",
        f"/api/campaigns/{external_key}/recipients/{recipient['id']}/retry",
        payload=scope,
        session=session,
    )


def refresh_campaign_relay_stats(campaign, *, session=None):
    """Read and persist aggregate metrics from all native Relay campaign waves."""
    from django.utils import timezone

    keys = list(campaign.relay_campaign_keys or [])
    if not keys and campaign.relay_campaign_key:
        keys = [campaign.relay_campaign_key]
    if not keys:
        return None
    fields = (
        "sent_count", "delivered_count", "unique_open_count", "open_count",
        "unique_click_count", "click_count", "unsubscribe_count", "bounce_count",
        "complaint_count",
    )
    totals = {field: 0 for field in fields}
    native_statuses = []
    for key in keys:
        native = get_native_campaign_stats(key, session=session)
        native_statuses.append(native.get("status", "unknown"))
        for field in fields:
            totals[field] += native[field]
    totals["status"] = "sent" if native_statuses and all(s == "sent" for s in native_statuses) else "sending"
    campaign.relay_campaign_stats = totals
    campaign.relay_campaign_stats_synced_at = timezone.now()
    campaign.sent_count = totals["sent_count"]
    campaign.save(update_fields=[
        "relay_campaign_stats", "relay_campaign_stats_synced_at", "sent_count",
    ])
    return totals


def refresh_campaign_relay_stats_loud(campaign, *, session=None):
    """Refresh stats and persist/alert any failure before propagating it."""
    try:
        result = refresh_campaign_relay_stats(campaign, session=session)
    except Exception as exc:
        campaign.relay_campaign_error = f"Relay stats refresh failed: {type(exc).__name__}: {exc}"[:2000]
        campaign.save(update_fields=["relay_campaign_error"])
        alert_relay_campaign_failure(campaign, "stats refresh", exc)
        raise
    if result is not None and campaign.relay_campaign_error:
        campaign.relay_campaign_error = ""
        campaign.save(update_fields=["relay_campaign_error"])
    return result


def alert_relay_campaign_failure(campaign, operation, error):
    """Alert the mail operators through the configured Relay email backend."""
    message = (
        f"AISHL campaign {campaign.pk} Relay {operation} failed.\n"
        f"Relay key: {campaign.relay_campaign_key or '(not assigned)'}\n"
        f"Error: {type(error).__name__}: {error}"
    )
    try:
        send_mail(
            subject=f"AISHL campaign {operation} failed: {campaign.pk}",
            message=message,
            from_email=None,
            recipient_list=["dev@dtcdev.click"],
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            "Could not send dev alert for AISHL campaign Relay %s failure campaign=%s",
            operation,
            campaign.pk,
        )
