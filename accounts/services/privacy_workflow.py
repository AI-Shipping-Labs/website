"""Locked Studio orchestration for accepted account-deletion requests."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timezone as datetime_timezone
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import PrivacyCompletionDelivery, PrivacyRequestLog
from accounts.services.privacy import (
    delete_account_for_privacy,
    normalized_privacy_email_hash,
    notify_privacy_staff,
    write_privacy_execution_audit,
)
from accounts.services.privacy_recipient import decrypt_recipient, encrypt_recipient
from email_app.models import EmailLog
from email_app.services.email_service import (
    EmailService,
    EmailServiceError,
    EmailTransportOutcomeUnknown,
)
from integrations.config import get_config, validate_email_config_value

logger = logging.getLogger(__name__)

PRIVACY_REQUEST_EMAIL_KEY = "PRIVACY_REQUEST_EMAIL"
DEFAULT_PRIVACY_REQUEST_EMAIL = "team@aishippinglabs.com"
PRIVACY_SAFE_RECIPIENT = "[deleted-account]"

BLOCKER_IDENTITY_CHANGED = "identity_changed"
ERROR_MISSING_USER = "missing_user"
ERROR_EXECUTION_FAILED = "execution_failed"
ERROR_RECIPIENT_UNAVAILABLE = "recipient_unavailable"
ERROR_PRIVACY_MAILBOX_INVALID = "privacy_mailbox_invalid"
ERROR_DELIVERY_FAILED = "delivery_failed"


@dataclass(frozen=True)
class DeletionExecutionResult:
    request_id: int
    outcome: str
    deletion_success: bool = False
    blocker_reason: str = ""
    delivery_status: str = ""


def _accepted_request_queryset():
    return PrivacyRequestLog.objects.filter(
        request_type=PrivacyRequestLog.REQUEST_DELETION_REQUEST,
        status__in=[
            PrivacyRequestLog.STATUS_REQUESTED,
            PrivacyRequestLog.STATUS_COMPLETED,
        ],
    )


def get_deletion_request_for_review(request_id):
    """Return an accepted request and its current safe review state."""
    request_log = _accepted_request_queryset().filter(pk=request_id).first()
    if request_log is None:
        return None

    completed_audit = request_log.execution_audits.filter(
        request_type=PrivacyRequestLog.REQUEST_DELETE,
        status=PrivacyRequestLog.STATUS_COMPLETED,
    ).first()
    target = get_user_model().objects.filter(pk=request_log.old_user_id).first()
    blocker = ""
    if request_log.status == PrivacyRequestLog.STATUS_REQUESTED:
        if target is None:
            blocker = ERROR_MISSING_USER
        elif normalized_privacy_email_hash(target.email) != request_log.normalized_email_hash:
            blocker = BLOCKER_IDENTITY_CHANGED
        elif target.is_staff or target.is_superuser:
            blocker = PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT
        elif target.subscription_id:
            blocker = PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION

    delivery = getattr(request_log, "completion_delivery", None)
    return {
        "request_log": request_log,
        "target": target,
        "completed_audit": completed_audit,
        "blocker": blocker,
        "delivery": delivery,
        "is_terminal": bool(completed_audit),
    }


def execute_deletion_request(
    request_id,
    operator,
    *,
    confirm_email,
    acknowledged,
    workflow_version,
    request_context=None,
):
    """Validate, lock, and execute one accepted request exactly once."""
    notify = None
    with transaction.atomic():
        request_log = _accepted_request_queryset().select_for_update().filter(pk=request_id).first()
        if request_log is None:
            return DeletionExecutionResult(request_id, "not_actionable")

        completed_audit = request_log.execution_audits.filter(
            request_type=PrivacyRequestLog.REQUEST_DELETE,
            status=PrivacyRequestLog.STATUS_COMPLETED,
        ).first()
        if completed_audit is not None:
            delivery = getattr(request_log, "completion_delivery", None)
            return DeletionExecutionResult(
                request_id,
                "completed",
                deletion_success=True,
                delivery_status=delivery.status if delivery else "",
            )
        if request_log.status != PrivacyRequestLog.STATUS_REQUESTED:
            return DeletionExecutionResult(request_id, "not_actionable")
        if workflow_version != request_log.workflow_version:
            return DeletionExecutionResult(request_id, "stale_submission")
        if not acknowledged or not confirm_email:
            return DeletionExecutionResult(request_id, "confirmation_required")

        target = get_user_model().objects.select_for_update().filter(pk=request_log.old_user_id).first()
        request_log.workflow_version += 1
        request_log.save(update_fields=["workflow_version"])

        if target is None:
            write_privacy_execution_audit(
                request_log,
                operator.pk,
                status=PrivacyRequestLog.STATUS_FAILED,
                error_code=ERROR_MISSING_USER,
                request_context=request_context,
            )
            return DeletionExecutionResult(request_id, ERROR_MISSING_USER)

        if normalized_privacy_email_hash(target.email) != request_log.normalized_email_hash:
            write_privacy_execution_audit(
                request_log,
                operator.pk,
                status=PrivacyRequestLog.STATUS_BLOCKED,
                blocker_reason=BLOCKER_IDENTITY_CHANGED,
                request_context=request_context,
            )
            return DeletionExecutionResult(
                request_id,
                "blocked",
                blocker_reason=BLOCKER_IDENTITY_CHANGED,
            )
        if confirm_email.strip().casefold() != target.email.strip().casefold():
            return DeletionExecutionResult(request_id, "confirmation_mismatch")

        target_email = target.email
        try:
            with transaction.atomic():
                service_result = delete_account_for_privacy(
                    target,
                    request_context,
                    notify_staff=False,
                )
                audit = PrivacyRequestLog.objects.get(pk=service_result.audit_log_id)
                audit.originating_request = request_log
                audit.operator_user_id = operator.pk
                audit.save(update_fields=["originating_request", "operator_user_id"])

                if service_result.success:
                    request_log.status = PrivacyRequestLog.STATUS_COMPLETED
                    request_log.blocker_reason = ""
                    request_log.save(update_fields=["status", "blocker_reason"])
                    PrivacyCompletionDelivery.objects.create(
                        request=request_log,
                        recipient_ciphertext=encrypt_recipient(target_email),
                        normalized_email_hash=request_log.normalized_email_hash,
                        email_domain=request_log.email_domain,
                        dedupe_key=f"account-deletion-completed:{request_log.pk}",
                    )
        except Exception:
            logger.error(
                "Privacy deletion execution failed request_id=%s",
                request_log.pk,
            )
            write_privacy_execution_audit(
                request_log,
                operator.pk,
                status=PrivacyRequestLog.STATUS_FAILED,
                error_code=ERROR_EXECUTION_FAILED,
                request_context=request_context,
            )
            return DeletionExecutionResult(request_id, ERROR_EXECUTION_FAILED)

        if not service_result.success:
            notify = (
                (
                    "blocked_active_subscription",
                    target_email,
                    target.pk,
                    service_result.row_count_summary or {},
                )
                if service_result.blocker_reason == PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION
                else None
            )
            outcome = DeletionExecutionResult(
                request_id,
                "blocked",
                blocker_reason=service_result.blocker_reason,
            )
        else:
            notify = (
                "completed_delete",
                target_email,
                request_log.old_user_id,
                service_result.row_count_summary or {},
            )
            outcome = DeletionExecutionResult(
                request_id,
                "completed",
                deletion_success=True,
                delivery_status=PrivacyCompletionDelivery.STATUS_PENDING,
            )

    if notify is not None:
        event, email, old_user_id, summary = notify
        notify_privacy_staff(
            event=event,
            email=email,
            old_user_id=old_user_id,
            row_count_summary=summary,
        )
    return outcome


def deliver_deletion_confirmation(request_id, *, retry_failed=False):
    """Attempt one fenced completion send outside the deletion transaction."""
    with transaction.atomic():
        delivery = (
            PrivacyCompletionDelivery.objects.select_for_update()
            .select_related("request")
            .filter(request_id=request_id)
            .first()
        )
        if delivery is None:
            return "missing"
        if delivery.status == PrivacyCompletionDelivery.STATUS_SENT:
            return delivery.status
        if delivery.status == PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN:
            return delivery.status
        if delivery.status == PrivacyCompletionDelivery.STATUS_SENDING:
            delivery.status = PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN
            delivery.error_code = "transport_outcome_unknown"
            delivery.save(update_fields=["status", "error_code", "updated_at"])
            return delivery.status
        if delivery.status == PrivacyCompletionDelivery.STATUS_FAILED and not retry_failed:
            return delivery.status
        if delivery.status not in {
            PrivacyCompletionDelivery.STATUS_PENDING,
            PrivacyCompletionDelivery.STATUS_FAILED,
        }:
            return delivery.status
        ciphertext = delivery.recipient_ciphertext
        request_log = delivery.request

    try:
        recipient = decrypt_recipient(ciphertext)
    except Exception:
        _mark_known_delivery_failure(delivery.pk, ERROR_RECIPIENT_UNAVAILABLE)
        return PrivacyCompletionDelivery.STATUS_FAILED

    privacy_email = validate_email_config_value(
        PRIVACY_REQUEST_EMAIL_KEY,
        get_config(PRIVACY_REQUEST_EMAIL_KEY, DEFAULT_PRIVACY_REQUEST_EMAIL),
    )
    if not privacy_email:
        _mark_known_delivery_failure(delivery.pk, ERROR_PRIVACY_MAILBOX_INVALID)
        return PrivacyCompletionDelivery.STATUS_FAILED

    completed_audit = request_log.execution_audits.filter(
        request_type=PrivacyRequestLog.REQUEST_DELETE,
        status=PrivacyRequestLog.STATUS_COMPLETED,
    ).first()
    completed_at = (
        (completed_audit or request_log)
        .requested_at.astimezone(
            datetime_timezone.utc,
        )
        .strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    recipient_user = SimpleNamespace(
        email=recipient,
        pk=None,
        email_verified=True,
        unsubscribed=True,
        first_name="",
        last_name="",
    )
    try:
        service = EmailService()
        prepared = service.prepare_template(
            recipient_user,
            "account_deletion_completed",
            {
                "support_id": request_log.old_user_id,
                "completed_at_utc": completed_at,
                "privacy_email": privacy_email,
            },
            recipient_email=recipient,
            cc=[privacy_email],
            redact_transport_recipient=True,
        )
    except EmailServiceError:
        _mark_known_delivery_failure(delivery.pk, ERROR_DELIVERY_FAILED)
        return PrivacyCompletionDelivery.STATUS_FAILED

    claim_token = uuid.uuid4()
    with transaction.atomic():
        claimed = PrivacyCompletionDelivery.objects.select_for_update().get(pk=delivery.pk)
        allowed_status = (
            PrivacyCompletionDelivery.STATUS_FAILED if retry_failed else PrivacyCompletionDelivery.STATUS_PENDING
        )
        if claimed.status != allowed_status:
            if claimed.status == PrivacyCompletionDelivery.STATUS_SENDING:
                claimed.status = PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN
                claimed.error_code = "transport_outcome_unknown"
                claimed.save(update_fields=["status", "error_code", "updated_at"])
            return claimed.status
        now = timezone.now()
        claimed.status = PrivacyCompletionDelivery.STATUS_SENDING
        claimed.claim_token = claim_token
        claimed.attempt_count += 1
        claimed.last_attempt_at = now
        claimed.transport_started_at = now
        claimed.error_code = ""
        claimed.save(
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

    try:
        ses_message_id = service.send_prepared(prepared)
    except EmailTransportOutcomeUnknown:
        return _mark_delivery_outcome_unknown(
            delivery.pk,
            claim_token=claim_token,
        )
    except EmailServiceError:
        _mark_known_delivery_failure(
            delivery.pk,
            ERROR_DELIVERY_FAILED,
            claim_token=claim_token,
        )
        return PrivacyCompletionDelivery.STATUS_FAILED

    with transaction.atomic():
        sent_delivery = PrivacyCompletionDelivery.objects.select_for_update().get(
            pk=delivery.pk,
        )
        if sent_delivery.claim_token != claim_token or sent_delivery.status not in {
            PrivacyCompletionDelivery.STATUS_SENDING,
            PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        }:
            return sent_delivery.status
        email_log, _ = EmailLog.objects.get_or_create(
            dedupe_key=sent_delivery.dedupe_key,
            defaults={
                "user": None,
                "recipient_email": PRIVACY_SAFE_RECIPIENT,
                "email_type": "account_deletion_completed",
                "subject": prepared.subject,
                "ses_message_id": ses_message_id,
            },
        )
        sent_delivery.status = PrivacyCompletionDelivery.STATUS_SENT
        sent_delivery.email_log = email_log
        sent_delivery.recipient_ciphertext = ""
        sent_delivery.error_code = ""
        sent_delivery.sent_at = timezone.now()
        sent_delivery.save(
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


def _mark_delivery_outcome_unknown(delivery_id, *, claim_token):
    with transaction.atomic():
        delivery = PrivacyCompletionDelivery.objects.select_for_update().get(
            pk=delivery_id,
        )
        if delivery.claim_token != claim_token:
            return delivery.status
        if delivery.status != PrivacyCompletionDelivery.STATUS_SENDING:
            return delivery.status
        delivery.status = PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN
        delivery.error_code = "transport_outcome_unknown"
        delivery.save(update_fields=["status", "error_code", "updated_at"])
        return delivery.status


def _mark_known_delivery_failure(delivery_id, error_code, *, claim_token=None):
    with transaction.atomic():
        delivery = PrivacyCompletionDelivery.objects.select_for_update().get(
            pk=delivery_id,
        )
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
