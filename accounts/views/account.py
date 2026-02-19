"""Account page view and email preferences API."""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from payments.models import Tier


@login_required
def account_view(request):
    """Render the account page showing tier, billing info, and actions."""
    user = request.user
    tier = user.tier
    pending_tier = user.pending_tier

    # Determine tier level for conditional display
    is_free = tier is None or tier.level == 0
    is_premium = tier is not None and tier.slug == "premium"
    is_basic = tier is not None and tier.slug == "basic"
    has_subscription = bool(user.subscription_id)

    # Check if subscription is cancelled (cancel_at_period_end)
    # We detect this by: user has a subscription and billing_period_end is set,
    # but the tier will revert to free (no pending_tier means full cancellation
    # vs pending_tier means downgrade). We use a convention: if the user has
    # a subscription_id and no pending_tier, but the Stripe subscription is set
    # to cancel_at_period_end, we store that state. Since we don't have a
    # dedicated "cancelled" field, we check via a different approach:
    # The cancel_subscription() service sets cancel_at_period_end on Stripe.
    # The webhook handler for subscription.updated with cancel_at_period_end
    # does NOT set pending_tier. So we need a way to track this.
    #
    # For now, we'll add a simple check: if the user has subscription_id and
    # billing_period_end but no pending_tier, we can't distinguish "active" from
    # "cancelled at period end" without querying Stripe. To avoid API calls on
    # every page load, we'll use a convention in email_preferences to store
    # cancel status, OR we detect it from the subscription_cancelled field.
    #
    # Actually, looking at the User model more carefully, there's no
    # subscription_cancelled field. The cleanest approach for the account page
    # is: if pending_tier is set, show "plan will change"; otherwise the user
    # is either active or cancelled-at-period-end. We'll need to check Stripe
    # or add a field. For this issue, let's add a simple boolean approach using
    # the existing fields: we'll treat "no subscription_id" as not subscribed.
    # The cancel flow sets cancel_at_period_end on Stripe but doesn't clear
    # subscription_id until the subscription is actually deleted.
    #
    # The simplest approach without a new field: store cancellation status in
    # a transient way. But the acceptance criteria say "If subscription is
    # cancelled, page shows: Your {tier} access ends on {billing_period_end}".
    # We need to know if the subscription is in "cancelled at period end" state.
    #
    # Let's check the email_preferences JSON to see if we can store it there,
    # or better yet, let's just check if pending_tier is None and the user
    # has a subscription. For the cancelled state, we need a dedicated field
    # or a convention. Since the issue says "cancel button calls API to cancel
    # subscription at period end", and the webhook handler in services.py
    # doesn't set pending_tier for cancellation, we need another indicator.
    #
    # Decision: We'll use a convention where pending_tier with slug="free"
    # means cancellation is scheduled. This aligns with the downgrade flow.
    # But actually, the cancel_subscription service doesn't set pending_tier.
    # Let's fix that in our cancel API endpoint instead, or use a different
    # approach: store "subscription_cancel_at_period_end" in email_preferences.
    #
    # Simplest approach that works with existing fields: when the user clicks
    # cancel on the account page, our API sets pending_tier to the free tier.
    # This way the account page can detect cancellation by checking if
    # pending_tier exists and pending_tier.slug == "free".

    # Determine display states
    is_pending_downgrade = (
        pending_tier is not None
        and pending_tier.slug != "free"
    )
    is_pending_cancellation = (
        pending_tier is not None
        and pending_tier.slug == "free"
    )

    # Get available tiers for upgrade/downgrade options
    all_tiers = list(Tier.objects.exclude(slug="free").order_by("level"))

    # Determine upgrade tiers (higher level than current)
    current_level = tier.level if tier else 0
    upgrade_tiers = [t for t in all_tiers if t.level > current_level]
    downgrade_tiers = [t for t in all_tiers if 0 < t.level < current_level]

    context = {
        "tier": tier,
        "pending_tier": pending_tier,
        "is_free": is_free,
        "is_premium": is_premium,
        "is_basic": is_basic,
        "has_subscription": has_subscription,
        "is_pending_downgrade": is_pending_downgrade,
        "is_pending_cancellation": is_pending_cancellation,
        "billing_period_end": user.billing_period_end,
        "upgrade_tiers": upgrade_tiers,
        "downgrade_tiers": downgrade_tiers,
        "email_preferences": user.email_preferences,
        "newsletter_subscribed": not user.unsubscribed,
    }

    return render(request, "accounts/account.html", context)


@login_required
@require_POST
def email_preferences_view(request):
    """Update email preferences (newsletter subscribe/unsubscribe).

    Expects JSON body with:
        newsletter: bool - True to subscribe, False to unsubscribe
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    newsletter = data.get("newsletter")
    if newsletter is None:
        return JsonResponse({"error": "newsletter field is required"}, status=400)

    user = request.user
    user.unsubscribed = not newsletter
    user.email_preferences["newsletter"] = newsletter
    user.save(update_fields=["unsubscribed", "email_preferences"])

    return JsonResponse({
        "status": "ok",
        "newsletter": newsletter,
    })


@login_required
@require_POST
def cancel_subscription_view(request):
    """Cancel subscription at period end and set pending_tier to free.

    This wraps the payments service cancel_subscription and also sets
    the pending_tier to free so the account page can show the cancellation
    status without querying Stripe.
    """
    from payments.services import cancel_subscription

    user = request.user

    if not user.subscription_id:
        return JsonResponse({"error": "No active subscription"}, status=400)

    try:
        cancel_subscription(user)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:
        return JsonResponse(
            {"error": "Failed to cancel subscription"}, status=500
        )

    # Set pending_tier to free to indicate cancellation at period end
    free_tier = Tier.objects.filter(slug="free").first()
    if free_tier:
        user.pending_tier = free_tier
        user.save(update_fields=["pending_tier"])

    return JsonResponse({"status": "ok"})
