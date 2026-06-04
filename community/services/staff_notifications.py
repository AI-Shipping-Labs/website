"""Staff notifications for paid-signup automation (issue #703).

Replaces Valeriia's hand-written "welcome + intro" email and the implicit
internal heads-up with a single orchestrating helper:

- (A) Personalised co-founder welcome to the new paid user, with the
  configured staff mailbox put on CC.
- (B) Structured internal heads-up to staff via email and Slack.

The three sends are independent: a failure on one path NEVER blocks
the others and NEVER bubbles up out of ``notify_paid_signup``. The
caller (``payments/services/webhook_handlers.py::handle_checkout_completed``)
is the Stripe webhook handler — the user has already paid, so any
notification failure is a logging concern, not a payment concern.

Idempotency: ``notify_paid_signup`` is non-idempotent on its own. The
caller relies on the existing ``WebhookEvent`` short-circuit at the
dispatch layer (``payments/services/signatures.py``) — a replayed
``checkout.session.completed`` event ID never reaches this helper a
second time, so this module does not add a second guard.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import requests

from integrations.config import get_config, is_enabled, site_base_url

logger = logging.getLogger(__name__)


_DASH = "—"
_STRIPE_CUSTOMER_DASHBOARD_BASE = "https://dashboard.stripe.com/customers"
_SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


def notify_paid_signup(
    user,
    tier,
    previous_tier,
    was_new_user,
    stripe_customer_id,
    session_id,
    *,
    billing_period="",
):
    """Fire the welcome + staff heads-up for a paid checkout.

    Three independent best-effort sends:

    1. Co-founder welcome email to ``user.email``, CC'ing
       ``STAFF_SIGNUP_NOTIFY_EMAIL`` when set.
    2. Internal heads-up email to ``STAFF_SIGNUP_NOTIFY_EMAIL`` when set.
    3. Slack post to ``STAFF_SIGNUP_NOTIFY_CHANNEL_ID`` when set AND
       ``SLACK_ENABLED`` is true AND ``SLACK_BOT_TOKEN`` is configured.

    Each send is wrapped in its own broad ``try/except``. Failure on
    one path NEVER blocks the others and NEVER raises out of this
    function. This mirrors the broad-catch pattern in
    ``_record_conversion_attribution``.

    Args:
        user: ``User`` whose paid checkout completed.
        tier: ``Tier`` the user purchased. Caller has already gated on
            ``tier.level >= LEVEL_BASIC``.
        previous_tier: ``Tier`` the user held before this checkout
            (``None`` for genuinely new paid users).
        was_new_user: ``True`` iff the ``User`` row was created by this
            checkout.
        stripe_customer_id: ``cus_...`` for traceability + Stripe deep
            link.
        session_id: ``cs_...`` for traceability + match with
            ``WebhookEvent`` rows.
        billing_period: ``"monthly"`` / ``"yearly"`` / ``""`` (unknown).
            Used to format the EUR amount paid in the staff payloads.
    """
    staff_email = (get_config("STAFF_SIGNUP_NOTIFY_EMAIL", "") or "").strip()
    slack_channel_id = (get_config("STAFF_SIGNUP_NOTIFY_CHANNEL_ID", "") or "").strip()

    # Build the shared context once so the three sends agree on the
    # rendered values (tier label, amount, UTM, timestamp).
    ctx = _build_signup_context(
        user=user,
        tier=tier,
        previous_tier=previous_tier,
        was_new_user=was_new_user,
        stripe_customer_id=stripe_customer_id,
        session_id=session_id,
        billing_period=billing_period,
    )

    # (A) Co-founder welcome to the user. Independent try/except.
    try:
        _send_cofounder_welcome(user, tier, ctx, cc=staff_email or None)
    except Exception:
        # Broad catch by design: any failure inside EmailService /
        # template rendering / SES must not block the staff heads-up or
        # bubble out to the webhook caller. The user has already paid.
        logger.exception(
            "notify_paid_signup: failed to send co-founder welcome to %s "
            "(session=%s)",
            user.email,
            session_id,
        )

    # (B1) Internal staff email. Independent try/except.
    if staff_email:
        try:
            _send_staff_signup_notification(staff_email, ctx)
        except Exception:
            logger.exception(
                "notify_paid_signup: failed to send staff signup "
                "notification to %s (session=%s)",
                staff_email,
                session_id,
            )

    # (B2) Slack post. Independent try/except. Gated on settings.
    if slack_channel_id:
        try:
            _post_slack_signup_notification(slack_channel_id, ctx)
        except Exception:
            logger.exception(
                "notify_paid_signup: failed to post Slack signup "
                "notification to channel=%s (session=%s)",
                slack_channel_id,
                session_id,
            )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _build_signup_context(
    *,
    user,
    tier,
    previous_tier,
    was_new_user,
    stripe_customer_id,
    session_id,
    billing_period,
):
    """Materialise the shared context dict for all three sends."""
    tier_slug = getattr(tier, "slug", "") or ""
    tier_name = getattr(tier, "name", "") or tier_slug
    previous_tier_slug = (
        getattr(previous_tier, "slug", "") if previous_tier is not None else ""
    )

    # Amount: read off the tier matching the billing_period. price_eur_*
    # may be ``None`` on misconfigured tiers — render "unknown" in that
    # case so the founder sees the gap instead of a silent missing line.
    amount_label = _format_amount(tier, billing_period)

    # UTM: pull through the OneToOne attribution row. Very old users may
    # not have an attribution row at all (the signal landed after their
    # signup). The OneToOne reverse descriptor raises
    # ``RelatedObjectDoesNotExist`` instead of returning ``None``, so we
    # have to catch it explicitly — a bare ``getattr(user, "attribution",
    # None)`` would still raise from inside the descriptor.
    attribution = _safe_attribution(user)
    first_touch_utm_source = _or_dash(
        getattr(attribution, "first_touch_utm_source", "") if attribution else ""
    )
    first_touch_utm_campaign = _or_dash(
        getattr(attribution, "first_touch_utm_campaign", "") if attribution else ""
    )

    studio_user_url = f"{site_base_url()}/studio/users/{user.pk}/"
    stripe_customer_url = f"{_STRIPE_CUSTOMER_DASHBOARD_BASE}/{stripe_customer_id}"

    return {
        # User-facing
        "paid_user_email": user.email,
        "paid_user_first_name": _or_dash(user.first_name),
        "first_name_raw": user.first_name or "",
        # Tier
        "tier_slug": tier_slug or _DASH,
        "tier_name": tier_name or _DASH,
        "previous_tier_slug": _or_dash(previous_tier_slug),
        "billing_period": billing_period or _DASH,
        # Flags
        "was_new_user_label": "yes" if was_new_user else "no",
        # Money
        "amount_label": amount_label,
        # Stripe
        "stripe_customer_id": stripe_customer_id or _DASH,
        "stripe_customer_url": stripe_customer_url,
        "stripe_session_id": session_id or _DASH,
        # Attribution
        "first_touch_utm_source": first_touch_utm_source,
        "first_touch_utm_campaign": first_touch_utm_campaign,
        # Time
        "signup_timestamp": datetime.now(timezone.utc).isoformat(),
        # Studio link
        "studio_user_url": studio_user_url,
        # Sprint blurb for the welcome — computed lazily so the staff
        # paths don't pay for a DB lookup they don't use.
    }


def _welcome_template_for_tier(tier):
    """Pick the welcome template slug for the purchased tier.

    Routes on ``tier.level`` so a future renamed slug still routes
    correctly: level 10 -> Basic, level 20 -> Main, level 30 -> Premium.
    The caller has already gated on ``tier.level >= LEVEL_BASIC``, so
    levels other than 10/20/30 are not expected. For an unexpected paid
    level we fall back to the Main ``cofounder_welcome`` template and log
    a warning rather than silently dropping the welcome on a paid signup.
    """
    from content.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM

    level = getattr(tier, "level", None)
    if level == LEVEL_BASIC:
        return "basic_welcome"
    if level == LEVEL_MAIN:
        return "cofounder_welcome"
    if level == LEVEL_PREMIUM:
        return "premium_welcome"

    logger.warning(
        "notify_paid_signup: unexpected paid tier level %r (slug=%r) — "
        "falling back to the Main cofounder_welcome template",
        level,
        getattr(tier, "slug", ""),
    )
    return "cofounder_welcome"


def _send_cofounder_welcome(user, tier, ctx, *, cc):
    """Send (A) — the welcome to the new paid user.

    The template is selected by the purchased tier so each paid tier gets
    exactly its own email (Basic / Main / Premium). The CC-to-staff
    behaviour and the EmailLog write are unchanged — only the template
    selection varies.
    """
    from email_app.services import EmailService

    template_slug = _welcome_template_for_tier(tier)
    welcome_ctx = {
        "user_first_name": ctx["first_name_raw"],
        "current_sprint_status_paragraph": _current_sprint_paragraph(),
    }
    EmailService().send(user, template_slug, welcome_ctx, cc=cc)


def _send_staff_signup_notification(staff_email, ctx):
    """Send (B1) — the structured internal email to staff.

    Uses a ``SimpleNamespace`` recipient surrogate because
    ``EmailService.send`` needs a user-shaped object for personalisation
    defaults (``user.email``, ``user.first_name``, ``user.unsubscribed``,
    ``user.email_verified``). The staff mailbox is an internal pipe, not
    a real ``User`` row — we don't want to create a fake DB row, and the
    refactor of ``EmailService.send`` to drop the user requirement is
    out of scope for this issue.
    """
    from email_app.services import EmailService

    staff_recipient = SimpleNamespace(
        email=staff_email,
        first_name="",
        email_verified=True,
        unsubscribed=False,
        # ``pk`` is touched by the unsubscribe URL builder when sending
        # promotional mail. This template is transactional so that path
        # is unreachable, but the attribute being present keeps the
        # surrogate compatible with any future helper that reads it.
        pk=0,
    )
    EmailService().send(staff_recipient, "staff_signup_notification", ctx)


def _post_slack_signup_notification(channel_id, ctx):
    """Send (B2) — the mrkdwn Slack heads-up to the staff channel.

    Uses raw ``requests.post`` against ``chat.postMessage`` because
    ``SlackCommunityService`` is channel-membership focused (invite /
    kick), not posting. Mirrors the pattern in
    ``notifications/services/slack_announcements.py``.

    Silently skips when ``SLACK_ENABLED`` is false or ``SLACK_BOT_TOKEN``
    is empty — both are normal configurations on a dev machine.
    """
    if not is_enabled("SLACK_ENABLED"):
        logger.debug(
            "Skipping paid-signup Slack post: SLACK_ENABLED is not true"
        )
        return False

    bot_token = get_config("SLACK_BOT_TOKEN")
    if not bot_token:
        logger.info(
            "Skipping paid-signup Slack post: SLACK_BOT_TOKEN is not set"
        )
        return False

    text = _build_slack_text(ctx)

    try:
        response = requests.post(
            _SLACK_POST_MESSAGE_URL,
            json={"channel": channel_id, "text": text},
            headers={
                "Authorization": f"Bearer {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=10,
        )
    except requests.exceptions.RequestException:
        logger.exception(
            "Failed to POST paid-signup Slack notification to channel=%s",
            channel_id,
        )
        return False

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "Slack paid-signup notification returned non-JSON response "
            "for channel=%s",
            channel_id,
        )
        return False

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(
            "Slack paid-signup notification rejected for channel=%s: %s",
            channel_id,
            (data or {}).get("error", "unknown") if isinstance(data, dict) else "non-dict",
        )
        return False

    logger.info(
        "Posted paid-signup Slack notification: channel=%s session=%s",
        channel_id,
        ctx["stripe_session_id"],
    )
    return True


def _build_slack_text(ctx):
    """Compose the plain mrkdwn body for the staff Slack heads-up."""
    return (
        f"*New paid signup:* {ctx['paid_user_email']} → "
        f"{ctx['tier_name']} ({ctx['billing_period']})\n"
        f"*Name:* {ctx['paid_user_first_name']}\n"
        f"*Was new user:* {ctx['was_new_user_label']}\n"
        f"*Previous tier:* {ctx['previous_tier_slug']}\n"
        f"*Amount:* {ctx['amount_label']} | "
        f"<{ctx['stripe_customer_url']}|Stripe customer>\n"
        f"*UTM source / campaign:* {ctx['first_touch_utm_source']} / "
        f"{ctx['first_touch_utm_campaign']}\n"
        f"*Studio:* <{ctx['studio_user_url']}|user page>"
    )


def _format_amount(tier, billing_period):
    """Render the EUR amount paid in a single human-friendly token.

    Returns ``"€20 (monthly)"`` etc. when the tier has the matching
    price field set; ``"unknown"`` when it doesn't (misconfigured tier
    or unmapped billing period). Never raises.
    """
    if billing_period == "monthly":
        price = getattr(tier, "price_eur_month", None)
    elif billing_period == "yearly":
        price = getattr(tier, "price_eur_year", None)
    else:
        price = None
    if price is None:
        return "unknown"
    return f"€{price} ({billing_period})"


def _current_sprint_paragraph():
    """Resolve the sprint-status sentence for the welcome email.

    Returns the running-sprint sentence only when a sprint with
    ``status='active'`` has a computed ``end_date``
    (``start_date + duration_weeks``) that is today or in the future —
    i.e. the sprint is genuinely live/upcoming. A sprint marked
    ``active`` whose end date has already passed (a finished sprint
    whose status was never flipped) must NOT leak a "started ... ends"
    sentence into the welcome, so an empty string is returned instead.

    When no qualifying live/upcoming sprint exists, returns an empty
    string ``""`` — there is no generic fallback. An empty string
    renders as nothing in the template, leaving the sprint lead-in
    sentence to stand on its own.

    Imports ``Sprint`` lazily because ``community.services`` is imported
    at app startup by ``community/apps.py`` indirectly — keeping the
    model import inside the function avoids tight app-loading coupling.
    """
    from datetime import timedelta

    from django.utils import timezone

    try:
        from plans.models import Sprint

        today = timezone.localdate()
        sprint = (
            Sprint.objects.filter(status="active")
            .order_by("-start_date")
            .first()
        )
    except Exception:
        # Defensive: if the plans app or DB is unhealthy at the moment
        # of the welcome send, return an empty string rather than
        # blocking the user-facing email.
        logger.exception(
            "notify_paid_signup: failed to query active Sprint for welcome"
        )
        return ""

    if sprint is None:
        return ""

    end_date = sprint.start_date + timedelta(weeks=sprint.duration_weeks)
    if end_date < today:
        # The sprint is marked active but its real-world end date has
        # already passed — do not surface a finished sprint.
        return ""

    return (
        f"Sprint {sprint.name} is currently running — it started on "
        f"{sprint.start_date.isoformat()} and ends on "
        f"{end_date.isoformat()}."
    )


def _or_dash(value):
    """Return the dash placeholder for empty / falsy values."""
    if value is None:
        return _DASH
    s = str(value).strip()
    return s if s else _DASH


def _safe_attribution(user):
    """Return ``user.attribution`` or ``None`` when the row does not exist.

    Django's reverse OneToOne descriptor raises
    ``RelatedObjectDoesNotExist`` (a subclass of ``DoesNotExist``) when
    the row is absent. We catch that specifically so a missing
    attribution becomes a soft ``None`` instead of crashing the helper.
    """
    try:
        return user.attribution
    except Exception:  # noqa: BLE001 - matches the dynamic exception type
        return None
