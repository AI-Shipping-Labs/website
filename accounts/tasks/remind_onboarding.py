"""One-shot onboarding reminder sweep (issue #1133).

A paid member receives their onboarding link inside the paid-signup
welcome email (``basic_welcome`` / ``cofounder_welcome`` /
``premium_welcome``). When they have not completed onboarding after a
configurable delay (default 7 days), this daily sweep sends a single
warm nudge and BCCs the team a copy.

Design notes mirroring ``remind_unverified_users`` (issue #452):

- The 7-day window is anchored on the ``EmailLog.sent_at`` of the
  member's EARLIEST welcome email whose ``email_type`` is in
  :data:`ONBOARDING_WELCOME_SLUGS` — the literal "received the
  onboarding email" moment. No new field or migration is needed.
- Completion is derived, not stored: ``has_completed_onboarding`` (a
  submitted ``purpose='onboarding'`` Response) is the single source of
  truth. A completed member is never reminded.
- Paid-now gating: ``can_access_onboarding`` (effective tier level
  >= LEVEL_BASIC) is re-checked at run time so churned members are not
  chased.
- Idempotency is two-layered. ``find_due_members`` skips any member with
  an ``EmailLog`` of type ``onboarding_reminder`` — written by the mail
  worker after provider acceptance. Inside the acceptance gap before
  that row exists, the send carries the stable idempotency key
  ``onboarding_reminder:{user.pk}`` on the durable ``EmailDelivery``, so
  a cron re-tick, retry, or restart can never create a second delivery.
  One reminder per member, ever.
- All tunables (``ONBOARDING_REMINDER_ENABLED``,
  ``ONBOARDING_REMINDER_DELAY_DAYS``) resolve through the
  IntegrationSetting framework via ``get_config`` — no hard-coded values,
  no raw ``os.environ`` reads.
"""

import datetime
import logging

from django.contrib.auth import get_user_model
from django.db.models import Min
from django.utils import timezone

from integrations.config import get_config

logger = logging.getLogger(__name__)

# The paid-signup welcome templates that carry the ``/onboarding/`` link.
# Extending the reminder to another lifecycle is a one-line change here.
# Explicitly EXCLUDED (different lifecycles / already onboarded): the
# returning-member ``welcome_back``, ``welcome_imported``,
# ``community_invite``, and ``maven_welcome``.
ONBOARDING_WELCOME_SLUGS = {
    "basic_welcome",
    "cofounder_welcome",
    "premium_welcome",
}

# The reminder email template / EmailLog type. Its presence is the
# idempotency key — one row per member means "already reminded".
REMINDER_EMAIL_TYPE = "onboarding_reminder"

# Studio-editable config keys (registered in integrations.settings_registry).
ENABLED_KEY = "ONBOARDING_REMINDER_ENABLED"
DELAY_DAYS_KEY = "ONBOARDING_REMINDER_DELAY_DAYS"
DEFAULT_DELAY_DAYS = 7


def reminder_enabled():
    """True unless the reminder sweep is explicitly disabled in Studio.

    Defaults ON: the flag exists to turn the sweep OFF without a redeploy,
    so only an explicit falsey value disables it.
    """
    raw = get_config(ENABLED_KEY, "true")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes")


def reminder_delay_days():
    """Resolve the reminder delay in days via ``get_config`` (default 7).

    A blank, non-numeric, or non-positive override falls back to the
    default so the cron can never send at time zero or crash on a typo.
    """
    raw = get_config(DELAY_DAYS_KEY, DEFAULT_DELAY_DAYS)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_DELAY_DAYS
    return value if value > 0 else DEFAULT_DELAY_DAYS


def _cohort_welcome_map(cutoff):
    """Map ``user_id -> earliest welcome sent_at`` for the past-cutoff cohort.

    A member is in the cohort when the EARLIEST of their onboarding-welcome
    ``EmailLog`` rows was sent on or before ``cutoff``.
    """
    from email_app.models import EmailLog  # noqa: PLC0415  circular-dep guard

    rows = (
        EmailLog.objects
        .filter(email_type__in=ONBOARDING_WELCOME_SLUGS, user__isnull=False)
        .values("user")
        .annotate(earliest=Min("sent_at"))
        .filter(earliest__lte=cutoff)
    )
    return {row["user"]: row["earliest"] for row in rows}


def find_due_members(now=None):
    """Return ``[(user, welcome_sent_at), ...]`` for members due a reminder now.

    "Due" means: earliest onboarding-welcome older than the configured
    delay, has NOT completed onboarding, still qualifies as a paid member,
    and has NOT already been reminded. This is the exact set the sweep
    would email — used by the sweep and by the ``--dry-run`` preview.
    """
    from email_app.models import EmailLog  # noqa: PLC0415  circular-dep guard
    from questionnaires.onboarding import (  # noqa: PLC0415  circular-dep guard
        can_access_onboarding,
        has_completed_onboarding,
    )

    now = now or timezone.now()
    cutoff = now - datetime.timedelta(days=reminder_delay_days())
    welcome_map = _cohort_welcome_map(cutoff)
    if not welcome_map:
        return []

    User = get_user_model()
    users = User.objects.filter(pk__in=welcome_map.keys()).order_by("pk")
    already_reminded = set(
        EmailLog.objects
        .filter(email_type=REMINDER_EMAIL_TYPE, user__in=users)
        .values_list("user", flat=True)
    )

    due = []
    for user in users:
        if user.pk in already_reminded:
            continue
        if has_completed_onboarding(user):
            continue
        if not can_access_onboarding(user):
            continue
        due.append((user, welcome_map[user.pk]))
    return due


def remind_onboarding_incomplete():
    """Send the one-week onboarding reminder to due members, BCC the team.

    Returns:
        dict: ``{"sent": N, "skipped": M}`` summary for logging. ``skipped``
        counts cohort members that were NOT emailed this run (completed,
        churned, already reminded, or a send the preference resolver
        suppressed).
    """
    from community_base.mail.models import EmailDelivery  # noqa: PLC0415

    from email_app.package_mail import send_package_mail  # noqa: PLC0415

    if not reminder_enabled():
        logger.info("remind_onboarding_incomplete skipped: disabled via %s", ENABLED_KEY)
        return {"sent": 0, "skipped": 0}

    now = timezone.now()
    cutoff = now - datetime.timedelta(days=reminder_delay_days())
    cohort_size = len(_cohort_welcome_map(cutoff))
    if cohort_size == 0:
        return {"sent": 0, "skipped": 0}

    due = find_due_members(now=now)
    team_email = (get_config("STAFF_SIGNUP_NOTIFY_EMAIL", "") or "").strip()

    sent = 0
    for user, _welcome_at in due:
        try:
            delivery = send_package_mail(
                user,
                REMINDER_EMAIL_TYPE,
                {},
                bcc=team_email or None,
                idempotency_key=f"{REMINDER_EMAIL_TYPE}:{user.pk}",
            )
        except Exception:
            logger.exception(
                "Failed to send onboarding reminder to %s", user.email,
            )
            continue
        if delivery.state == EmailDelivery.State.SUPPRESSED:
            # The preference resolver declined the send; leave the member
            # due for the next run.
            continue
        sent += 1

    skipped = cohort_size - sent
    if sent or skipped:
        logger.info(
            "remind_onboarding_incomplete completed: sent=%d skipped=%d",
            sent,
            skipped,
        )
    return {"sent": sent, "skipped": skipped}
