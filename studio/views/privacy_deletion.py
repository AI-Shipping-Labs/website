"""Studio review and execution views for accepted deletion requests."""

from datetime import timezone as datetime_timezone

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.models import PrivacyCompletionDelivery, PrivacyRequestLog
from accounts.services.privacy import request_context_from_request
from accounts.services.privacy_workflow import (
    deliver_deletion_confirmation,
    execute_deletion_request,
    get_deletion_request_for_review,
)
from studio.decorators import staff_required, superuser_required

BLOCKER_MESSAGES = {
    PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION: (
        "Active subscription cleanup is required before local deletion. "
        "Studio does not cancel Stripe or remove processor records."
    ),
    PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT: (
        "Protected staff and superuser accounts cannot be deleted through this workflow."
    ),
    "identity_changed": (
        "The login identity changed after this request. The member must submit a fresh "
        "deletion request for the current login email."
    ),
    "missing_user": (
        "No live account matches this request and no completed deletion audit exists. "
        "Review the audit evidence; this is not proof of deletion."
    ),
}

OUTCOME_MESSAGES = {
    "confirmation_required": "Enter the exact login email and acknowledge the irreversible action.",
    "confirmation_mismatch": "The confirmation email does not match the current login email.",
    "stale_submission": "This request changed in another tab. Review the current state before retrying.",
    "execution_failed": "Account deletion did not complete. No changes were committed; retry after reviewing system health.",
    "missing_user": BLOCKER_MESSAGES["missing_user"],
    "not_actionable": "This deletion request is not actionable.",
}


@staff_required
def privacy_deletion_review(request, request_id):
    state = get_deletion_request_for_review(request_id)
    if state is None:
        raise Http404
    request_log = state["request_log"]
    latest_attempt = request_log.execution_audits.order_by("-requested_at", "-pk").first()
    state.update(
        {
            "requested_at_utc": request_log.requested_at.astimezone(
                datetime_timezone.utc,
            ).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "completed_at_utc": (
                state["completed_audit"]
                .requested_at.astimezone(
                    datetime_timezone.utc,
                )
                .strftime("%Y-%m-%d %H:%M:%S UTC")
                if state["completed_audit"] is not None
                else ""
            ),
            "blocker_message": BLOCKER_MESSAGES.get(state["blocker"], ""),
            "latest_attempt": latest_attempt,
            "can_confirm": (
                request.user.is_superuser
                and request_log.status == PrivacyRequestLog.STATUS_REQUESTED
                and state["target"] is not None
            ),
            "can_retry_delivery": (
                request.user.is_superuser
                and state["delivery"] is not None
                and state["delivery"].status == PrivacyCompletionDelivery.STATUS_FAILED
            ),
        },
    )
    return render(request, "studio/privacy/deletion_request.html", state)


@superuser_required
@require_POST
def privacy_deletion_confirm(request, request_id):
    try:
        workflow_version = int(request.POST.get("workflow_version", ""))
    except (TypeError, ValueError):
        workflow_version = -1
    result = execute_deletion_request(
        request_id,
        request.user,
        confirm_email=request.POST.get("confirm_email", ""),
        acknowledged=request.POST.get("acknowledge") == "yes",
        workflow_version=workflow_version,
        request_context=request_context_from_request(request),
    )
    if result.deletion_success and result.delivery_status == PrivacyCompletionDelivery.STATUS_PENDING:
        deliver_deletion_confirmation(request_id)
    elif result.outcome in OUTCOME_MESSAGES:
        messages.error(request, OUTCOME_MESSAGES[result.outcome])
    return redirect("studio_privacy_deletion_review", request_id=request_id)


@superuser_required
@require_POST
def privacy_deletion_retry_confirmation(request, request_id):
    state = get_deletion_request_for_review(request_id)
    if state is None or state["completed_audit"] is None:
        raise Http404
    deliver_deletion_confirmation(request_id, retry_failed=True)
    return redirect("studio_privacy_deletion_review", request_id=request_id)
