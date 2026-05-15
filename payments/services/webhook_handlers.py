"""Stripe webhook event handlers.

Each ``handle_*`` here corresponds to a Stripe event type the platform
acts on:

- ``checkout.session.completed`` — fulfill a tier purchase or one-off
  course buy.
- ``customer.updated`` — sync the local user's email when the customer
  edits it in the Customer Portal.
- ``customer.subscription.updated`` — propagate tier changes and
  schedule community removals.
- ``customer.subscription.deleted`` — revert the user to ``free``.
- ``invoice.payment_failed`` — notify the user without revoking tier.

Cross-module calls (``_community_*``, ``_record_conversion_attribution``,
``_get_subscription_*``, ``_tier_from_subscription``) and module-level
``send_mail`` / ``get_config`` / ``logger`` references go through the
``payments.services`` package so the existing ``mock.patch
("payments.services.X")`` test surface keeps working unchanged.
"""

import json
from datetime import datetime, timezone
from smtplib import SMTPException

from django.core.mail import BadHeaderError

from accounts.models import User
from community.models import CommunityAuditLog
from payments import services as _services
from payments.exceptions import WebhookPermanentError
from payments.models import Tier


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
    session_id = session_data.get("id", "")

    # Check if this is an individual course purchase
    course_id = metadata.get("course_id")
    if course_id:
        _handle_course_purchase(session_data, course_id)
        return

    # Look up user: first by client_reference_id (user PK), then by email
    user = None
    was_new_user = False
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
        was_new_user = True

    if user is None:
        _services.logger.error(
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
        tier = _services._tier_from_subscription(subscription_id)

    if tier is None:
        _services.logger.error(
            "checkout.session.completed: Could not determine tier. "
            "tier_slug=%s, session_id=%s",
            tier_slug,
            session_data.get("id"),
        )
        return

    # Remember the previous tier so we can tell upgrade-vs-new-paid-user
    # apart when building the operator notification email below.
    previous_tier = user.tier

    # Update user fields
    user.tier = tier
    user.stripe_customer_id = customer_id or user.stripe_customer_id
    user.subscription_id = subscription_id or user.subscription_id
    user.pending_tier = None  # Clear any pending downgrade

    # Set billing_period_end from subscription if available
    if subscription_id:
        billing_end = _services._get_subscription_period_end(subscription_id)
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
    _services.logger.info(
        "checkout.session.completed: user=%s tier=%s", user.email, tier.slug,
    )

    # Determine billing period from the Stripe subscription's price ID.
    billing_period = ""
    if subscription_id:
        sub_price_id = _services._get_subscription_price_id(subscription_id)
        if sub_price_id and sub_price_id == tier.stripe_price_id_yearly:
            billing_period = "yearly"
        elif sub_price_id and sub_price_id == tier.stripe_price_id_monthly:
            billing_period = "monthly"

    # Snapshot UTM attribution for this conversion. Wrapped so an
    # attribution failure NEVER breaks the tier/customer/subscription
    # update above — the user's payment is the source of truth; missing
    # attribution is a warning, not an error.
    try:
        _services._record_conversion_attribution(
            user=user,
            session_data=session_data,
            tier=tier,
            billing_period=billing_period,
            amount_eur=None,  # derived inside the helper from tier + billing_period
        )
    except Exception:
        # Intentional broad catch: an attribution write must NEVER undo
        # the tier/customer/subscription update above — the user's payment
        # is the source of truth. Any failure (DB integrity, missing
        # UserAttribution, dashboard bookkeeping bug) becomes a logged
        # warning, not a propagated error.
        _services.logger.exception(
            "Failed to record ConversionAttribution for user=%s session=%s",
            user.email,
            session_data.get("id"),
        )

    # Community integration: invite user if tier qualifies (Main+ = level >= 20)
    if tier.level >= 20:
        _services._community_invite(user)

    # Best-effort operator notification. Runs after every successful
    # tier change so the configured recipient (issue #645) sees who
    # joined and whether they are new or upgrading. Failures here MUST
    # NOT break the webhook — the user has already paid.
    _send_payment_notification_email(
        event_id=session_id,
        user=user,
        was_new_user=was_new_user,
        tier=tier,
        previous_tier=previous_tier,
        course=None,
        stripe_customer_id=customer_id,
    )


def _send_payment_notification_email(
    event_id,
    user,
    was_new_user,
    tier,
    previous_tier,
    course,
    stripe_customer_id,
):
    """Notify the configured operator that a checkout has completed.

    Best-effort: when ``PAYMENT_NOTIFICATION_EMAIL`` is unset or empty
    we no-op silently. When the value IS set, we send a single plain
    text mail via :func:`django.core.mail.send_mail`. The handler MUST
    survive transport errors — the user has already paid, so a missing
    notification is a logging concern, not a payment concern. We catch
    a broad ``Exception`` here for that reason (mirrors the Slack-invite
    email pattern in ``community/services/slack.py``).

    Idempotency is handled at the webhook-dispatch layer by the
    ``WebhookEvent`` row; this helper does not add a second guard.

    Args:
        event_id: Stripe checkout session id (``cs_...``). Acts as the
            traceability handle on the receiving side — it is the same
            value stored in ``WebhookEvent.payload['data']['object']['id']``.
        user: The ``User`` whose checkout completed.
        was_new_user: ``True`` iff the user was created by this checkout.
        tier: The ``Tier`` purchased, or ``None`` for course purchases.
        previous_tier: The ``Tier`` the user held before this checkout,
            or ``None`` for new users / course purchases.
        course: The ``Course`` purchased, or ``None`` for tier checkouts.
        stripe_customer_id: ``cus_...`` for traceability.
    """
    recipient = _services.get_config("PAYMENT_NOTIFICATION_EMAIL", "")
    if not recipient:
        return

    timestamp = datetime.now(timezone.utc).isoformat()

    if course is not None:
        subject = f"[AISL] Course purchase: {user.email}"
        body_lines = [
            f"User email: {user.email}",
            f"New user: {'yes' if was_new_user else 'no'}",
            f"Course slug: {course.slug}",
            f"Course title: {course.title}",
            f"Stripe customer id: {stripe_customer_id}",
            f"Stripe session id: {event_id}",
            f"Timestamp (UTC): {timestamp}",
        ]
    else:
        tier_slug = tier.slug if tier is not None else ""
        tier_label = getattr(tier, "name", "") or getattr(tier, "label", "") or tier_slug
        if was_new_user:
            subject = f"[AISL] New paid signup: {user.email}"
        else:
            subject = f"[AISL] Tier upgrade: {user.email} -> {tier_slug}"
        body_lines = [
            f"User email: {user.email}",
            f"New user: {'yes' if was_new_user else 'no'}",
            f"Tier slug: {tier_slug}",
            f"Tier label: {tier_label}",
        ]
        if previous_tier is not None:
            body_lines.append(f"Previous tier: {previous_tier.slug}")
        body_lines.extend([
            f"Stripe customer id: {stripe_customer_id}",
            f"Stripe session id: {event_id}",
            f"Timestamp (UTC): {timestamp}",
        ])

    message = "\n".join(body_lines)

    try:
        _services.send_mail(
            subject=subject,
            message=message,
            from_email=None,  # Uses DEFAULT_FROM_EMAIL
            recipient_list=[recipient],
            fail_silently=False,
        )
    except (BadHeaderError, OSError, SMTPException):
        # Mirrors the narrowed catch in ``handle_invoice_payment_failed``
        # below. ``SMTPException`` covers SMTP-protocol failures,
        # ``OSError`` covers connection-level failures (DNS, broken
        # socket), and ``BadHeaderError`` covers Django's defense
        # against header-injection in the subject. A misconfigured
        # backend (``ImproperlyConfigured``) or template bug should
        # surface, not be swallowed.
        _services.logger.warning(
            "Failed to send payment notification email to %s for user=%s "
            "session=%s",
            recipient,
            user.email,
            event_id,
            exc_info=True,
        )


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
    was_new_user = False
    if client_reference_id:
        user = User.objects.filter(pk=client_reference_id).first()
    if user is None and metadata.get("user_id"):
        user = User.objects.filter(pk=metadata["user_id"]).first()
    if user is None and customer_email:
        user = User.objects.filter(email=customer_email).first()

    if user is None:
        _services.logger.error(
            "course purchase: Could not find user. session_id=%s",
            session_id,
        )
        return

    # Look up course
    try:
        course = Course.objects.get(pk=course_id)
    except Course.DoesNotExist:
        _services.logger.error(
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

    _services.logger.info(
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
        _services._record_conversion_attribution(
            user=user,
            session_data=session_data,
            tier=None,
            billing_period="",
            amount_eur=course_amount_eur,
        )
    except Exception:
        # Intentional broad catch: the ``CourseAccess`` write above is
        # the source of truth for the user's purchase; attribution is
        # bookkeeping for the conversion dashboard. Any failure here
        # (DB integrity, decimal -> int rounding, etc.) must not
        # propagate, otherwise Stripe will retry the webhook and the
        # idempotent ``get_or_create`` above will keep firing.
        _services.logger.exception(
            "Failed to record ConversionAttribution for course purchase "
            "user=%s session=%s",
            user.email,
            session_id,
        )

    # Best-effort operator notification for the course purchase (issue
    # #645). Tier is unchanged for a one-off course buy.
    _send_payment_notification_email(
        event_id=session_id,
        user=user,
        was_new_user=was_new_user,
        tier=None,
        previous_tier=None,
        course=course,
        stripe_customer_id=customer_id,
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
        _services.logger.error(
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
        _services.logger.info(
            "customer.subscription.updated: cancel_at_period_end for user=%s",
            user.email,
        )
        # Schedule community removal at billing period end
        if user.tier and user.tier.level >= 20 and user.billing_period_end:
            _services._community_schedule_removal(user)
        return

    # Look up the new tier from price_id
    old_tier_level = user.tier.level if user.tier else 0
    if price_id:
        new_tier = _services._tier_for_price_id(price_id)
        if new_tier and new_tier != user.tier:
            # Check if this is an active subscription update
            if status == "active":
                user.tier = new_tier
                user.pending_tier = None
                _services.logger.info(
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
        _services._community_reactivate(user)
    elif new_tier_level < 20 and old_tier_level >= 20:
        # Immediate downgrade below Main: remove from community now
        _services._community_remove(user)


def handle_customer_updated(customer_data):
    """Process a ``customer.updated`` event by syncing the email only.

    When a user edits their billing email in the Stripe Customer Portal,
    Stripe fires ``customer.updated`` and the new email arrives in
    ``customer_data["email"]``. Without this handler, the local
    ``User.email`` drifts away from the email Stripe is sending receipts
    to, which breaks reconciliation, password resets, and audit trails.

    Scope is intentionally narrow: EMAIL ONLY. ``customer.updated`` can
    also carry ``name``, ``metadata``, ``phone``, etc., but the local
    ``User`` model has no name field today and we do not want this
    handler to silently take ownership of unrelated profile state.

    Behavior:

    - Look up the user by ``stripe_customer_id == customer_data["id"]``.
      If no match, log INFO and return cleanly (do NOT raise). This
      mirrors how ``handle_subscription_updated`` treats unknown
      customers: returning cleanly skips the ``WebhookEvent`` row so a
      future local signup with the same Stripe customer can still pick
      the event up via Stripe replay.
    - If ``customer_data["email"]`` is missing, empty, or already
      matches ``user.email`` (case-insensitively, after normalize),
      no-op. No audit log row.
    - If the new email is taken by a different local user, raise
      ``WebhookPermanentError``. A unique-collision is not retriable;
      the dispatcher records a ``failed_permanent`` row so on-call has
      a trace and Stripe stops retrying.
    - Otherwise, normalize the new email, save it on ``update_fields``,
      and write a ``CommunityAuditLog`` row with the old and new email
      values for traceability.
    """
    customer_id = customer_data.get("id", "") or ""
    new_email_raw = customer_data.get("email", "") or ""

    if not customer_id:
        _services.logger.info("customer.updated: missing customer id, ignoring")
        return

    user = User.objects.filter(stripe_customer_id=customer_id).first()
    if user is None:
        # Stripe may carry customers that pre-date the local account,
        # or test-mode customers that never had a local user. Returning
        # cleanly without recording a WebhookEvent row matches the
        # existing handle_subscription_updated behavior and keeps the
        # door open for a later replay once the local user exists.
        _services.logger.info(
            "customer.updated: no local user for stripe_customer_id=%s",
            customer_id,
        )
        return

    if not new_email_raw:
        # Empty / missing email is a no-op. Stripe sends the full
        # customer object on every customer.updated event, including
        # cases where only metadata or name changed.
        return

    new_email = User.objects.normalize_email(new_email_raw)
    if not new_email:
        return

    # Case-insensitive compare against the local email to stay idempotent
    # against pure-case edits (e.g. Stripe normalized the casing).
    if user.email.lower() == new_email.lower():
        return

    # Unique-collision: another local user already owns this email.
    # Raise WebhookPermanentError so the dispatcher records a terminal
    # failed_permanent row — Stripe stops retrying, on-call can dig in.
    collision = (
        User.objects
        .filter(email__iexact=new_email)
        .exclude(pk=user.pk)
        .first()
    )
    if collision is not None:
        CommunityAuditLog.objects.create(
            user=user,
            action="email_synced_from_stripe",
            details=json.dumps({
                "status": "failed",
                "reason": "email_collision",
                "old_email": user.email,
                "new_email": new_email,
                "colliding_user_id": collision.pk,
            }),
        )
        raise WebhookPermanentError(
            f"customer.updated: email collision for stripe_customer_id="
            f"{customer_id}; another local user already owns {new_email}"
        )

    old_email = user.email
    user.email = new_email
    user.save(update_fields=["email"])

    CommunityAuditLog.objects.create(
        user=user,
        action="email_synced_from_stripe",
        details=json.dumps({
            "status": "ok",
            "reason": "customer_updated",
            "old_email": old_email,
            "new_email": new_email,
        }),
    )
    _services.logger.info(
        "customer.updated: synced email user=%s old=%s new=%s",
        user.pk, old_email, new_email,
    )


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
        _services.logger.error(
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
    _services.logger.info(
        "customer.subscription.deleted: user=%s reverted to free", user.email,
    )

    # Community integration: remove if they had community access
    if had_community:
        _services._community_remove(user)


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
        _services.logger.error(
            "invoice.payment_failed: Could not find user. customer_id=%s",
            customer_id,
        )
        return

    portal_url = _services.get_config("STRIPE_CUSTOMER_PORTAL_URL", "")

    try:
        _services.send_mail(
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
            fail_silently=False,
        )
    except (BadHeaderError, OSError, SMTPException):
        _services.logger.exception(
            "invoice.payment_failed: Failed to send email to user=%s",
            user.email,
        )

    _services.logger.info(
        "invoice.payment_failed: notified user=%s (tier NOT revoked)",
        user.email,
    )
