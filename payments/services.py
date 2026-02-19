"""Business logic for Stripe payments and subscription lifecycle.

All Stripe API calls and user-tier updates are centralized here so views
stay thin and logic is easy to test.
"""

import logging
from datetime import datetime, timezone

import stripe
from django.conf import settings
from django.core.mail import send_mail

from accounts.models import User
from payments.models import Tier, WebhookEvent

logger = logging.getLogger(__name__)


def _get_stripe_client():
    """Return a configured Stripe client using the secret key from settings."""
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY)


def _tier_for_price_id(price_id):
    """Look up a Tier by its monthly or yearly Stripe price ID.

    Returns None if no tier matches or if price_id is empty.
    """
    if not price_id:
        return None
    tier = Tier.objects.filter(stripe_price_id_monthly=price_id).first()
    if tier is None:
        tier = Tier.objects.filter(stripe_price_id_yearly=price_id).first()
    return tier


def create_checkout_session(user, tier_slug, billing_period, success_url, cancel_url):
    """Create a Stripe Checkout Session for the given tier and billing period.

    Args:
        user: The authenticated User instance.
        tier_slug: Slug of the tier to purchase (e.g. "basic", "main").
        billing_period: Either "monthly" or "yearly".
        success_url: URL to redirect to after successful checkout.
        cancel_url: URL to redirect to if the user cancels.

    Returns:
        The Stripe Checkout Session object.

    Raises:
        ValueError: If the tier or price_id is not found.
    """
    try:
        tier = Tier.objects.get(slug=tier_slug)
    except Tier.DoesNotExist:
        raise ValueError(f"Tier '{tier_slug}' not found.")

    if billing_period == "yearly":
        price_id = tier.stripe_price_id_yearly
    else:
        price_id = tier.stripe_price_id_monthly

    if not price_id:
        raise ValueError(
            f"No Stripe price ID configured for tier '{tier_slug}' ({billing_period})."
        )

    client = _get_stripe_client()

    session_params = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(user.pk),
        "customer_email": user.email,
        "metadata": {
            "user_id": str(user.pk),
            "tier_slug": tier_slug,
        },
    }

    # If user already has a Stripe customer ID, use it instead of email
    if user.stripe_customer_id:
        session_params.pop("customer_email")
        session_params["customer"] = user.stripe_customer_id

    session = client.checkout.sessions.create(params=session_params)
    return session


def handle_checkout_completed(session_data):
    """Process a checkout.session.completed event.

    Sets the user's tier, stripe_customer_id, subscription_id, and
    billing_period_end based on the completed checkout session.
    """
    customer_email = session_data.get("customer_details", {}).get("email", "")
    customer_id = session_data.get("customer", "")
    subscription_id = session_data.get("subscription", "")
    client_reference_id = session_data.get("client_reference_id")
    metadata = session_data.get("metadata", {})
    tier_slug = metadata.get("tier_slug", "")

    # Look up user: first by client_reference_id (user PK), then by email
    user = None
    if client_reference_id:
        user = User.objects.filter(pk=client_reference_id).first()
    if user is None and customer_email:
        user = User.objects.filter(email=customer_email).first()
    if user is None and customer_email:
        # Create a new user if one doesn't exist (spec says "or creates a new user")
        user = User.objects.create_user(email=customer_email)

    if user is None:
        logger.error(
            "checkout.session.completed: Could not find or create user. "
            "session_id=%s",
            session_data.get("id"),
        )
        return

    # Look up the purchased tier
    tier = None
    if tier_slug:
        tier = Tier.objects.filter(slug=tier_slug).first()

    # If tier not found via metadata, try to look up from subscription
    if tier is None and subscription_id:
        tier = _tier_from_subscription(subscription_id)

    if tier is None:
        logger.error(
            "checkout.session.completed: Could not determine tier. "
            "tier_slug=%s, session_id=%s",
            tier_slug,
            session_data.get("id"),
        )
        return

    # Update user fields
    user.tier = tier
    user.stripe_customer_id = customer_id or user.stripe_customer_id
    user.subscription_id = subscription_id or user.subscription_id
    user.pending_tier = None  # Clear any pending downgrade

    # Set billing_period_end from subscription if available
    if subscription_id:
        billing_end = _get_subscription_period_end(subscription_id)
        if billing_end:
            user.billing_period_end = billing_end

    user.save(
        update_fields=[
            "tier",
            "stripe_customer_id",
            "subscription_id",
            "billing_period_end",
            "pending_tier",
        ]
    )
    logger.info(
        "checkout.session.completed: user=%s tier=%s", user.email, tier.slug
    )

    # Community integration: invite user if tier qualifies (Main+ = level >= 20)
    if tier.level >= 20:
        _community_invite(user)


def handle_subscription_updated(subscription_data):
    """Process a customer.subscription.updated event.

    Updates the user's tier if the plan changed and updates billing_period_end.
    If a pending downgrade is scheduled (schedule change at period end),
    sets pending_tier instead of changing tier immediately.
    """
    subscription_id = subscription_data.get("id", "")
    customer_id = subscription_data.get("customer", "")
    status = subscription_data.get("status", "")
    cancel_at_period_end = subscription_data.get("cancel_at_period_end", False)

    # Get the current price from subscription items
    items = subscription_data.get("items", {}).get("data", [])
    price_id = ""
    if items:
        price_id = items[0].get("price", {}).get("id", "")

    # Find the user by subscription_id or customer_id
    user = User.objects.filter(subscription_id=subscription_id).first()
    if user is None and customer_id:
        user = User.objects.filter(stripe_customer_id=customer_id).first()

    if user is None:
        logger.error(
            "customer.subscription.updated: Could not find user. "
            "subscription_id=%s, customer_id=%s",
            subscription_id,
            customer_id,
        )
        return

    # Update billing_period_end
    current_period_end = subscription_data.get("current_period_end")
    if current_period_end:
        user.billing_period_end = datetime.fromtimestamp(
            current_period_end, tz=timezone.utc
        )

    # Check if this is a scheduled change (Stripe schedule or pending items)
    # If cancel_at_period_end is True, user is cancelling - don't change tier
    if cancel_at_period_end:
        user.save(update_fields=["billing_period_end"])
        logger.info(
            "customer.subscription.updated: cancel_at_period_end for user=%s",
            user.email,
        )
        # Schedule community removal at billing period end
        if user.tier and user.tier.level >= 20 and user.billing_period_end:
            _community_schedule_removal(user)
        return

    # Look up the new tier from price_id
    old_tier_level = user.tier.level if user.tier else 0
    if price_id:
        new_tier = _tier_for_price_id(price_id)
        if new_tier and new_tier != user.tier:
            # Check if this is an active subscription update
            if status == "active":
                user.tier = new_tier
                user.pending_tier = None
                logger.info(
                    "customer.subscription.updated: user=%s new_tier=%s",
                    user.email,
                    new_tier.slug,
                )

    user.subscription_id = subscription_id
    user.save(
        update_fields=[
            "tier",
            "subscription_id",
            "billing_period_end",
            "pending_tier",
        ]
    )

    # Community integration: handle tier changes
    new_tier_level = user.tier.level if user.tier else 0
    if new_tier_level >= 20 and old_tier_level < 20:
        # Re-subscribe: user upgraded back to community-eligible tier
        _community_reactivate(user)
    elif new_tier_level < 20 and old_tier_level >= 20:
        # Immediate downgrade below Main: remove from community now
        _community_remove(user)


def handle_subscription_deleted(subscription_data):
    """Process a customer.subscription.deleted event.

    Sets user's tier to 'free' and clears subscription fields.
    """
    subscription_id = subscription_data.get("id", "")
    customer_id = subscription_data.get("customer", "")

    user = User.objects.filter(subscription_id=subscription_id).first()
    if user is None and customer_id:
        user = User.objects.filter(stripe_customer_id=customer_id).first()

    if user is None:
        logger.error(
            "customer.subscription.deleted: Could not find user. "
            "subscription_id=%s",
            subscription_id,
        )
        return

    # Check if user had community access before downgrade
    had_community = user.tier and user.tier.level >= 20

    free_tier = Tier.objects.filter(slug="free").first()
    user.tier = free_tier
    user.subscription_id = ""
    user.billing_period_end = None
    user.pending_tier = None
    user.save(
        update_fields=[
            "tier",
            "subscription_id",
            "billing_period_end",
            "pending_tier",
        ]
    )
    logger.info(
        "customer.subscription.deleted: user=%s reverted to free", user.email
    )

    # Community integration: remove if they had community access
    if had_community:
        _community_remove(user)


def handle_invoice_payment_failed(invoice_data):
    """Process an invoice.payment_failed event.

    Sends an email to the user with a payment update link.
    Does NOT revoke the tier.
    """
    customer_id = invoice_data.get("customer", "")
    customer_email = invoice_data.get("customer_email", "")

    user = None
    if customer_id:
        user = User.objects.filter(stripe_customer_id=customer_id).first()
    if user is None and customer_email:
        user = User.objects.filter(email=customer_email).first()

    if user is None:
        logger.error(
            "invoice.payment_failed: Could not find user. customer_id=%s",
            customer_id,
        )
        return

    portal_url = getattr(settings, "STRIPE_CUSTOMER_PORTAL_URL", "")

    try:
        send_mail(
            subject="Payment failed - please update your payment method",
            message=(
                f"Hi,\n\n"
                f"Your recent payment for AI Shipping Labs membership failed. "
                f"Please update your payment method to keep your subscription active.\n\n"
                f"Update payment method: {portal_url}\n\n"
                f"If you have any questions, reply to this email.\n\n"
                f"- AI Shipping Labs"
            ),
            from_email=None,  # Uses DEFAULT_FROM_EMAIL
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception:
        logger.exception(
            "invoice.payment_failed: Failed to send email to user=%s",
            user.email,
        )

    logger.info(
        "invoice.payment_failed: notified user=%s (tier NOT revoked)",
        user.email,
    )


def upgrade_subscription(user, new_tier_slug, billing_period):
    """Upgrade a user's subscription via Stripe (proration).

    Updates the existing subscription to the new price. Stripe handles
    proration automatically. The actual tier change happens via webhook
    when Stripe fires customer.subscription.updated.

    Args:
        user: The authenticated User instance.
        new_tier_slug: Slug of the tier to upgrade to.
        billing_period: Either "monthly" or "yearly".

    Returns:
        The updated Stripe Subscription object.

    Raises:
        ValueError: If the tier, price_id, or subscription is not found.
    """
    if not user.subscription_id:
        raise ValueError("User has no active subscription to upgrade.")

    try:
        tier = Tier.objects.get(slug=new_tier_slug)
    except Tier.DoesNotExist:
        raise ValueError(f"Tier '{new_tier_slug}' not found.")

    if billing_period == "yearly":
        price_id = tier.stripe_price_id_yearly
    else:
        price_id = tier.stripe_price_id_monthly

    if not price_id:
        raise ValueError(
            f"No Stripe price ID configured for tier '{new_tier_slug}' ({billing_period})."
        )

    client = _get_stripe_client()

    # Get the current subscription to find the item ID
    subscription = client.subscriptions.retrieve(user.subscription_id)
    item_id = subscription.items.data[0].id

    # Update subscription with proration (Stripe default behavior)
    updated = client.subscriptions.update(
        user.subscription_id,
        params={
            "items": [{"id": item_id, "price": price_id}],
            "proration_behavior": "create_prorations",
        },
    )

    return updated


def downgrade_subscription(user, new_tier_slug, billing_period):
    """Schedule a downgrade at the end of the current billing period.

    Sets pending_tier on the user. The actual plan change is scheduled
    via Stripe's subscription schedule so it takes effect at period end.

    Args:
        user: The authenticated User instance.
        new_tier_slug: Slug of the tier to downgrade to.
        billing_period: Either "monthly" or "yearly".

    Returns:
        The updated Stripe Subscription object.

    Raises:
        ValueError: If the tier, price_id, or subscription is not found.
    """
    if not user.subscription_id:
        raise ValueError("User has no active subscription to downgrade.")

    try:
        new_tier = Tier.objects.get(slug=new_tier_slug)
    except Tier.DoesNotExist:
        raise ValueError(f"Tier '{new_tier_slug}' not found.")

    if billing_period == "yearly":
        price_id = new_tier.stripe_price_id_yearly
    else:
        price_id = new_tier.stripe_price_id_monthly

    if not price_id:
        raise ValueError(
            f"No Stripe price ID configured for tier '{new_tier_slug}' ({billing_period})."
        )

    client = _get_stripe_client()

    # Get the current subscription to find the item ID
    subscription = client.subscriptions.retrieve(user.subscription_id)
    item_id = subscription.items.data[0].id

    # Schedule the change at period end (no proration)
    updated = client.subscriptions.update(
        user.subscription_id,
        params={
            "items": [{"id": item_id, "price": price_id}],
            "proration_behavior": "none",
            "billing_cycle_anchor": "unchanged",
        },
    )

    # Set pending_tier on the user so the UI can show "changing to X at period end"
    user.pending_tier = new_tier
    user.save(update_fields=["pending_tier"])

    return updated


def cancel_subscription(user):
    """Cancel a user's subscription at the end of the billing period.

    The user keeps access until billing_period_end. When Stripe fires
    customer.subscription.deleted at period end, the webhook handler
    sets the user's tier back to 'free'.

    Args:
        user: The authenticated User instance.

    Returns:
        The updated Stripe Subscription object.

    Raises:
        ValueError: If the user has no active subscription.
    """
    if not user.subscription_id:
        raise ValueError("User has no active subscription to cancel.")

    client = _get_stripe_client()
    updated = client.subscriptions.update(
        user.subscription_id,
        params={"cancel_at_period_end": True},
    )

    return updated


def verify_webhook_signature(payload, sig_header):
    """Verify a Stripe webhook signature.

    Args:
        payload: The raw request body (bytes).
        sig_header: The Stripe-Signature header value.

    Returns:
        The verified Stripe Event object.

    Raises:
        stripe.SignatureVerificationError: If the signature is invalid.
        ValueError: If the payload is invalid.
    """
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET
    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    return event


def is_event_already_processed(event_id):
    """Check if a webhook event has already been processed (idempotency)."""
    return WebhookEvent.objects.filter(stripe_event_id=event_id).exists()


def record_processed_event(event_id, event_type, payload=None):
    """Record that a webhook event has been processed."""
    WebhookEvent.objects.get_or_create(
        stripe_event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": payload or {},
        },
    )


def _tier_from_subscription(subscription_id):
    """Look up the tier from a Stripe subscription's price ID."""
    try:
        client = _get_stripe_client()
        subscription = client.subscriptions.retrieve(subscription_id)
        if subscription.items.data:
            price_id = subscription.items.data[0].price.id
            return _tier_for_price_id(price_id)
    except Exception:
        logger.exception(
            "Failed to look up tier from subscription %s", subscription_id
        )
    return None


def _get_subscription_period_end(subscription_id):
    """Get the current_period_end from a Stripe subscription."""
    try:
        client = _get_stripe_client()
        subscription = client.subscriptions.retrieve(subscription_id)
        if subscription.current_period_end:
            return datetime.fromtimestamp(
                subscription.current_period_end, tz=timezone.utc
            )
    except Exception:
        logger.exception(
            "Failed to get period end for subscription %s", subscription_id
        )
    return None


# ---------------------------------------------------------------------------
# Community integration helpers
# ---------------------------------------------------------------------------

def _community_invite(user):
    """Invite a user to the community via a background task."""
    try:
        from jobs.tasks import async_task
        async_task(
            "community.tasks.hooks.community_invite_task",
            user_id=user.pk,
        )
    except Exception:
        logger.exception("Failed to enqueue community invite for user=%s", user.email)


def _community_reactivate(user):
    """Reactivate a user in the community via a background task."""
    try:
        from jobs.tasks import async_task
        async_task(
            "community.tasks.hooks.community_reactivate_task",
            user_id=user.pk,
        )
    except Exception:
        logger.exception("Failed to enqueue community reactivate for user=%s", user.email)


def _community_remove(user):
    """Remove a user from the community via a background task."""
    try:
        from jobs.tasks import async_task
        async_task(
            "community.tasks.hooks.community_remove_task",
            user_id=user.pk,
        )
    except Exception:
        logger.exception("Failed to enqueue community remove for user=%s", user.email)


def _community_schedule_removal(user):
    """Schedule community removal at billing_period_end via a background task."""
    try:
        from jobs.tasks import async_task
        async_task(
            "community.tasks.removal.scheduled_community_removal",
            user_id=user.pk,
        )
    except Exception:
        logger.exception(
            "Failed to schedule community removal for user=%s", user.email
        )
