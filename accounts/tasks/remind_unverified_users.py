"""Daily one-shot reminder for unverified email-signup accounts (issue #452).

Sends a "verify your email or your account expires in 24 hours" nudge
to users where ``verification_expires_at`` is within the next 24 hours
and we haven't already nudged them. Tracked via
``verification_reminder_sent_at`` so a slow-running cron, retries, or
multiple workers can never spam the same user twice.

Skip conditions:

- ``email_verified=True`` — already done, nothing to remind about.
- ``unsubscribed=True`` — respect the global unsubscribe.
- ``last_login is not None`` — the account has been used; spec #452
  treats it as a real account that just hasn't toggled the verified
  flag, and the purge job will skip it for the same reason.
- ``verification_reminder_sent_at`` already set — one reminder per user.

Issue #767: the reminder template is chosen per user based on the slug
of the most recent verification ``EmailLog`` row (signup vs subscribe).

A1.2: the send carries the stable idempotency key
``{template}:{user.pk}`` on the durable ``EmailDelivery``, so two
overlapping runs cannot create two deliveries for the same member and
template; the verify URL is minted in the worker by
``email_app.hooks.resolve_auth_mail_context`` and never stored.
"""

import datetime
import logging

from django.contrib.auth import get_user_model
from django.utils import timezone

logger = logging.getLogger(__name__)

REMINDER_WINDOW = datetime.timedelta(hours=24)
SIGNUP_REMINDER_TEMPLATE_NAME = "email_verification_signup_reminder"
SUBSCRIBE_REMINDER_TEMPLATE_NAME = "email_verification_subscribe_reminder"
VERIFICATION_SLUGS = (
    "email_verification_signup",
    "email_verification_subscribe",
)


def _pick_reminder_template(user):
    """Choose the reminder template based on the user's latest verification log.

    Returns ``email_verification_subscribe_reminder`` when the user's most
    recent verification EmailLog is ``email_verification_subscribe``;
    otherwise returns ``email_verification_signup_reminder`` (safe default
    that also covers users with no prior verification log — e.g. legacy
    users whose original send predates this split or was wiped).
    """
    # Inline import: ``email_app.models`` pulls in the user model
    # indirectly via FK metadata, which would be a circular dep at the
    # accounts.tasks module-load boundary.
    from email_app.models import EmailLog  # noqa: PLC0415

    latest = (
        EmailLog.objects
        .filter(user=user, email_type__in=VERIFICATION_SLUGS)
        .order_by("-sent_at")
        .first()
    )
    if latest is not None and latest.email_type == "email_verification_subscribe":
        return SUBSCRIBE_REMINDER_TEMPLATE_NAME
    return SIGNUP_REMINDER_TEMPLATE_NAME


def remind_unverified_users():
    """Send the 24-hour-before-expiry verification reminder.

    The reminder goes through the package mail app (A1.2): the caller
    persists only ``expires_at``, and ``email_app.hooks.resolve_auth_mail_context``
    mints the signed verify URL in the worker.

    Returns:
        dict: ``{"sent": N, "skipped": M}`` summary for logging.
    """
    from community_base.mail.models import EmailDelivery  # noqa: PLC0415

    from email_app.package_mail import send_package_mail  # noqa: PLC0415

    User = get_user_model()
    now = timezone.now()
    cutoff = now + REMINDER_WINDOW

    candidates = User.objects.filter(
        email_verified=False,
        unsubscribed=False,
        last_login__isnull=True,
        verification_reminder_sent_at__isnull=True,
        verification_expires_at__isnull=False,
        verification_expires_at__gt=now,
        verification_expires_at__lte=cutoff,
    )

    sent = 0
    skipped = 0
    for user in candidates:
        template_name = _pick_reminder_template(user)
        try:
            delivery = send_package_mail(
                user,
                template_name,
                {
                    # Delivery context must be JSON values (A1.2); the
                    # reminder templates state the window in prose, the
                    # timestamp stays audit-only.
                    "expires_at": user.verification_expires_at.isoformat(),
                },
                idempotency_key=f"{template_name}:{user.pk}",
            )
        except Exception:
            logger.exception(
                "Failed to send verification reminder to %s",
                user.email,
            )
            skipped += 1
            continue

        if delivery.state == EmailDelivery.State.SUPPRESSED:
            # The preference resolver declined the send (e.g. unsubscribed
            # flipped between the queryset and the send). Don't mark as
            # sent so we can retry next day if circumstances change.
            skipped += 1
            continue

        user.verification_reminder_sent_at = timezone.now()
        user.save(update_fields=["verification_reminder_sent_at"])
        sent += 1

    if sent or skipped:
        logger.info(
            "remind_unverified_users completed: sent=%d skipped=%d",
            sent,
            skipped,
        )
    return {"sent": sent, "skipped": skipped}
