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
from payments.exceptions import WebhookPermanentError
from payments.models import ConversionAttribution, Tier, WebhookEvent

# Re-exported so callers can ``from payments.services import WebhookPermanentError``
# without needing to know the exception lives in ``payments.exceptions``.
__all__ = ["WebhookPermanentError"]

logger = logging.getLogger(__name__)
_MISSING = object()


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


def _stripe_value(obj, key):
    """Read a StripeObject/dict field without tripping over mapping methods."""
    if obj is None:
        return _MISSING
    if isinstance(obj, dict):
        return obj.get(key, _MISSING)
    try:
        return obj[key]
    except (KeyError, TypeError, AttributeError):
        pass
    value = getattr(obj, key, _MISSING)
    if callable(value):
        return _MISSING
    return value


def _first_subscription_item(subscription):
    """Return the first subscription item from Stripe dict/object payloads."""
    items = _stripe_value(subscription, "items")
    if items is _MISSING:
        return None
    data = _stripe_value(items, "data")
    if data in (_MISSING, None):
        return None
    try:
        return data[0]
    except (IndexError, KeyError, TypeError):
        return None


def _subscription_price_id(subscription):
    item = _first_subscription_item(subscription)
    price = _stripe_value(item, "price")
    price_id = _stripe_value(price, "id")
    return price_id if price_id is not _MISSING and price_id else ""


def _subscription_period_end(subscription):
    period_end = _stripe_value(subscription, "current_period_end")
    if period_end in (_MISSING, None, ""):
        item = _first_subscription_item(subscription)
        period_end = _stripe_value(item, "current_period_end")
    if period_end in (_MISSING, None, ""):
        return None
    try:
        return datetime.fromtimestamp(period_end, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


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

    If metadata contains ``course_id``, creates a CourseAccess record
    for an individual course purchase (one-time payment). Otherwise,
    sets the user's tier, stripe_customer_id, subscription_id, and
    billing_period_end based on the completed checkout session.
    """
    customer_email = session_data.get("customer_details", {}).get("email", "")
    customer_id = session_data.get("customer", "")
    subscription_id = session_data.get("subscription", "")
    client_reference_id = session_data.get("client_reference_id")
    metadata = session_data.get("metadata", {})
    tier_slug = metadata.get("tier_slug", "")

    # Check if this is an individual course purchase
    course_id = metadata.get("course_id")
    if course_id:
        _handle_course_purchase(session_data, course_id)
        return

    # Look up user: first by client_reference_id (user PK), then by email
    user = None
    if client_reference_id:
        user = User.objects.filter(pk=client_reference_id).first()
    if user is None and customer_email:
        user = User.objects.filter(email=customer_email).first()
    if user is None and customer_email:
        # Create a new user if one doesn't exist (spec says "or creates a new user")
        # Flag the creation so the analytics post_save handler can record
        # signup_path='stripe_checkout' on the resulting UserAttribution row
        # (the webhook runs without an HttpRequest bound to the thread).
        from analytics.request_context import set_stripe_user_creation
        set_stripe_user_creation()
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

    # Determine billing period from the Stripe subscription's price ID.
    billing_period = ""
    if subscription_id:
        sub_price_id = _get_subscription_price_id(subscription_id)
        if sub_price_id and sub_price_id == tier.stripe_price_id_yearly:
            billing_period = "yearly"
        elif sub_price_id and sub_price_id == tier.stripe_price_id_monthly:
            billing_period = "monthly"

    # Snapshot UTM attribution for this conversion. Wrapped so an
    # attribution failure NEVER breaks the tier/customer/subscription
    # update above — the user's payment is the source of truth; missing
    # attribution is a warning, not an error.
    try:
        _record_conversion_attribution(
            user=user,
            session_data=session_data,
            tier=tier,
            billing_period=billing_period,
            amount_eur=None,  # derived inside the helper from tier + billing_period
        )
    except Exception:
        logger.exception(
            "Failed to record ConversionAttribution for user=%s session=%s",
            user.email,
            session_data.get("id"),
        )

    # Community integration: invite user if tier qualifies (Main+ = level >= 20)
    if tier.level >= 20:
        _community_invite(user)


def _handle_course_purchase(session_data, course_id):
    """Handle a one-time course purchase from checkout.session.completed.

    Creates a CourseAccess record. Does NOT change the user's tier.
    """
    from content.models import Course, CourseAccess

    customer_email = session_data.get("customer_details", {}).get("email", "")
    customer_id = session_data.get("customer", "")
    client_reference_id = session_data.get("client_reference_id")
    metadata = session_data.get("metadata", {})
    session_id = session_data.get("id", "")

    # Look up user
    user = None
    if client_reference_id:
        user = User.objects.filter(pk=client_reference_id).first()
    if user is None and metadata.get("user_id"):
        user = User.objects.filter(pk=metadata["user_id"]).first()
    if user is None and customer_email:
        user = User.objects.filter(email=customer_email).first()

    if user is None:
        logger.error(
            "course purchase: Could not find user. session_id=%s",
            session_id,
        )
        return

    # Look up course
    try:
        course = Course.objects.get(pk=course_id)
    except Course.DoesNotExist:
        logger.error(
            "course purchase: Course %s not found. session_id=%s",
            course_id,
            session_id,
        )
        return

    # Create CourseAccess (idempotent via get_or_create)
    CourseAccess.objects.get_or_create(
        user=user,
        course=course,
        defaults={
            "access_type": "purchased",
            "stripe_session_id": session_id,
        },
    )

    # Update stripe_customer_id if not already set
    if customer_id and not user.stripe_customer_id:
        user.stripe_customer_id = customer_id
        user.save(update_fields=["stripe_customer_id"])

    logger.info(
        "course purchase: user=%s course=%s (%s)",
        user.email,
        course.title,
        course.pk,
    )

    # Snapshot UTM attribution for the one-off course conversion.
    # tier=None / billing_period="" / mrr_eur=None — this is not an MRR
    # event. amount_eur comes from the course's individual_price_eur if
    # available. Wrapped so failure here doesn't block the CourseAccess
    # write above.
    try:
        course_amount_eur = None
        if course.individual_price_eur is not None:
            course_amount_eur = int(course.individual_price_eur)
        _record_conversion_attribution(
            user=user,
            session_data=session_data,
            tier=None,
            billing_period="",
            amount_eur=course_amount_eur,
        )
    except Exception:
        logger.exception(
            "Failed to record ConversionAttribution for course purchase "
            "user=%s session=%s",
            user.email,
            session_id,
        )


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


def record_processed_event(
    event_id,
    event_type,
    payload=None,
    status=WebhookEvent.STATUS_PROCESSED,
    error_message="",
):
    """Record that a webhook event has reached a terminal state.

    A ``WebhookEvent`` row means "do not run the handler for this event
    id again" — it represents either a clean handler run (``processed``)
    or a permanent, non-retryable failure (``failed_permanent``).
    Transient failures (generic ``Exception``) MUST NOT call this so
    Stripe's retry can re-run the handler.

    Idempotent via ``get_or_create``: if a concurrent retry beats us to
    the row, the existing row stays and we don't overwrite its status.
    """
    WebhookEvent.objects.get_or_create(
        stripe_event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": payload or {},
            "status": status,
            "error_message": error_message,
        },
    )


def _tier_from_subscription(subscription_id):
    """Look up the tier from a Stripe subscription's price ID."""
    try:
        client = _get_stripe_client()
        subscription = client.subscriptions.retrieve(subscription_id)
        price_id = _subscription_price_id(subscription)
        if price_id:
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
        return _subscription_period_end(subscription)
    except Exception:
        logger.exception(
            "Failed to get period end for subscription %s", subscription_id
        )
    return None


def _get_subscription_price_id(subscription_id):
    """Return the price ID of the first item on a Stripe subscription.

    Used by ``handle_checkout_completed`` to determine whether the user
    bought the monthly or yearly variant of a tier. Returns "" on any
    error so the caller can fall back to a blank ``billing_period``
    rather than crashing.
    """
    try:
        client = _get_stripe_client()
        subscription = client.subscriptions.retrieve(subscription_id)
        return _subscription_price_id(subscription)
    except Exception:
        logger.exception(
            "Failed to get price id for subscription %s", subscription_id
        )
    return ""


def _record_conversion_attribution(
    user, session_data, tier, billing_period, amount_eur=None
):
    """Snapshot a UserAttribution row into a frozen ConversionAttribution.

    Idempotent: if a row already exists for this stripe_session_id, return
    early without creating a duplicate. This is the second idempotency
    layer beyond ``WebhookEvent``.

    For users with no ``UserAttribution`` row (signed up before #194 or
    no UTMs ever captured), still create a row with all UTM fields blank
    so the conversion appears in dashboards as "no attribution".

    Args:
        user: The User who paid.
        session_data: The Stripe session payload dict.
        tier: The Tier purchased, or None for one-off course purchases.
        billing_period: "monthly", "yearly", or "" for one-off.
        amount_eur: Pre-computed amount for course purchases. If None and
            ``tier`` is set, derived from ``tier.price_eur_*`` based on
            ``billing_period``. ``mrr_eur`` is derived from ``amount_eur``
            and ``billing_period`` (yearly is divided by 12).
    """
    session_id = session_data.get("id", "")
    if not session_id:
        logger.warning(
            "_record_conversion_attribution: empty session_id, skipping "
            "(user=%s)",
            user.email,
        )
        return

    # Belt-and-braces idempotency on top of WebhookEvent.
    if ConversionAttribution.objects.filter(stripe_session_id=session_id).exists():
        return

    # Compute amount_eur and mrr_eur for subscription-mode conversions.
    # For one-off course purchases the caller passes amount_eur in directly
    # and tier is None, so this block is skipped.
    mrr_eur = None
    if tier is not None and amount_eur is None:
        if billing_period == "monthly":
            amount_eur = tier.price_eur_month
            mrr_eur = tier.price_eur_month
        elif billing_period == "yearly":
            amount_eur = tier.price_eur_year
            if tier.price_eur_year is not None:
                mrr_eur = tier.price_eur_year // 12

    # Look up the user's attribution snapshot. Absence is allowed — we
    # still write a row with blank UTM fields so the dashboard shows the
    # conversion under "no attribution" rather than dropping it.
    attribution = None
    try:
        attribution = user.attribution
    except Exception:
        # OneToOne RelatedObjectDoesNotExist or any other lookup failure.
        attribution = None

    if attribution is None:
        logger.info(
            "_record_conversion_attribution: no UserAttribution for user=%s, "
            "writing blank UTM snapshot (session=%s)",
            user.email,
            session_id,
        )

    fields = {
        "user": user,
        "stripe_session_id": session_id,
        "stripe_subscription_id": session_data.get("subscription") or "",
        "tier": tier,
        "billing_period": billing_period or "",
        "amount_eur": amount_eur,
        "mrr_eur": mrr_eur,
    }

    if attribution is not None:
        fields.update({
            "first_touch_utm_source": attribution.first_touch_utm_source,
            "first_touch_utm_medium": attribution.first_touch_utm_medium,
            "first_touch_utm_campaign": attribution.first_touch_utm_campaign,
            "first_touch_utm_content": attribution.first_touch_utm_content,
            "first_touch_utm_term": attribution.first_touch_utm_term,
            "first_touch_campaign": attribution.first_touch_campaign,
            "last_touch_utm_source": attribution.last_touch_utm_source,
            "last_touch_utm_medium": attribution.last_touch_utm_medium,
            "last_touch_utm_campaign": attribution.last_touch_utm_campaign,
            "last_touch_utm_content": attribution.last_touch_utm_content,
            "last_touch_utm_term": attribution.last_touch_utm_term,
            "last_touch_campaign": attribution.last_touch_campaign,
        })

    ConversionAttribution.objects.create(**fields)


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
