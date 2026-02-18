"""Stripe Checkout views for creating checkout sessions and managing subscriptions."""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from payments.services import (
    cancel_subscription,
    create_checkout_session,
    downgrade_subscription,
    upgrade_subscription,
)

logger = logging.getLogger(__name__)


@login_required
@require_POST
def create_checkout(request):
    """Create a Stripe Checkout session and return the URL.

    Expects JSON body with:
        tier_slug: str - The tier to purchase (e.g. "basic", "main", "premium")
        billing_period: str - "monthly" or "yearly"

    Returns JSON with:
        checkout_url: str - The Stripe Checkout URL to redirect the user to
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    tier_slug = data.get("tier_slug", "")
    billing_period = data.get("billing_period", "monthly")

    if not tier_slug:
        return JsonResponse({"error": "tier_slug is required"}, status=400)

    if billing_period not in ("monthly", "yearly"):
        return JsonResponse(
            {"error": "billing_period must be 'monthly' or 'yearly'"}, status=400
        )

    success_url = request.build_absolute_uri("/pricing?checkout=success")
    cancel_url = request.build_absolute_uri("/pricing?checkout=cancelled")

    try:
        session = create_checkout_session(
            user=request.user,
            tier_slug=tier_slug,
            billing_period=billing_period,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:
        logger.exception("Failed to create checkout session")
        return JsonResponse({"error": "Failed to create checkout session"}, status=500)

    return JsonResponse({"checkout_url": session.url})


@login_required
@require_POST
def upgrade(request):
    """Upgrade the user's subscription (with proration).

    Expects JSON body with:
        tier_slug: str - The tier to upgrade to
        billing_period: str - "monthly" or "yearly"

    Returns JSON with status.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    tier_slug = data.get("tier_slug", "")
    billing_period = data.get("billing_period", "monthly")

    if not tier_slug:
        return JsonResponse({"error": "tier_slug is required"}, status=400)

    try:
        upgrade_subscription(
            user=request.user,
            new_tier_slug=tier_slug,
            billing_period=billing_period,
        )
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:
        logger.exception("Failed to upgrade subscription")
        return JsonResponse({"error": "Failed to upgrade subscription"}, status=500)

    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def downgrade(request):
    """Schedule a downgrade at the end of the current billing period.

    Expects JSON body with:
        tier_slug: str - The tier to downgrade to
        billing_period: str - "monthly" or "yearly"

    Returns JSON with status.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    tier_slug = data.get("tier_slug", "")
    billing_period = data.get("billing_period", "monthly")

    if not tier_slug:
        return JsonResponse({"error": "tier_slug is required"}, status=400)

    try:
        downgrade_subscription(
            user=request.user,
            new_tier_slug=tier_slug,
            billing_period=billing_period,
        )
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:
        logger.exception("Failed to downgrade subscription")
        return JsonResponse({"error": "Failed to downgrade subscription"}, status=500)

    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def cancel(request):
    """Cancel the user's subscription at the end of the billing period.

    Returns JSON with status.
    """
    try:
        cancel_subscription(user=request.user)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:
        logger.exception("Failed to cancel subscription")
        return JsonResponse({"error": "Failed to cancel subscription"}, status=500)

    return JsonResponse({"status": "ok"})
