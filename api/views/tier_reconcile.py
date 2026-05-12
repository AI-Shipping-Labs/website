"""Tier-reconciliation API endpoints (issue #621).

Two JSON-in / JSON-out endpoints under ``/api/payments/`` that wrap the
existing ``payments.services.backfill_tiers.backfill_user_from_stripe``
service so the orchestrator can diagnose and fix mis-tier'd customers
remotely without prod shell access.

Both endpoints are gated by ``token_required``, which is staff-only by
construction: the ``Token`` model rejects non-staff token rows in
``clean()`` and the decorator re-validates the staff flag on every call.

A per-request hard cap (``MAX_USERS_PER_REQUEST``, default 500) protects
Stripe from accidental no-arg pulls. Operators above the cap should batch
via the explicit ``emails`` list on the apply endpoint.
"""

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.models import TierOverride
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from payments.services.backfill_tiers import backfill_user_from_stripe
from payments.services.import_stripe import _price_to_tier_map

User = get_user_model()

MAX_USERS_PER_REQUEST = 500


def _eligible_users_queryset():
    """Users who could conceivably have a Stripe-driven tier mismatch."""
    return (
        User.objects.exclude(stripe_customer_id="")
        .select_related("tier")
        .order_by("email")
    )


def _current_tier_slug(user):
    if user.tier_id and user.tier:
        return user.tier.slug
    return "free"


def _current_tier_source(user):
    """Where the user's current effective tier comes from.

    Returns ``"override"`` when an active TierOverride matches the user's
    current direct tier (so the override is what's holding them at that
    level — the "redundant override" case from #473). Returns ``"direct"``
    when ``user.tier`` is paid and no matching override is active. Returns
    ``"none"`` when the user is on free.
    """
    slug = _current_tier_slug(user)
    if slug == "free":
        return "none"
    has_matching_override = TierOverride.objects.filter(
        user_id=user.pk,
        override_tier_id=user.tier_id,
        is_active=True,
        expires_at__gt=timezone.now(),
    ).exists()
    if has_matching_override:
        return "override"
    return "direct"


def _action_for_record(record):
    """Translate a ``ChangeRecord`` into the documented ``action_needed``."""
    status = record.status
    if status == "warning":
        if "no active Stripe subscription" in record.message:
            return "warning_no_active_subscription"
        return "warning_unknown_price"
    if status == "skipped":
        return "noop"
    tier_changed = record.old_tier_slug != record.new_tier_slug
    if tier_changed:
        return "set_direct_tier"
    if record.override_deactivated:
        return "deactivate_override"
    if record.metadata_saved:
        return "save_metadata"
    return "noop"


def _serialize_diagnostic(user, record):
    subscription_id = record.subscription_id or ""
    if record.status == "warning" and "no active Stripe subscription" in record.message:
        stripe_active_tier = None
    elif record.status == "warning":
        stripe_active_tier = "unknown"
    else:
        # skipped or dry_run: ChangeRecord.new_tier_slug holds the Stripe
        # tier when there is one. Empty string means there was no active
        # subscription at all (skipped, free user, no Stripe sub).
        stripe_active_tier = record.new_tier_slug or None

    billing_period_end = None
    if user.billing_period_end is not None:
        billing_period_end = user.billing_period_end.isoformat()

    return {
        "email": user.email,
        "stripe_customer_id": user.stripe_customer_id,
        "current_tier": _current_tier_slug(user),
        "current_tier_source": _current_tier_source(user),
        "stripe_active_tier": stripe_active_tier,
        "subscription_id": subscription_id,
        "billing_period_end": billing_period_end,
        "action_needed": _action_for_record(record),
    }


@token_required
@require_methods("GET")
def tier_reconcile_diagnostics(request):
    """``GET /api/payments/tier-reconcile/diagnostics``.

    Returns users whose ``user.tier`` likely needs reconciling against their
    active Stripe subscription, computed by calling
    ``backfill_user_from_stripe(user, dry_run=True)`` per candidate user.

    Query params:
    - ``email`` -- filter to a single user (case-insensitive). Returns
      ``count: 0`` when the email is unknown or has no Stripe customer ID;
      this endpoint is a search, not a lookup, so it never returns 404.
    - ``include=ok`` -- include users whose tier already matches Stripe.
      They appear with ``action_needed: "noop"``.

    Capped at ``MAX_USERS_PER_REQUEST`` (default 500) eligible users.
    Above the cap returns 429 ``too_many_users``.
    """
    email_filter = (request.GET.get("email") or "").strip()
    include_ok = (request.GET.get("include") or "") == "ok"

    queryset = _eligible_users_queryset()
    if email_filter:
        queryset = queryset.filter(email__iexact=email_filter)

    eligible_users = list(queryset[: MAX_USERS_PER_REQUEST + 1])
    if len(eligible_users) > MAX_USERS_PER_REQUEST:
        return error_response(
            (
                f"Too many users to process in one request "
                f"(max {MAX_USERS_PER_REQUEST}). Pass an explicit ?email= "
                f"filter or batch via the apply endpoint."
            ),
            "too_many_users",
            status=429,
        )

    price_to_tier = _price_to_tier_map()

    users_payload = []
    for user in eligible_users:
        record = backfill_user_from_stripe(
            user,
            dry_run=True,
            price_to_tier=price_to_tier,
        )
        if record.status == "skipped" and not include_ok:
            continue
        users_payload.append(_serialize_diagnostic(user, record))

    return JsonResponse(
        {"count": len(users_payload), "users": users_payload},
        status=200,
    )


def _normalize_apply_status(status):
    if status == "dry_run":
        return "would_change"
    return status


def _serialize_apply_result(email, record, *, status):
    return {
        "email": email,
        "status": status,
        "from": record.old_tier_slug or None,
        "to": record.new_tier_slug or None,
        "subscription_id": record.subscription_id or "",
        "deactivated_override": record.override_deactivated,
        "saved_metadata": record.metadata_saved,
        "audit_event_id": record.audit_event_id or "",
        "message": record.message,
    }


@token_required
@csrf_exempt
@require_methods("POST")
def tier_reconcile_apply(request):
    """``POST /api/payments/tier-reconcile``.

    Body (JSON object):

        {"emails": ["user@example.com"], "dry_run": false}

    Both fields are optional. When ``emails`` is omitted the endpoint
    processes every user with a non-empty ``stripe_customer_id`` (capped
    at ``MAX_USERS_PER_REQUEST``; above the cap returns 429
    ``too_many_users``). When ``dry_run`` is true the underlying service
    runs in dry-run mode and the per-row status is normalized from
    ``"dry_run"`` to ``"would_change"`` so clients don't special-case it.

    Per-row ``status`` is one of ``"changed"``, ``"would_change"``,
    ``"skipped"``, ``"warning"``, ``"not_found"``. Top-level counters
    (``processed``, ``changed``, ``skipped``, ``warnings``) sum to
    ``processed`` and exclude ``not_found`` rows by design — the
    ``processed`` field counts users actually iterated, not emails
    requested.
    """
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return error_response(
            "Body must be valid JSON",
            "invalid_json",
        )
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    emails_raw = data.get("emails", None)
    if emails_raw is not None and not isinstance(emails_raw, list):
        return error_response(
            "emails must be a list of strings",
            "invalid_type",
            details={"field": "emails", "expected": "list"},
        )

    dry_run_raw = data.get("dry_run", False)
    if not isinstance(dry_run_raw, bool):
        return error_response(
            "dry_run must be a boolean",
            "invalid_type",
            details={"field": "dry_run", "expected": "bool"},
        )

    requested_emails = None
    if emails_raw is not None:
        for index, value in enumerate(emails_raw):
            if not isinstance(value, str) or not value.strip():
                return error_response(
                    "Each emails entry must be a non-empty string",
                    "validation_error",
                    status=422,
                    details={"field": f"emails[{index}]"},
                )
        requested_emails = [value.strip() for value in emails_raw]

    queryset = _eligible_users_queryset()
    if requested_emails is not None:
        normalized = [email.lower() for email in requested_emails]
        users_by_email = {
            user.email.lower(): user
            for user in queryset.filter(email__in=normalized)
        }
        # Also handle case-insensitive matches that bypass the IN clause
        # on case-sensitive collations.
        if len(users_by_email) != len(set(normalized)):
            users_by_email = {}
            for user in queryset:
                lowered = user.email.lower()
                if lowered in normalized and lowered not in users_by_email:
                    users_by_email[lowered] = user
        targets = []
        seen = set()
        for original in requested_emails:
            lowered = original.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            user = users_by_email.get(lowered)
            targets.append((original, user))
    else:
        eligible = list(queryset[: MAX_USERS_PER_REQUEST + 1])
        if len(eligible) > MAX_USERS_PER_REQUEST:
            return error_response(
                (
                    f"Too many users to process in one request "
                    f"(max {MAX_USERS_PER_REQUEST}). Pass an explicit "
                    f"emails list to batch."
                ),
                "too_many_users",
                status=429,
            )
        targets = [(user.email, user) for user in eligible]

    price_to_tier = _price_to_tier_map()

    results = []
    processed = changed = skipped = warnings = 0
    for original_email, user in targets:
        if user is None:
            results.append({
                "email": original_email,
                "status": "not_found",
                "from": None,
                "to": None,
                "subscription_id": "",
                "deactivated_override": False,
                "saved_metadata": False,
                "audit_event_id": "",
                "message": f"not found: no user with email {original_email} and a Stripe customer ID",
            })
            continue

        record = backfill_user_from_stripe(
            user,
            dry_run=dry_run_raw,
            price_to_tier=price_to_tier,
        )
        normalized_status = _normalize_apply_status(record.status)
        results.append(_serialize_apply_result(
            user.email,
            record,
            status=normalized_status,
        ))
        processed += 1
        if normalized_status in ("changed", "would_change"):
            changed += 1
        elif normalized_status == "skipped":
            skipped += 1
        elif normalized_status == "warning":
            warnings += 1

    return JsonResponse(
        {
            "processed": processed,
            "changed": changed,
            "skipped": skipped,
            "warnings": warnings,
            "dry_run": dry_run_raw,
            "results": results,
        },
        status=200,
    )
