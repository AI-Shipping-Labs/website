"""Direct AISL adapter for Relay transactional sends with extra MIME parts."""

import logging
from dataclasses import dataclass

import requests
from community_base.kernel.context import is_safe_external_context_id
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.utils.display import GREETING_FALLBACK, greeting_name
from email_app.models import RelayTransactionalDelivery
from email_app.relay_versions import template_version_for
from integrations.config import get_config, is_enabled
from notifications.models import Notification
from notifications.services.staff_slack import (
    message_payload,
    post_staff_slack_message,
    staff_alert_channel_id,
)

logger = logging.getLogger(__name__)

RELAY_TIMEOUT_SECONDS = 15
ACCEPTED_STATUSES = frozenset(
    {"accepted", "queued", "sent", "provider_accepted", "delivered"},
)


class RelayTransactionalError(RuntimeError):
    """Safe Relay error whose text never contains recipient or response data."""

    def __init__(self, code, *, outcome_unknown=False):
        self.code = code
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


@dataclass(frozen=True)
class RelayTransactionalResult:
    message_id: str
    status: str
    idempotent_replay: bool


def recipient_template_context(user, context):
    """Add the legacy greeting scalar required by Relay-native templates."""
    resolved = dict(context)
    resolved["user_name"] = greeting_name(user) or GREETING_FALLBACK
    return resolved


def calendar_alternative(content, *, method, name="event.ics"):
    """Build Relay's calendar alternative while preserving iTIP semantics."""
    normalized_method = str(method).upper()
    if normalized_method not in {"REQUEST", "CANCEL"}:
        raise ValueError("calendar method must be REQUEST or CANCEL")
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return {"content": content, "method": normalized_method, "name": name}


def send_relay_transactional(
    recipient,
    template_key,
    context,
    *,
    idempotency_key,
    safe_correlation,
    cc=None,
    calendar=None,
):
    """Send one named template through Relay with a stable logical key."""
    existing = _claim_delivery(template_key, idempotency_key, safe_correlation)
    if existing is not None:
        return existing
    payload = _build_payload(
        recipient,
        template_key,
        context,
        idempotency_key,
        cc=cc,
        calendar=calendar,
    )
    response = _post_relay(payload, template_key, idempotency_key, safe_correlation)
    return _handle_response(response, template_key, idempotency_key, safe_correlation)


def _post_relay(payload, template_key, idempotency_key, safe_correlation):
    try:
        return requests.post(
            _relay_send_url(),
            json=payload,
            headers=_relay_headers(),
            timeout=RELAY_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        _fail_delivery(template_key, idempotency_key, safe_correlation, "relay_ack_unknown", True)
        raise RelayTransactionalError("relay_ack_unknown", outcome_unknown=True) from exc
    except requests.RequestException as exc:
        _fail_delivery(template_key, idempotency_key, safe_correlation, "relay_unavailable", False)
        raise RelayTransactionalError("relay_unavailable") from exc
    except RelayTransactionalError as exc:
        _fail_delivery(template_key, idempotency_key, safe_correlation, exc.code, False)
        raise


def _claim_delivery(template_key, idempotency_key, safe_correlation):
    with transaction.atomic():
        delivery, _ = RelayTransactionalDelivery.objects.select_for_update().get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                "template_key": template_key,
                "safe_correlation": safe_correlation,
                "status": RelayTransactionalDelivery.STATUS_SENDING,
            },
        )
        if delivery.template_key != template_key:
            raise RelayTransactionalError("idempotency_key_conflict")
        if delivery.status == RelayTransactionalDelivery.STATUS_ACCEPTED:
            return RelayTransactionalResult(delivery.message_id, "accepted", True)
        delivery.status = RelayTransactionalDelivery.STATUS_SENDING
        delivery.attempt_count += 1
        delivery.last_attempt_at = timezone.now()
        delivery.error_code = ""
        delivery.save(
            update_fields=["status", "attempt_count", "last_attempt_at", "error_code", "updated_at"],
        )
    return None


def _build_payload(recipient, template_key, context, idempotency_key, *, cc, calendar):
    payload = {
        "email": recipient,
        "template_key": template_key,
        "template_version": template_version_for(template_key),
        "idempotency_key": idempotency_key,
        "context": context,
    }
    if cc:
        payload["cc"] = list(cc)
    if calendar is not None:
        payload["calendar_alternative"] = calendar
    return payload


def _relay_send_url():
    base_url = str(get_config("RELAY_BASE_URL", "") or "").rstrip("/")
    if not base_url:
        raise RelayTransactionalError("relay_not_configured")
    return f"{base_url}/api/transactional/send"


def _relay_headers():
    api_key = str(get_config("RELAY_API_KEY", "") or "")
    if not api_key:
        raise RelayTransactionalError("relay_not_configured")
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _handle_response(response, template_key, idempotency_key, safe_correlation):
    try:
        document = response.json()
    except (TypeError, ValueError) as exc:
        return _reject_response(
            template_key, idempotency_key, safe_correlation, "relay_malformed_response", exc,
        )
    if response.status_code not in {200, 202}:
        return _reject_response(
            template_key, idempotency_key, safe_correlation, "relay_http_error",
        )
    result, error_code = _parse_result(document, template_key, idempotency_key)
    if result is None:
        return _reject_response(
            template_key, idempotency_key, safe_correlation, error_code,
        )
    _record_accepted(idempotency_key, result.message_id)
    return result


def _parse_result(document, template_key, idempotency_key):
    message = document.get("message") if isinstance(document, dict) else None
    status = message.get("status") if isinstance(message, dict) else None
    message_id = message.get("id") if isinstance(message, dict) else None
    replay = document.get("idempotent_replay") if isinstance(document, dict) else None
    valid_identity = (
        isinstance(message, dict)
        and message.get("template_key") == template_key
        and message.get("idempotency_key") == idempotency_key
    )
    valid_message_id = (
        isinstance(message_id, (str, int))
        and not isinstance(message_id, bool)
        and is_safe_external_context_id(str(message_id))
    )
    if (
        status not in ACCEPTED_STATUSES
        or not valid_message_id
        or not isinstance(replay, bool)
        or not valid_identity
    ):
        code = "relay_rejected"
        if not isinstance(status, str):
            code = "relay_malformed_response"
        return None, code
    return RelayTransactionalResult(str(message_id), status, replay), ""


def _reject_response(template_key, idempotency_key, safe_correlation, code, cause=None):
    _fail_delivery(template_key, idempotency_key, safe_correlation, code, False)
    error = RelayTransactionalError(code)
    if cause is not None:
        raise error from cause
    raise error


def _record_accepted(idempotency_key, message_id):
    with transaction.atomic():
        delivery = RelayTransactionalDelivery.objects.select_for_update().get(
            idempotency_key=idempotency_key,
        )
        delivery.status = RelayTransactionalDelivery.STATUS_ACCEPTED
        delivery.message_id = message_id
        delivery.error_code = ""
        delivery.save(
            update_fields=["status", "message_id", "error_code", "updated_at"],
        )


def _fail_delivery(template_key, idempotency_key, safe_correlation, code, unknown):
    status = RelayTransactionalDelivery.STATUS_FAILED
    if unknown:
        status = RelayTransactionalDelivery.STATUS_OUTCOME_UNKNOWN
    recorded, should_alert = _record_failure(idempotency_key, status, code)
    if not recorded:
        return
    logger.error(
        "Relay transactional send failed template=%s key=%s correlation=%s code=%s",
        template_key,
        idempotency_key,
        safe_correlation,
        code,
    )
    if should_alert:
        _alert_operators(template_key, idempotency_key, safe_correlation, code)


def _record_failure(idempotency_key, status, code):
    with transaction.atomic():
        delivery = RelayTransactionalDelivery.objects.select_for_update().get(
            idempotency_key=idempotency_key,
        )
        if delivery.status == RelayTransactionalDelivery.STATUS_ACCEPTED:
            return False, False
        should_alert = delivery.alerted_at is None
        delivery.status = status
        delivery.error_code = code
        if should_alert:
            delivery.alerted_at = timezone.now()
        delivery.save(update_fields=["status", "error_code", "alerted_at", "updated_at"])
    return True, should_alert


def _alert_operators(template_key, idempotency_key, safe_correlation, code):
    _create_staff_notifications(template_key, idempotency_key, safe_correlation, code)
    _post_staff_slack_alert(template_key, idempotency_key, safe_correlation, code)


def _create_staff_notifications(template_key, idempotency_key, safe_correlation, code):
    staff_ids = User.objects.filter(is_active=True, is_staff=True).order_by("pk").values_list("pk", flat=True)[:50]
    body = f"{code}; key={idempotency_key}; correlation={safe_correlation}"
    rows = []
    for staff_id in staff_ids:
        rows.append(
            Notification(
                user_id=staff_id,
                title=f"Relay email failed: {template_key}",
                body=body,
                url="/studio/",
                notification_type="announcement",
            ),
        )
    Notification.objects.bulk_create(rows)


def _post_staff_slack_alert(template_key, idempotency_key, safe_correlation, code):
    if not is_enabled("SLACK_ENABLED"):
        return
    channel_id = staff_alert_channel_id()
    if not channel_id:
        return
    text = (
        f"Relay email failed: template={template_key} key={idempotency_key} "
        f"correlation={safe_correlation} code={code}"
    )
    post_staff_slack_message(channel_id, message_payload(text, []))
