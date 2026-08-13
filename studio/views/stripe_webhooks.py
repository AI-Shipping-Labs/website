"""Studio Stripe webhook cancellation diagnostics (issue #1314).

Staff-only page that proves whether the production Stripe cancellation
callbacks are configured, received, processed, retried, or rejected. It shows
the latest read-only endpoint check, independent health indicators, a filtered
and paginated delivery-attempt history, and a read-only event inspector.

Studio can inspect but NEVER executes replays; the confirmed replay write is
API-only.
"""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from integrations.config import get_config
from payments.models import StripeWebhookDeliveryAttempt, StripeWebhookEndpointCheck
from payments.services.stripe_endpoint_verifier import (
    CANCELLATION_EVENTS,
    REQUIRED_EVENTS,
    get_expected_webhook_url,
    signing_secret_evidence,
    verify_stripe_endpoint,
)
from payments.services.webhook_dispatch import CANCELLATION_EVENT_TYPES
from payments.services.webhook_replay import ReplayError, preview_event
from studio.decorators import staff_required
from studio.utils import studio_pagination_context

CANCELLATION_FILTER = "cancellation"


def _stripe_dashboard_url(account_id, resource, object_id):
    if not account_id or not object_id:
        return ""
    return f"https://dashboard.stripe.com/{account_id}/{resource}/{object_id}"


def _attempt_rows(attempts, account_id):
    rows = []
    for attempt in attempts:
        rows.append({
            "attempt": attempt,
            "customer_url": _stripe_dashboard_url(
                account_id, "customers", attempt.stripe_customer_id,
            ),
            "subscription_url": _stripe_dashboard_url(
                account_id, "subscriptions", attempt.stripe_subscription_id,
            ),
            "charge_url": _stripe_dashboard_url(
                account_id, "payments", attempt.stripe_charge_id,
            ),
            "invoice_url": _stripe_dashboard_url(
                account_id, "invoices", attempt.stripe_invoice_id,
            ),
            "dispute_url": _stripe_dashboard_url(
                account_id, "disputes", attempt.stripe_dispute_id,
            ),
            "event_url": _stripe_dashboard_url(
                account_id, "events", attempt.stripe_event_id,
            ),
        })
    return rows


@staff_required
def stripe_webhooks_dashboard(request):
    latest_check = (
        StripeWebhookEndpointCheck.objects.order_by("-checked_at").first()
    )
    secret_evidence = signing_secret_evidence()
    account_id = get_config("STRIPE_DASHBOARD_ACCOUNT_ID", "")

    base_qs = StripeWebhookDeliveryAttempt.objects.all()
    has_any_attempt = base_qs.exists()

    outcome = (request.GET.get("outcome") or "").strip()
    event_id = (request.GET.get("event_id") or "").strip()
    customer_id = (request.GET.get("customer_id") or "").strip()
    subscription_id = (request.GET.get("subscription_id") or "").strip()
    scope = (request.GET.get("scope") or "all").strip()

    qs = base_qs
    if scope == CANCELLATION_FILTER:
        qs = qs.filter(event_type__in=CANCELLATION_EVENT_TYPES)
    if outcome:
        qs = qs.filter(outcome=outcome)
    if event_id:
        qs = qs.filter(stripe_event_id=event_id)
    if customer_id:
        qs = qs.filter(stripe_customer_id=customer_id)
    if subscription_id:
        qs = qs.filter(stripe_subscription_id=subscription_id)

    filters_active = any([
        outcome, event_id, customer_id, subscription_id,
        scope == CANCELLATION_FILTER,
    ])
    pager = studio_pagination_context(request, qs)
    rows = _attempt_rows(pager["page"].object_list, account_id)

    # Read-only event inspection (Studio never executes a replay).
    inspect_event_id = (request.GET.get("inspect_event_id") or "").strip()
    inspect_result = None
    inspect_error = None
    if inspect_event_id:
        try:
            inspect_result = preview_event(inspect_event_id)
        except ReplayError as exc:
            inspect_error = {"code": exc.code, "message": exc.message}

    context = {
        "latest_check": latest_check,
        "expected_url": get_expected_webhook_url(),
        "required_events": REQUIRED_EVENTS,
        "cancellation_events": CANCELLATION_EVENTS,
        "secret_evidence": secret_evidence,
        "rows": rows,
        "has_any_attempt": has_any_attempt,
        "filters_active": filters_active,
        "active_outcome": outcome,
        "active_scope": scope,
        "search_event_id": event_id,
        "search_customer_id": customer_id,
        "search_subscription_id": subscription_id,
        "outcome_choices": StripeWebhookDeliveryAttempt.OUTCOME_CHOICES,
        "cancellation_filter": CANCELLATION_FILTER,
        "clear_url": request.path,
        "inspect_event_id": inspect_event_id,
        "inspect_result": inspect_result,
        "inspect_error": inspect_error,
        "account_id": account_id,
        **pager,
    }
    return render(request, "studio/payments/stripe_webhooks.html", context)


@staff_required
@require_POST
def stripe_webhooks_verify(request):
    check = verify_stripe_endpoint(
        source=StripeWebhookEndpointCheck.SOURCE_STUDIO,
        checked_by=request.user,
    )
    if check.status == StripeWebhookEndpointCheck.STATUS_PASS:
        messages.success(request, "Stripe webhook endpoint configuration passes.")
    elif check.status == StripeWebhookEndpointCheck.STATUS_WARNING:
        messages.warning(
            request,
            f"Stripe endpoint check has a warning: {check.error_message}",
        )
    else:
        messages.error(
            request,
            f"Stripe endpoint check failed: {check.error_message}",
        )
    return redirect("studio_stripe_webhooks")
