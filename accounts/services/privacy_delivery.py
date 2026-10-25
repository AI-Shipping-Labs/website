"""Privacy-safe Relay delivery for account-deletion confirmations."""

import uuid
from datetime import timezone as datetime_timezone
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from accounts.models import PrivacyCompletionDelivery, PrivacyRequestLog
from accounts.services.privacy_recipient import decrypt_recipient
from email_app.models import EmailLog
from email_app.relay_transactional import (
    RelayTransactionalError,
    recipient_template_context,
    send_relay_transactional,
)
from email_app.services.relay_templates import RelayTemplateError, relay_template_subject
from integrations.config import get_config, validate_email_config_value

PRIVACY_REQUEST_EMAIL_KEY = "PRIVACY_REQUEST_EMAIL"
DEFAULT_PRIVACY_REQUEST_EMAIL = "team@aishippinglabs.com"
PRIVACY_SAFE_RECIPIENT = "[deleted-account]"
ERROR_RECIPIENT_UNAVAILABLE = "recipient_unavailable"
ERROR_PRIVACY_MAILBOX_INVALID = "privacy_mailbox_invalid"
ERROR_DELIVERY_FAILED = "delivery_failed"


def deliver_deletion_confirmation(request_id, *, retry_failed=False):
    """Attempt one fenced Relay completion send outside the deletion transaction."""
    candidate = _load_candidate(request_id, retry_failed)
    if isinstance(candidate, str):
        return candidate
    delivery, request_log, ciphertext = candidate
    prepared = _prepare_delivery(delivery, request_log, ciphertext)
    if prepared is None:
        return PrivacyCompletionDelivery.STATUS_FAILED
    recipient, privacy_email, subject, context = prepared
    claim_token = _claim_delivery(delivery.pk, retry_failed)
    if not isinstance(claim_token, uuid.UUID):
        return claim_token
    result = _send_claimed(
        delivery,
        recipient,
        privacy_email,
        context,
        claim_token,
    )
    if isinstance(result, str):
        return result
    return _complete_delivery(delivery.pk, claim_token, subject, result.message_id)


def _load_candidate(request_id, retry_failed):
    with transaction.atomic():
        delivery = (
            PrivacyCompletionDelivery.objects.select_for_update()
            .select_related("request")
            .filter(request_id=request_id)
            .first()
        )
        if delivery is None:
            return "missing"
        terminal = _terminal_status(delivery, retry_failed)
        if terminal:
            return terminal
        return delivery, delivery.request, delivery.recipient_ciphertext


def _terminal_status(delivery, retry_failed):
    if delivery.status in {
        PrivacyCompletionDelivery.STATUS_SENT,
        PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
    }:
        return delivery.status
    if delivery.status == PrivacyCompletionDelivery.STATUS_SENDING:
        delivery.status = PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN
        delivery.error_code = "transport_outcome_unknown"
        delivery.save(update_fields=["status", "error_code", "updated_at"])
        return delivery.status
    if delivery.status == PrivacyCompletionDelivery.STATUS_FAILED and not retry_failed:
        return delivery.status
    allowed = {
        PrivacyCompletionDelivery.STATUS_PENDING,
        PrivacyCompletionDelivery.STATUS_FAILED,
    }
    if delivery.status not in allowed:
        return delivery.status
    return ""


def _prepare_delivery(delivery, request_log, ciphertext):
    try:
        recipient = decrypt_recipient(ciphertext)
    except Exception:
        _mark_known_failure(delivery.pk, ERROR_RECIPIENT_UNAVAILABLE)
        return None
    privacy_email = validate_email_config_value(
        PRIVACY_REQUEST_EMAIL_KEY,
        get_config(PRIVACY_REQUEST_EMAIL_KEY, DEFAULT_PRIVACY_REQUEST_EMAIL),
    )
    if not privacy_email:
        _mark_known_failure(delivery.pk, ERROR_PRIVACY_MAILBOX_INVALID)
        return None
    context = _privacy_context(request_log, privacy_email)
    user = SimpleNamespace(email=recipient, first_name="", last_name="")
    try:
        subject = relay_template_subject(
            "account_deletion_completed",
            recipient_template_context(user, context),
        )
    except RelayTemplateError:
        _mark_known_failure(delivery.pk, ERROR_DELIVERY_FAILED)
        return None
    return recipient, privacy_email, subject, context


def _privacy_context(request_log, privacy_email):
    completed_audit = request_log.execution_audits.filter(
        request_type=PrivacyRequestLog.REQUEST_DELETE,
        status=PrivacyRequestLog.STATUS_COMPLETED,
    ).first()
    completed_at = (
        (completed_audit or request_log)
        .requested_at.astimezone(datetime_timezone.utc)
        .strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    return {
        "support_id": request_log.old_user_id,
        "completed_at_utc": completed_at,
        "privacy_email": privacy_email,
    }


def _claim_delivery(delivery_id, retry_failed):
    claim_token = uuid.uuid4()
    with transaction.atomic():
        delivery = PrivacyCompletionDelivery.objects.select_for_update().get(pk=delivery_id)
        allowed = PrivacyCompletionDelivery.STATUS_PENDING
        if retry_failed:
            allowed = PrivacyCompletionDelivery.STATUS_FAILED
        if delivery.status != allowed:
            return _resolve_claim_race(delivery)
        now = timezone.now()
        delivery.status = PrivacyCompletionDelivery.STATUS_SENDING
        delivery.claim_token = claim_token
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.transport_started_at = now
        delivery.error_code = ""
        delivery.save(
            update_fields=[
                "status",
                "claim_token",
                "attempt_count",
                "last_attempt_at",
                "transport_started_at",
                "error_code",
                "updated_at",
            ],
        )
    return claim_token


def _resolve_claim_race(delivery):
    if delivery.status == PrivacyCompletionDelivery.STATUS_SENDING:
        delivery.status = PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN
        delivery.error_code = "transport_outcome_unknown"
        delivery.save(update_fields=["status", "error_code", "updated_at"])
    return delivery.status


def _send_claimed(delivery, recipient, privacy_email, context, claim_token):
    try:
        return send_relay_transactional(
            recipient,
            "account_deletion_completed",
            context,
            idempotency_key=delivery.dedupe_key,
            safe_correlation=f"privacy_request={delivery.request_id}",
            cc=[privacy_email],
        )
    except RelayTransactionalError as exc:
        if exc.outcome_unknown:
            return _mark_outcome_unknown(delivery.pk, claim_token=claim_token)
        _mark_known_failure(
            delivery.pk,
            ERROR_DELIVERY_FAILED,
            claim_token=claim_token,
        )
        return PrivacyCompletionDelivery.STATUS_FAILED


def _complete_delivery(delivery_id, claim_token, subject, message_id):
    with transaction.atomic():
        delivery = PrivacyCompletionDelivery.objects.select_for_update().get(pk=delivery_id)
        if delivery.claim_token != claim_token or delivery.status not in {
            PrivacyCompletionDelivery.STATUS_SENDING,
            PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        }:
            return delivery.status
        email_log = _privacy_email_log(delivery, subject, message_id)
        delivery.status = PrivacyCompletionDelivery.STATUS_SENT
        delivery.email_log = email_log
        delivery.recipient_ciphertext = ""
        delivery.error_code = ""
        delivery.sent_at = timezone.now()
        delivery.save(
            update_fields=[
                "status",
                "email_log",
                "recipient_ciphertext",
                "error_code",
                "sent_at",
                "updated_at",
            ],
        )
    return PrivacyCompletionDelivery.STATUS_SENT


def _privacy_email_log(delivery, subject, message_id):
    email_log, _ = EmailLog.objects.get_or_create(
        dedupe_key=delivery.dedupe_key,
        defaults={
            "user": None,
            "recipient_email": PRIVACY_SAFE_RECIPIENT,
            "email_type": "account_deletion_completed",
            "subject": subject,
            "ses_message_id": message_id,
        },
    )
    return email_log


def _mark_outcome_unknown(delivery_id, *, claim_token):
    with transaction.atomic():
        delivery = PrivacyCompletionDelivery.objects.select_for_update().get(pk=delivery_id)
        if delivery.claim_token != claim_token:
            return delivery.status
        if delivery.status != PrivacyCompletionDelivery.STATUS_SENDING:
            return delivery.status
        delivery.status = PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN
        delivery.error_code = "transport_outcome_unknown"
        delivery.save(update_fields=["status", "error_code", "updated_at"])
        return delivery.status


def _mark_known_failure(delivery_id, error_code, *, claim_token=None):
    with transaction.atomic():
        delivery = PrivacyCompletionDelivery.objects.select_for_update().get(pk=delivery_id)
        if claim_token is not None and delivery.claim_token != claim_token:
            return
        if delivery.status in {
            PrivacyCompletionDelivery.STATUS_SENT,
            PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        }:
            return
        delivery.status = PrivacyCompletionDelivery.STATUS_FAILED
        delivery.error_code = error_code
        delivery.save(update_fields=["status", "error_code", "updated_at"])
