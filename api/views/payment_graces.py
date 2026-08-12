"""Staff-token, read-only monthly payment-grace API (issue #1413)."""


from django.http import JsonResponse
from django.utils.dateparse import parse_datetime

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from payments.models import MonthlyPaymentGrace as Grace
from payments.services.monthly_payment_grace import _effective_tier

VALID_STATUS = {value for value, _ in Grace.STATUS_CHOICES}
VALID_SOURCE = {value for value, _ in Grace.SOURCE_CHOICES}
VALID_TIERS = {"free", "basic", "main", "premium"}
VALID_DELIVERY_STATUS = {"pending", "sent", "failed"}
PAGE_SIZE = 100


def _dt(value):
    return value.isoformat() if value else None


def _serialize(grace, *, detail=False):
    effective = _effective_tier(grace.user)
    deliveries = [
        {
            "kind": delivery.kind,
            "status": delivery.status,
            "recipient": delivery.recipient,
            "attempt_count": delivery.attempt_count,
            "last_attempt_at": _dt(delivery.last_attempt_at),
            "transport_started_at": _dt(delivery.transport_started_at),
            "sent_at": _dt(delivery.sent_at),
            "last_error": delivery.last_error,
        }
        for delivery in grace.deliveries.all()
    ]
    data = {
        "id": str(grace.pk),
        "status": grace.status,
        "source": grace.source,
        "user": {"id": grace.user_id, "email": grace.user.email},
        "base_tier": grace.user.tier.slug if grace.user.tier_id else "free",
        "base_tier_at_start": grace.base_tier_at_start.slug,
        "effective_tier": effective.slug if effective else "free",
        "stripe_customer_id": grace.stripe_customer_id,
        "stripe_subscription_id": grace.stripe_subscription_id,
        "stripe_invoice_id": grace.stripe_invoice_id,
        "livemode": grace.livemode,
        "interval": grace.interval,
        "interval_count": grace.interval_count,
        "grace_started_at": _dt(grace.grace_started_at),
        "grace_expires_at": _dt(grace.grace_expires_at),
        "effective_expires_at": _dt(grace.effective_expires_at),
        "policy_enforced_at": _dt(grace.policy_enforced_at),
        "recovered_at": _dt(grace.recovered_at),
        "expired_at": _dt(grace.expired_at),
        "last_checked_at": _dt(grace.last_checked_at),
        "review": {
            "code": grace.last_error_code,
            "message": grace.last_error_message,
        },
        "deliveries": deliveries,
    }
    if detail:
        data["events"] = {
            "last_failure_event_id": grace.last_failure_event_id,
            "recovery_event_id": grace.recovery_event_id,
        }
        data["created_at"] = _dt(grace.created_at)
        data["updated_at"] = _dt(grace.updated_at)
    return data


def _validation(field, message):
    return error_response(
        message, "validation_error", status=422, details={"field": field},
    )


def _parse_datetime_filter(request, field):
    raw = (request.GET.get(field) or "").strip()
    if not raw:
        return None, None
    value = parse_datetime(raw)
    if value is None or value.tzinfo is None:
        return None, _validation(field, f"{field} must be an ISO-8601 timezone-aware datetime")
    return value, None


@token_required
@require_methods("GET")
@openapi_spec(
    tag="Payments",
    summary="List monthly payment graces",
    methods={"GET": {
        "description": "Staff-token-only read-only payment-grace queue with stable filters and pagination.",
        "query": {
            "status": {"type": "string", "required": False},
            "user": {"type": "string", "required": False},
            "email": {"type": "string", "required": False},
            "tier": {"type": "string", "required": False},
            "interval": {"type": "string", "required": False},
            "source": {"type": "string", "required": False},
            "delivery_status": {"type": "string", "required": False},
            "started_from": {"type": "string", "required": False},
            "started_to": {"type": "string", "required": False},
            "page": {"type": "integer", "required": False},
            "page_size": {"type": "integer", "required": False},
        },
        "responses": {200: {"description": "Paginated graces"}, 401: {"description": "Invalid staff token"}, 422: {"description": "Invalid filter"}},
    }},
)
def payment_graces_collection(request):
    qs = Grace.objects.select_related("user__tier", "base_tier_at_start").prefetch_related("deliveries")
    status = (request.GET.get("status") or "").strip().lower()
    if status:
        if status not in VALID_STATUS:
            return _validation("status", "Unknown status filter")
        qs = qs.filter(status=status)
    source = (request.GET.get("source") or "").strip().lower()
    if source:
        if source not in VALID_SOURCE:
            return _validation("source", "Unknown source filter")
        qs = qs.filter(source=source)
    tier = (request.GET.get("tier") or "").strip().lower()
    if tier:
        if tier not in VALID_TIERS:
            return _validation("tier", "Unknown tier filter")
        qs = qs.filter(user__tier__slug=tier)
    interval = (request.GET.get("interval") or "").strip().lower()
    if interval:
        if interval not in {"month", "year", "week", "day"}:
            return _validation("interval", "Unknown interval filter")
        qs = qs.filter(interval=interval)
    delivery_status = (request.GET.get("delivery_status") or "").strip().lower()
    if delivery_status:
        if delivery_status not in VALID_DELIVERY_STATUS:
            return _validation("delivery_status", "Unknown delivery status filter")
        qs = qs.filter(deliveries__status=delivery_status)
    user = (request.GET.get("user") or "").strip()
    if user:
        if not user.isdigit():
            return _validation("user", "user must be an integer ID")
        qs = qs.filter(user_id=int(user))
    email = (request.GET.get("email") or "").strip()
    if email:
        qs = qs.filter(user__email__icontains=email)
    started_from, err = _parse_datetime_filter(request, "started_from")
    if err:
        return err
    started_to, err = _parse_datetime_filter(request, "started_to")
    if err:
        return err
    if started_from:
        qs = qs.filter(grace_started_at__gte=started_from)
    if started_to:
        qs = qs.filter(grace_started_at__lte=started_to)
    try:
        page = int(request.GET.get("page", "1"))
        page_size = int(request.GET.get("page_size", str(PAGE_SIZE)))
    except (TypeError, ValueError):
        return _validation("page", "page and page_size must be integers")
    if page < 1 or page_size < 1 or page_size > 500:
        return _validation("page", "page must be >= 1 and page_size in 1..500")
    qs = qs.distinct().order_by("-grace_started_at", "-created_at")
    total = qs.count()
    start = (page - 1) * page_size
    rows = list(qs[start:start + page_size])
    return JsonResponse({
        "count": total,
        "page": page,
        "next_cursor": str(page + 1) if start + page_size < total else None,
        "payment_graces": [_serialize(row) for row in rows],
    })


@token_required
@require_methods("GET")
@openapi_spec(
    tag="Payments",
    summary="Get one monthly payment grace",
    methods={"GET": {"responses": {200: {"description": "Payment grace detail"}, 401: {"description": "Invalid staff token"}, 404: {"description": "Not found"}}}},
)
def payment_grace_detail(request, grace_id):
    grace = (
        Grace.objects.select_related("user__tier", "base_tier_at_start")
        .prefetch_related("deliveries").filter(pk=grace_id).first()
    )
    if grace is None:
        return error_response("Payment grace not found", "not_found", status=404)
    return JsonResponse({"payment_grace": _serialize(grace, detail=True)})
