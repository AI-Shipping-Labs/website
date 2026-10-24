"""Locked Studio orchestration for accepted account-deletion requests."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from accounts.models import PrivacyCompletionDelivery, PrivacyRequestLog
from accounts.services.privacy import (
    delete_account_for_privacy,
    normalized_privacy_email_hash,
    notify_privacy_staff,
    write_privacy_execution_audit,
)
from accounts.services.privacy_delivery import deliver_deletion_confirmation as _deliver_deletion_confirmation
from accounts.services.privacy_recipient import encrypt_recipient

logger = logging.getLogger(__name__)

BLOCKER_IDENTITY_CHANGED = "identity_changed"
ERROR_MISSING_USER = "missing_user"
ERROR_EXECUTION_FAILED = "execution_failed"


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
    target = (
        get_user_model()
        .objects.select_related("membership")
        .filter(pk=request_log.old_user_id)
        .first()
    )
    blocker = ""
    if request_log.status == PrivacyRequestLog.STATUS_REQUESTED:
        if target is None:
            blocker = ERROR_MISSING_USER
        elif normalized_privacy_email_hash(target.email) != request_log.normalized_email_hash:
            blocker = BLOCKER_IDENTITY_CHANGED
        elif target.is_staff or target.is_superuser:
            blocker = PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT
        elif target.membership.subscription_id:
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
    """Delegate to the isolated privacy-safe Relay state machine."""
    return _deliver_deletion_confirmation(request_id, retry_failed=retry_failed)
