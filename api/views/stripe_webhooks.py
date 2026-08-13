"""Staff-token API for Stripe webhook cancellation observability (issue #1314).

Four read/replay operations, all staff-token-only (``token_required`` rejects
non-staff tokens by construction) and authenticated BEFORE any Stripe call or
operational-record read:

- ``POST /api/payments/stripe-webhooks/verify``    — run + persist an endpoint check
- ``GET  /api/payments/stripe-webhooks/status``    — latest check + aggregates (no Stripe call)
- ``GET  /api/payments/stripe-webhooks/deliveries``— paginated/filterable attempts
- ``POST /api/payments/stripe-webhooks/replay``    — guarded dry-run/confirmed replay

No endpoint returns raw Stripe payloads, API keys, or signing secrets.
"""

from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from payments.models import StripeWebhookDeliveryAttempt, StripeWebhookEndpointCheck
from payments.services.refund_dispute_review import (
    DISPUTE_EVENT_TYPES,
    REFUND_EVENT_TYPES,
)
from payments.services.stripe_endpoint_verifier import (
    REQUIRED_EVENTS,
    get_expected_webhook_url,
    signing_secret_evidence,
    verify_stripe_endpoint,
)
from payments.services.webhook_dispatch import CANCELLATION_EVENT_TYPES
from payments.services.webhook_replay import ReplayError, replay_event

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


def _dt(value):
    return value.isoformat() if value else None


def _serialize_check(check):
    if check is None:
        return None
    return {
        "id": str(check.id),
        "source": check.source,
        "checked_at": _dt(check.checked_at),
        "status": check.status,
        "key_mode": check.key_mode,
        "expected_url": check.expected_url,
        "matching_endpoint_id": check.matching_endpoint_id,
        "endpoint_status": check.endpoint_status,
        "enabled_events": check.enabled_events,
        "missing_events": check.missing_events,
        "unexpected_events": check.unexpected_events,
        "api_version": check.api_version,
        "error_code": check.error_code,
        "error_message": check.error_message,
    }


def _serialize_attempt(attempt):
    return {
        "id": str(attempt.id),
        "source": attempt.source,
        "stripe_event_id": attempt.stripe_event_id,
        "event_type": attempt.event_type,
        "stripe_object_id": attempt.stripe_object_id,
        "stripe_customer_id": attempt.stripe_customer_id,
        "stripe_subscription_id": attempt.stripe_subscription_id,
        "stripe_charge_id": attempt.stripe_charge_id,
        "stripe_invoice_id": attempt.stripe_invoice_id,
        "stripe_dispute_id": attempt.stripe_dispute_id,
        "livemode": attempt.livemode,
        "attempt_number": attempt.attempt_number,
        "outcome": attempt.outcome,
        "http_status": attempt.http_status,
        "received_at": _dt(attempt.received_at),
        "finished_at": _dt(attempt.finished_at),
        "error_code": attempt.error_code,
        "error_message": attempt.error_message,
    }


def _signing_secret_payload():
    evidence = signing_secret_evidence()
    return {
        "configured": evidence["configured"],
        "verified_by_delivery": evidence["verified_by_delivery"],
        "latest_verified_at": _dt(evidence["latest_verified_at"]),
    }


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Payments",
    summary="Verify the Stripe webhook endpoint configuration",
    methods={
        "POST": {
            "summary": "Run and persist a read-only Stripe endpoint check",
            "description": (
                "Inspects Stripe webhook endpoints in the same mode as the "
                "configured key and persists the result. Never modifies Stripe "
                "configuration and never returns keys or signing secrets."
            ),
            "responses": {200: {"description": "Persisted endpoint check."}},
        }
    },
)
def stripe_webhooks_verify(request):
    check = verify_stripe_endpoint(
        source=StripeWebhookEndpointCheck.SOURCE_API,
        checked_by=request.user,
    )
    return JsonResponse({
        "check": _serialize_check(check),
        "status": check.status,
        "error_code": check.error_code,
        "missing_events": check.missing_events,
        "unexpected_events": check.unexpected_events,
        "required_events": REQUIRED_EVENTS,
        "expected_url": check.expected_url,
        "signing_secret": _signing_secret_payload(),
    })


@token_required
@require_methods("GET")
@openapi_spec(
    tag="Payments",
    summary="Stripe webhook status and review-attempt aggregates",
    methods={
        "GET": {
            "summary": "Latest persisted check + aggregates (no Stripe call)",
            "description": (
                "Returns the canonical `required_events` verifier list, "
                "currently eleven entries, with the latest persisted check "
                "and delivery aggregates. This read makes no Stripe call."
            ),
            "responses": {200: {"description": "Status snapshot."}},
        }
    },
)
def stripe_webhooks_status(request):
    latest = StripeWebhookEndpointCheck.objects.order_by("-checked_at").first()
    counts = {
        row["outcome"]: row["n"]
        for row in (
            StripeWebhookDeliveryAttempt.objects
            .filter(event_type__in=CANCELLATION_EVENT_TYPES)
            .values("outcome")
            .annotate(n=Count("id"))
        )
    }
    review_attempts = StripeWebhookDeliveryAttempt.objects.filter(
        outcome=StripeWebhookDeliveryAttempt.OUTCOME_REVIEW_REQUIRED,
    )
    return JsonResponse({
        "latest_check": _serialize_check(latest),
        "expected_url": get_expected_webhook_url(),
        "required_events": REQUIRED_EVENTS,
        "signing_secret": _signing_secret_payload(),
        "cancellation_attempt_counts": counts,
        "refund_review_attempt_count": review_attempts.filter(
            event_type__in=REFUND_EVENT_TYPES,
        ).count(),
        "dispute_review_attempt_count": review_attempts.filter(
            event_type__in=DISPUTE_EVENT_TYPES,
        ).count(),
    })


@token_required
@require_methods("GET")
@openapi_spec(
    tag="Payments",
    summary="List Stripe webhook delivery attempts",
    methods={
        "GET": {
            "summary": "Paginated, filterable delivery attempts",
            "query": {
                "event_type": {"type": "string", "required": False},
                "outcome": {"type": "string", "required": False},
                "stripe_event_id": {"type": "string", "required": False},
                "customer_id": {"type": "string", "required": False},
                "subscription_id": {"type": "string", "required": False},
                "charge_id": {"type": "string", "required": False},
                "invoice_id": {"type": "string", "required": False},
                "dispute_id": {"type": "string", "required": False},
                "cancellation": {"type": "boolean", "required": False},
                "page": {"type": "integer", "required": False},
                "page_size": {"type": "integer", "required": False},
            },
            "responses": {200: {"description": "Delivery attempts page."}},
        }
    },
)
def stripe_webhooks_deliveries(request):
    qs = StripeWebhookDeliveryAttempt.objects.all()

    event_type = (request.GET.get("event_type") or "").strip()
    if event_type:
        qs = qs.filter(event_type=event_type)

    if (request.GET.get("cancellation") or "").strip().lower() in {"1", "true", "yes"}:
        qs = qs.filter(event_type__in=CANCELLATION_EVENT_TYPES)

    outcome = (request.GET.get("outcome") or "").strip()
    if outcome:
        valid = {c[0] for c in StripeWebhookDeliveryAttempt.OUTCOME_CHOICES}
        if outcome not in valid:
            return error_response(
                "Invalid outcome", "validation_error", status=422,
                details={"field": "outcome", "allowed": sorted(valid)},
            )
        qs = qs.filter(outcome=outcome)

    stripe_event_id = (request.GET.get("stripe_event_id") or "").strip()
    if stripe_event_id:
        qs = qs.filter(stripe_event_id=stripe_event_id)

    customer_id = (request.GET.get("customer_id") or "").strip()
    if customer_id:
        qs = qs.filter(stripe_customer_id=customer_id)

    subscription_id = (request.GET.get("subscription_id") or "").strip()
    if subscription_id:
        qs = qs.filter(stripe_subscription_id=subscription_id)

    charge_id = (request.GET.get("charge_id") or "").strip()
    if charge_id:
        qs = qs.filter(stripe_charge_id=charge_id)

    invoice_id = (request.GET.get("invoice_id") or "").strip()
    if invoice_id:
        qs = qs.filter(stripe_invoice_id=invoice_id)

    dispute_id = (request.GET.get("dispute_id") or "").strip()
    if dispute_id:
        qs = qs.filter(stripe_dispute_id=dispute_id)

    try:
        page = max(1, int(request.GET.get("page", "1")))
    except (TypeError, ValueError):
        return error_response(
            "page must be an integer", "validation_error", status=422,
            details={"field": "page"},
        )
    try:
        page_size = int(request.GET.get("page_size", str(DEFAULT_PAGE_SIZE)))
    except (TypeError, ValueError):
        return error_response(
            "page_size must be an integer", "validation_error", status=422,
            details={"field": "page_size"},
        )
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    total = qs.count()
    start = (page - 1) * page_size
    rows = list(qs[start:start + page_size])
    return JsonResponse({
        "count": total,
        "page": page,
        "page_size": page_size,
        "deliveries": [_serialize_attempt(a) for a in rows],
    })


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Payments",
    summary="Inspect or replay a Stripe cancellation event",
    methods={
        "POST": {
            "summary": "Dry-run preview (default) or confirmed replay",
            "description": (
                "Defaults to ``dry_run=true``. A write requires "
                "``dry_run=false`` and ``confirm=replay_cancellation_event`` "
                "and only same-mode customer.subscription.updated/deleted "
                "events resolving to exactly one target can write."
            ),
            "request_body": {
                "required": ["event_id"],
                "properties": {
                    "event_id": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "string"},
                },
                "example": {"event_id": "evt_123", "dry_run": True},
            },
            "responses": {200: {"description": "Preview or replay result."}},
        }
    },
)
def stripe_webhooks_replay(request):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object", "invalid_type", status=400,
            details={"field": "body", "expected": "object"},
        )

    event_id = data.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return error_response(
            "event_id is required", "validation_error", status=422,
            details={"field": "event_id"},
        )
    event_id = event_id.strip()

    dry_run = data.get("dry_run", True)
    if not isinstance(dry_run, bool):
        return error_response(
            "dry_run must be a boolean", "invalid_type", status=422,
            details={"field": "dry_run"},
        )
    confirm = data.get("confirm", "") or ""
    if not isinstance(confirm, str):
        return error_response(
            "confirm must be a string", "invalid_type", status=422,
            details={"field": "confirm"},
        )

    try:
        result = replay_event(
            event_id,
            requested_by=request.user,
            dry_run=dry_run,
            confirm=confirm,
        )
    except ReplayError as exc:
        return error_response(
            exc.message, exc.code, status=exc.http_status,
        )
    return JsonResponse(result)
