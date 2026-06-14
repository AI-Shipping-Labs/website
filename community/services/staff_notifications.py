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
_STRIPE_DASHBOARD_BASE = "https://dashboard.stripe.com"
_SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

# Final fallback when neither the live charged amount nor a tier price is
# available. NEVER render "unknown" — the founder must know the gap is a
# missing-data state, not a literal zero/unknown amount.
_AMOUNT_PENDING = "Amount pending — see Stripe"

# How many recent activity rows to inline in the staff heads-up email.
_ACTIVITY_LIMIT = 5


def notify_paid_signup(
    user,
    tier,
    previous_tier,
    was_new_user,
    stripe_customer_id,
    session_id,
    *,
    billing_period="",
    amount_total_minor=None,
    currency="",
    payment_intent_id="",
    subscription_id="",
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
            Rendered as the plan interval (Monthly / Yearly) in the staff
            payloads.
        amount_total_minor: The Checkout Session ``amount_total`` in minor
            units (e.g. cents). Already loaded by the webhook handler — no
            new Stripe round-trip. ``None`` when the handler did not carry
            it (older event shapes); the formatter then falls back to the
            tier price and finally to ``_AMOUNT_PENDING``.
        currency: The Checkout Session ``currency`` (ISO code, lowercase),
            paired with ``amount_total_minor`` to render the real charge.
        payment_intent_id: ``pi_...`` from ``session_data.payment_intent``;
            used to build the payment dashboard deep-link.
        subscription_id: ``sub_...`` for the subscription dashboard link
            and the optional interval fallback lookup.
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
        amount_total_minor=amount_total_minor,
        currency=currency,
        payment_intent_id=payment_intent_id,
        subscription_id=subscription_id,
    )

    # (A) Co-founder welcome to the user. Independent try/except.
    try:
        _send_cofounder_welcome(user, tier, ctx, bcc=staff_email or None)
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


def notify_maven_cohort_removal(user, cohort, course="", *, email=None):
    """Send a staff heads-up that a Maven cohort removal arrived (issue #960).

    Mirrors ``notify_paid_signup``'s staff paths (recipient =
    ``STAFF_SIGNUP_NOTIFY_EMAIL`` plus the optional Slack staff channel) but
    makes NO change to the user's access — a cohort removal is not a decision
    to revoke community access. The notification suggests, never commands.

    Best-effort: both sends are wrapped so a mail/Slack failure never raises
    out of this function (the webhook must not 500 on a notification failure).

    Args:
        user: the resolved ``User``, or ``None`` when the email did not
            resolve to any account (a lighter "unknown user" note is sent).
        cohort: the cohort the user was removed from (text).
        course: the course name, if present (text).
        email: the raw email from the payload — used in the "unknown user"
            note when ``user`` is ``None``.
    """
    staff_email = (get_config("STAFF_SIGNUP_NOTIFY_EMAIL", "") or "").strip()
    slack_channel_id = (get_config("STAFF_SIGNUP_NOTIFY_CHANNEL_ID", "") or "").strip()

    ctx = _build_removal_context(user, cohort, course, email)

    if staff_email:
        try:
            _send_staff_removal_notification(staff_email, ctx)
        except Exception:
            logger.exception(
                "notify_maven_cohort_removal: failed to send staff email to %s "
                "(cohort=%s)",
                staff_email,
                cohort,
            )

    if slack_channel_id:
        try:
            _post_slack_removal_notification(slack_channel_id, ctx)
        except Exception:
            logger.exception(
                "notify_maven_cohort_removal: failed to post Slack note to "
                "channel=%s (cohort=%s)",
                slack_channel_id,
                cohort,
            )


def _build_removal_context(user, cohort, course, email):
    """Materialise the shared context for the removal staff notification."""
    if user is not None:
        display_name = (
            f"{user.first_name} {user.last_name}".strip()
            or user.first_name
            or user.email
        )
        return {
            "user_known": True,
            "removed_user_email": user.email,
            "removed_user_name": _or_dash(display_name),
            "removed_user_id": str(user.pk),
            "studio_user_url": f"{site_base_url()}/studio/users/{user.pk}/",
            "cohort": _or_dash(cohort),
            "course": _or_dash(course),
        }
    return {
        "user_known": False,
        "removed_user_email": _or_dash(email),
        "removed_user_name": _DASH,
        "removed_user_id": _DASH,
        "studio_user_url": "",
        "cohort": _or_dash(cohort),
        "course": _or_dash(course),
    }


def _send_staff_removal_notification(staff_email, ctx):
    """Send the structured internal removal email to staff."""
    from email_app.services import EmailService

    staff_recipient = SimpleNamespace(
        email=staff_email,
        first_name="",
        email_verified=True,
        unsubscribed=False,
        pk=0,
    )
    EmailService().send(staff_recipient, "maven_cohort_removal_notification", ctx)


def _post_slack_removal_notification(channel_id, ctx):
    """Post the mrkdwn removal heads-up to the staff Slack channel."""
    if not is_enabled("SLACK_ENABLED"):
        logger.debug("Skipping Maven removal Slack post: SLACK_ENABLED is not true")
        return False

    bot_token = get_config("SLACK_BOT_TOKEN")
    if not bot_token:
        logger.info("Skipping Maven removal Slack post: SLACK_BOT_TOKEN is not set")
        return False

    text = _build_removal_slack_text(ctx)
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
            "Failed to POST Maven removal Slack notification to channel=%s",
            channel_id,
        )
        return False

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "Maven removal Slack notification returned non-JSON for channel=%s",
            channel_id,
        )
        return False

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(
            "Maven removal Slack notification rejected for channel=%s: %s",
            channel_id,
            (data or {}).get("error", "unknown") if isinstance(data, dict) else "non-dict",
        )
        return False
    return True


def _build_removal_slack_text(ctx):
    """Compose the plain mrkdwn body for the removal staff heads-up."""
    cohort_line = ctx["cohort"]
    if ctx["course"] and ctx["course"] != _DASH:
        cohort_line = f"{ctx['cohort']} ({ctx['course']})"
    if ctx["user_known"]:
        return (
            f"*Maven cohort removal:* {ctx['removed_user_name']} "
            f"({ctx['removed_user_email']}) was removed from cohort "
            f"`{cohort_line}`.\n"
            f"*User ID:* {ctx['removed_user_id']} | "
            f"*Studio:* <{ctx['studio_user_url']}|user page>\n"
            "You may want to suspend their tier override / subscription. "
            "Their access is unchanged until you act."
        )
    return (
        f"*Maven cohort removal:* unknown user `{ctx['removed_user_email']}` "
        f"was removed from cohort `{cohort_line}` — no matching account. "
        "No action taken."
    )


# ---------------------------------------------------------------------
# Slack-join staff heads-up (issue #959)
# ---------------------------------------------------------------------

def notify_slack_join(user):
    """Fire a best-effort staff heads-up when a known user joins Slack.

    Called from ``community/tasks/slack_membership.py::refresh_slack_membership``
    only on a GENUINE forward transition (``previous_member`` False ->
    member, with ``slack_checked_at`` already set on a prior cycle). The
    caller wraps this in its own try/except, but every send here is ALSO
    wrapped so one failing path never blocks the other and nothing bubbles
    out to the membership refresh loop.

    Behaviour:

    - Gated by ``is_enabled('STAFF_SLACK_JOIN_NOTIFY_ENABLED')``. When the
      toggle is off, return immediately (no email, no Slack post).
    - Email recipient: reuse ``STAFF_SIGNUP_NOTIFY_EMAIL`` (the same staff
      mailbox the paid-signup heads-up uses). When blank, skip the email.
    - Slack post: reuse ``STAFF_SIGNUP_NOTIFY_CHANNEL_ID``, gated on
      ``SLACK_ENABLED`` AND a non-empty ``SLACK_BOT_TOKEN`` (same gating as
      ``_post_slack_signup_notification``). When the channel id is blank or
      Slack is disabled, skip the Slack post; the email side still runs.
    """
    if not is_enabled("STAFF_SLACK_JOIN_NOTIFY_ENABLED"):
        logger.debug(
            "notify_slack_join: STAFF_SLACK_JOIN_NOTIFY_ENABLED is off; "
            "skipping for user=%s",
            getattr(user, "pk", None),
        )
        return

    staff_email = (get_config("STAFF_SIGNUP_NOTIFY_EMAIL", "") or "").strip()
    slack_channel_id = (get_config("STAFF_SIGNUP_NOTIFY_CHANNEL_ID", "") or "").strip()

    ctx = _build_slack_join_context(user)

    # (1) Internal staff email. Independent try/except.
    if staff_email:
        try:
            _send_slack_join_notification(staff_email, ctx)
        except Exception:
            logger.exception(
                "notify_slack_join: failed to send staff join "
                "notification to %s (user=%s)",
                staff_email,
                getattr(user, "pk", None),
            )

    # (2) Slack post. Independent try/except. Gated on settings.
    if slack_channel_id:
        try:
            _post_slack_join_notification(slack_channel_id, ctx)
        except Exception:
            logger.exception(
                "notify_slack_join: failed to post Slack join "
                "notification to channel=%s (user=%s)",
                slack_channel_id,
                getattr(user, "pk", None),
            )


def _build_slack_join_context(user):
    """Materialise the lightweight context for the Slack-join heads-up.

    Keeps to the essentials the spec calls for: who the user is (full
    name, email, id), how we know them (tier name + first-touch UTM
    source), and the absolute Studio profile link. No Stripe block.
    """
    tier = getattr(user, "tier", None)
    tier_name = (getattr(tier, "name", "") or getattr(tier, "slug", "")) if tier else ""

    attribution = _safe_attribution(user)
    signup_source = _or_dash(
        getattr(attribution, "first_touch_utm_source", "") if attribution else ""
    )

    full_name = " ".join(
        part for part in [
            (getattr(user, "first_name", "") or "").strip(),
            (getattr(user, "last_name", "") or "").strip(),
        ] if part
    ).strip()

    return {
        "user_email": user.email,
        "user_full_name": _or_dash(full_name),
        "user_first_name": _or_dash(getattr(user, "first_name", "")),
        "user_id": user.pk,
        "tier_name": _or_dash(tier_name),
        "signup_source": signup_source,
        "studio_user_url": f"{site_base_url()}/studio/users/{user.pk}/",
    }


def _send_slack_join_notification(staff_email, ctx):
    """Send the structured internal Slack-join email to staff.

    Uses the same ``SimpleNamespace`` staff-recipient surrogate as
    ``_send_staff_signup_notification`` (the staff mailbox is an internal
    pipe, not a real ``User`` row).
    """
    from email_app.services import EmailService

    staff_recipient = SimpleNamespace(
        email=staff_email,
        first_name="",
        email_verified=True,
        unsubscribed=False,
        pk=0,
    )
    EmailService().send(staff_recipient, "slack_join_notification", ctx)


def _post_slack_join_notification(channel_id, ctx):
    """Post the mrkdwn Slack-join heads-up to the staff channel.

    Mirrors ``_post_slack_signup_notification``: silently skips when
    ``SLACK_ENABLED`` is false or ``SLACK_BOT_TOKEN`` is empty.
    """
    if not is_enabled("SLACK_ENABLED"):
        logger.debug(
            "Skipping Slack-join Slack post: SLACK_ENABLED is not true"
        )
        return False

    bot_token = get_config("SLACK_BOT_TOKEN")
    if not bot_token:
        logger.info(
            "Skipping Slack-join Slack post: SLACK_BOT_TOKEN is not set"
        )
        return False

    text = _build_slack_join_text(ctx)

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
            "Failed to POST Slack-join notification to channel=%s",
            channel_id,
        )
        return False

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "Slack-join notification returned non-JSON response "
            "for channel=%s",
            channel_id,
        )
        return False

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(
            "Slack-join notification rejected for channel=%s: %s",
            channel_id,
            (data or {}).get("error", "unknown") if isinstance(data, dict) else "non-dict",
        )
        return False

    logger.info(
        "Posted Slack-join notification: channel=%s user=%s",
        channel_id,
        ctx["user_id"],
    )
    return True


def _build_slack_join_text(ctx):
    """Compose the plain mrkdwn body for the staff Slack-join heads-up."""
    return (
        f"*New Slack member — say hi:* {ctx['user_email']}\n"
        f"*Name:* {ctx['user_full_name']}\n"
        f"*Tier:* {ctx['tier_name']}\n"
        f"*Signup source:* {ctx['signup_source']}\n"
        f"*Studio:* <{ctx['studio_user_url']}|user page>"
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
    amount_total_minor=None,
    currency="",
    payment_intent_id="",
    subscription_id="",
):
    """Materialise the shared context dict for all three sends."""
    tier_slug = getattr(tier, "slug", "") or ""
    tier_name = getattr(tier, "name", "") or tier_slug
    previous_tier_slug = (
        getattr(previous_tier, "slug", "") if previous_tier is not None else ""
    )

    # Amount: prefer the REAL charged value the webhook already loaded
    # (``amount_total`` + ``currency`` from the Checkout Session). Fall
    # back to the tier price for the billing period, then to a literal
    # "Amount pending — see Stripe". Never "unknown".
    amount_label = _format_charged_amount(
        amount_total_minor, currency, tier, billing_period,
    )

    # Interval (Monthly / Yearly) derived from the billing_period the
    # webhook already computed — the COMMON path makes no Stripe call.
    # Only when billing_period is empty AND a subscription id is present do
    # we attempt the OPTIONAL, fully-wrapped interval lookup; a slow or
    # failing Stripe call there returns "" and the line is simply omitted.
    interval_label = _format_interval(billing_period)
    if not interval_label and subscription_id:
        interval_label = _format_interval(
            _safe_subscription_interval(subscription_id)
        )

    # Inline pre-upgrade activity (issue #853 query shape): newest-first
    # window of the user's recorded activity so the founder sees what the
    # new member did before paying, without leaving the email.
    activity_lines = _recent_activity_lines(user)

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

    # Account-scoped Stripe dashboard deep-links (issue #952). The account
    # id encodes test/live mode; when blank every id renders as plain
    # copyable text (matches the Studio user-page behaviour). The previous
    # customer URL omitted ``<acct>`` entirely — fixed here.
    account_id = (get_config("STRIPE_DASHBOARD_ACCOUNT_ID", "") or "").strip()
    stripe_customer_url = _dashboard_url(account_id, "customers", stripe_customer_id)
    stripe_payment_url = _dashboard_url(account_id, "payments", payment_intent_id)
    stripe_subscription_url = _dashboard_url(
        account_id, "subscriptions", subscription_id,
    )

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
        "interval_label": interval_label,
        # Flags
        "was_new_user_label": "yes" if was_new_user else "no",
        # Money
        "amount_label": amount_label,
        # Stripe ids + (optional) dashboard deep-links. When the account id
        # is blank the ``*_url`` value is "" and the template renders the id
        # as plain text.
        "stripe_customer_id": stripe_customer_id or _DASH,
        "stripe_customer_url": stripe_customer_url,
        "stripe_payment_intent_id": payment_intent_id or _DASH,
        "stripe_payment_url": stripe_payment_url,
        "stripe_subscription_id": subscription_id or _DASH,
        "stripe_subscription_url": stripe_subscription_url,
        "stripe_session_id": session_id or _DASH,
        # Inline pre-upgrade activity (newest-first, last 5).
        "recent_activity_lines": activity_lines,
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


def _send_cofounder_welcome(user, tier, ctx, *, bcc):
    """Send (A) — the welcome to the new paid user.

    The template is selected by the purchased tier so each paid tier gets
    exactly its own email (Basic / Main / Premium). Issue #950: the staff
    mailbox rides on BCC (not CC) so the new member never sees the
    internal address and can't Reply-All to it; the EmailLog write is
    unchanged.
    """
    from email_app.services import EmailService

    template_slug = _welcome_template_for_tier(tier)
    welcome_ctx = {
        "user_first_name": ctx["first_name_raw"],
        "current_sprint_status_paragraph": _current_sprint_paragraph(),
    }
    EmailService().send(user, template_slug, welcome_ctx, bcc=bcc)


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
    customer_ref = _slack_link(ctx["stripe_customer_url"], "Stripe customer")
    interval = ctx["interval_label"]
    tier_line = (
        f"{ctx['tier_name']} ({interval})" if interval else ctx["tier_name"]
    )
    return (
        f"*New paid signup:* {ctx['paid_user_email']} → "
        f"{tier_line}\n"
        f"*Name:* {ctx['paid_user_first_name']}\n"
        f"*Was new user:* {ctx['was_new_user_label']}\n"
        f"*Previous tier:* {ctx['previous_tier_slug']}\n"
        f"*Amount:* {ctx['amount_label']} | {customer_ref}\n"
        f"*UTM source / campaign:* {ctx['first_touch_utm_source']} / "
        f"{ctx['first_touch_utm_campaign']}\n"
        f"*Studio:* <{ctx['studio_user_url']}|user page>"
    )


def _slack_link(url, label):
    """Render a Slack mrkdwn link, or just the label when the URL is blank.

    Keeps the Slack body tidy when ``STRIPE_DASHBOARD_ACCOUNT_ID`` is
    unset and there is no clickable dashboard target.
    """
    if url:
        return f"<{url}|{label}>"
    return label


_CURRENCY_SYMBOLS = {
    "eur": "€",
    "usd": "$",
    "gbp": "£",
}


def _format_charged_amount(amount_total_minor, currency, tier, billing_period):
    """Render the amount the member was actually charged.

    Resolution order:

    1. The REAL charged value — ``amount_total`` (minor units) + the
       session ``currency`` the webhook already loaded. Rendered as e.g.
       ``"€20.00"`` (or ``"USD 20.00"`` when the symbol is unknown).
    2. The tier price for the billing period (fallback for old event
       shapes that did not carry ``amount_total``).
    3. The literal ``"Amount pending — see Stripe"`` — NEVER ``"unknown"``.

    Never raises.
    """
    real = _format_minor_amount(amount_total_minor, currency)
    if real:
        return real

    tier_fallback = _format_amount(tier, billing_period)
    if tier_fallback:
        return tier_fallback

    return _AMOUNT_PENDING


def _format_minor_amount(amount_total_minor, currency):
    """Format ``amount_total`` (minor units) + currency, or "" when absent.

    Returns ``""`` (not a placeholder) so the caller can decide on the
    fallback chain. Zero-decimal currencies are not special-cased — the
    overwhelming majority of our charges are EUR; a missing amount simply
    yields "".
    """
    if amount_total_minor is None:
        return ""
    try:
        major = int(amount_total_minor) / 100
    except (TypeError, ValueError):
        return ""

    code = (currency or "").strip().lower()
    symbol = _CURRENCY_SYMBOLS.get(code)
    if symbol:
        return f"{symbol}{major:.2f}"
    if code:
        return f"{code.upper()} {major:.2f}"
    # No currency at all — render the bare amount rather than guessing.
    return f"{major:.2f}"


def _format_amount(tier, billing_period):
    """Tier-price FALLBACK token, or "" when the tier price is missing.

    Returns ``"€20 (monthly)"`` etc. when the tier has the matching price
    field set; ``""`` (empty — NOT a placeholder) when it doesn't, so the
    caller falls through to ``_AMOUNT_PENDING``. Never raises.
    """
    if billing_period == "monthly":
        price = getattr(tier, "price_eur_month", None)
    elif billing_period == "yearly":
        price = getattr(tier, "price_eur_year", None)
    else:
        price = None
    if price is None:
        return ""
    return f"€{price} ({billing_period})"


def _format_interval(billing_period):
    """Render the plan interval as ``"Monthly"`` / ``"Yearly"`` / ``""``.

    Accepts both the webhook ``billing_period`` tokens (``"monthly"`` /
    ``"yearly"``) and Stripe's raw ``recurring.interval`` tokens
    (``"month"`` / ``"year"``). One-time purchases carry no period, so an
    empty / unknown value yields ``""`` and the template OMITS the
    interval line entirely (rather than showing a blank or dash).
    """
    if billing_period in ("monthly", "month"):
        return "Monthly"
    if billing_period in ("yearly", "year"):
        return "Yearly"
    return ""


def _safe_subscription_interval(subscription_id):
    """Optional, fully-wrapped Stripe interval lookup.

    Returns ``"month"`` / ``"year"`` / ``""``. NEVER raises — a slow or
    failing Stripe call yields ``""`` so the staff email still sends with
    the interval line simply omitted. This is only reached when the
    webhook-computed ``billing_period`` was empty, so the common paid
    flow never makes this call.
    """
    try:
        from payments.services import _get_subscription_interval

        return _get_subscription_interval(subscription_id) or ""
    except Exception:  # noqa: BLE001 - optional best-effort lookup, never blocks the send
        logger.warning(
            "notify_paid_signup: optional interval lookup failed for "
            "subscription=%s",
            subscription_id,
            exc_info=True,
        )
        return ""


def _dashboard_url(account_id, resource, object_id):
    """Build an account-scoped Stripe dashboard deep-link, or "" when blank.

    ``https://dashboard.stripe.com/<acct>/<resource>/<object_id>``. Returns
    ``""`` when either the account id or the object id is missing — the
    template then renders the id as plain copyable text (matching the
    Studio user-page behaviour). The account id encodes test vs live mode.
    """
    if not account_id or not object_id:
        return ""
    return f"{_STRIPE_DASHBOARD_BASE}/{account_id}/{resource}/{object_id}"


def _recent_activity_lines(user):
    """Return up to the last 5 activity rows as rendered text lines.

    Reuses the issue #853 query shape (``UserActivity`` ordered
    newest-first on ``occurred_at``). Each line is a short
    ``"<when> — <type>: <label>"`` summary. Returns a list with a single
    "No recorded activity yet" sentinel when the user has no rows so the
    template always has something to print.

    Defensive: any failure (import, DB) yields the empty-state line rather
    than breaking the staff email — the notification is best-effort.
    """
    try:
        from analytics.models import UserActivity

        rows = list(
            UserActivity.objects
            .filter(user=user)
            .order_by("-occurred_at")[:_ACTIVITY_LIMIT]
        )
    except Exception:  # noqa: BLE001 - best-effort summary, never blocks the send
        logger.exception(
            "notify_paid_signup: failed to load recent activity for user=%s",
            getattr(user, "pk", None),
        )
        return ["No recorded activity yet"]

    if not rows:
        return ["No recorded activity yet"]

    lines = []
    for row in rows:
        when = row.occurred_at.strftime("%Y-%m-%d %H:%M") if row.occurred_at else _DASH
        type_label = row.get_event_type_display()
        label = (row.label or "").strip() or _DASH
        lines.append(f"{when} — {type_label}: {label}")
    return lines


def _current_sprint_paragraph():
    """Sprint-status sentence injected into the welcome email.

    Issue #950: the welcome copy is now EVERGREEN — it links the public
    ``/sprints`` page and says we regularly run community sprints, all
    in the template itself. No dated, specific-sprint sentence is ever
    injected, so the email can't go stale (e.g. naming a finished sprint
    or a past month). This helper therefore always returns an empty
    string; it is retained as a stable injection point and so existing
    callers/tests keep a defined contract.
    """
    return ""


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
