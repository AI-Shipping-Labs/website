"""Daily purge of expired unverified email-signup accounts (issue #452).

An email-only signup is considered abandoned if the user never clicked
the verification link before ``verification_expires_at``. We hard-delete
such rows so they stop polluting campaign targeting and free up the
unique-email slot if the user wants to retry with a corrected typo.

Hard-delete is permanent, so the safety gate is paranoid:

- ``email_verified`` MUST be False.
- ``verification_expires_at`` MUST be a real timestamp in the past.
  Legacy rows where it is NULL are out of scope on purpose — the
  migration leaves existing users untouched so we only enforce the
  policy from #452 onward.
- ``last_login`` MUST be NULL — if a user ever logged in, even once,
  they're a real user and we don't touch them.
- ``stripe_customer_id`` and ``subscription_id`` MUST be empty —
  payments imply a real account.
- No reverse FK on ``User`` may point at the user. We enumerate via
  ``_meta.get_fields()`` so future apps that add a ``ForeignKey(User)``
  automatically gate the purge without code changes here.

When safety blocks deletion the user row stays put with
``verification_expires_at`` still set, and the next daily run will
retry the gate (in case e.g. an EmailLog row was the only blocker and
was archived in the meantime).

Two-pass model (issue #766):

- Pass A — Standard bucket: 7-day TTL with the strict gate. EmailLog
  and SesEvent rows block deletion here so we don't blindly erase
  audit data for rows that might still come back.
- Pass B — Eager-bounce bucket: an unverified user whose verification
  email permanently bounced more than ``BOUNCE_PURGE_DELAY_HOURS`` ago
  is provably dead. We extend the ignore-set to skip EmailLog and
  SesEvent (the verification send is what bounced, so of course it's
  on the user) but keep every other blocker. The pass logs at INFO so
  an audit can reconstruct each deletion.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

logger = logging.getLogger(__name__)

# Relations auto-populated for every user during signup (admin
# bookkeeping, allauth scaffolding, attribution snapshots). They do
# not indicate "real activity" so they must not block the purge.
# Anything not in this set blocks purge if it has rows.
_PURGE_IGNORED_RELATIONS = frozenset({
    # Django admin audit log — only populated for staff actions, but
    # keep it ignored so a stray staff-impersonation entry isn't a
    # reason to keep an unverified email signup forever.
    "logentry",
    # allauth EmailAddress / SocialAccount rows — present for any
    # user that ever interacted with allauth, including bookkeeping.
    "emailaddress",
    "socialaccount",
    # analytics.UserAttribution is a OneToOne created by a
    # ``post_save`` signal on User, so every user has exactly one of
    # these. It carries UTM data, not user-driven activity.
    "attribution",
})

# Extra reverse relations ignored only by the eager-bounce bucket
# (issue #766). The verification email is the bounce source, so the
# matching ``EmailLog`` / ``SesEvent`` rows are not "activity" -- they
# are the very evidence that the address is dead.
_EAGER_PURGE_IGNORED_RELATIONS = _PURGE_IGNORED_RELATIONS | {
    "email_logs",
    "ses_events",
}


def _reverse_relation_has_rows(user, accessor_name, *, one_to_one):
    """Return True iff the user has at least one row on ``accessor_name``."""
    if one_to_one:
        try:
            getattr(user, accessor_name)
        except ObjectDoesNotExist:
            return False
        return True
    manager = getattr(user, accessor_name)
    return manager.exists()


def _first_blocking_relation(user, ignored):
    """Walk reverse relations on ``user``; return the first that has rows."""
    for field in user._meta.get_fields():
        if not field.auto_created:
            continue
        if not (field.one_to_many or field.one_to_one):
            continue
        accessor_name = field.get_accessor_name()
        if not accessor_name:
            continue
        if accessor_name in ignored:
            continue
        try:
            has_rows = _reverse_relation_has_rows(
                user, accessor_name, one_to_one=field.one_to_one,
            )
        except Exception:
            logger.exception(
                "Failed to check reverse relation %s on user %s; "
                "treating as blocked to be safe.",
                accessor_name,
                user.pk,
            )
            return accessor_name
        if has_rows:
            return accessor_name
    return None


def _user_has_related_activity(user):
    """Return the name of the first reverse relation with rows, or None.

    Standard-bucket gate: every reverse relation outside
    ``_PURGE_IGNORED_RELATIONS`` blocks deletion. Used by the strict
    7-day TTL bucket so an EmailLog / SesEvent row keeps the user.
    """
    return _first_blocking_relation(user, _PURGE_IGNORED_RELATIONS)


def _user_has_eager_blocking_activity(user):
    """Return the first blocking reverse relation for the eager bucket.

    Eager-bucket gate: extends ``_PURGE_IGNORED_RELATIONS`` with
    ``email_logs`` and ``ses_events`` (issue #766). Stripe / login /
    everything else still blocks.
    """
    return _first_blocking_relation(user, _EAGER_PURGE_IGNORED_RELATIONS)


def _is_safe_to_purge(user):
    """Confirm the candidate user has done nothing worth preserving."""
    if user.last_login is not None:
        return False, "last_login"
    if user.stripe_customer_id:
        return False, "stripe_customer_id"
    if user.subscription_id:
        return False, "subscription_id"
    blocker = _user_has_related_activity(user)
    if blocker:
        return False, blocker
    return True, None


def _is_safe_to_eager_purge(user):
    """Same as :func:`_is_safe_to_purge` but ignores EmailLog / SesEvent.

    Used by the eager-bounce bucket where the EmailLog / SesEvent rows
    ARE the evidence that the user is dead, not a signal of real
    activity (issue #766).
    """
    if user.last_login is not None:
        return False, "last_login"
    if user.stripe_customer_id:
        return False, "stripe_customer_id"
    if user.subscription_id:
        return False, "subscription_id"
    blocker = _user_has_eager_blocking_activity(user)
    if blocker:
        return False, blocker
    return True, None


def _run_standard_pass(now):
    """Pass A: standard 7-day TTL bucket (existing behavior)."""
    User = get_user_model()
    candidates = User.objects.filter(
        email_verified=False,
        verification_expires_at__isnull=False,
        verification_expires_at__lt=now,
    )

    deleted = 0
    skipped = 0
    for user in candidates:
        safe, reason = _is_safe_to_purge(user)
        if not safe:
            skipped += 1
            logger.warning(
                "Skipping purge of unverified user %s (id=%s): blocked by %s",
                user.email,
                user.pk,
                reason,
            )
            continue
        user_pk = user.pk
        user_email = user.email
        user.delete()
        deleted += 1
        logger.info(
            "Purged unverified user %s (id=%s) past verification_expires_at",
            user_email,
            user_pk,
        )
    return deleted, skipped


def _run_eager_bounce_pass(now):
    """Pass B: NEW eager-bounce bucket (issue #766).

    Drops unverified users whose verification email bounced permanently
    more than ``BOUNCE_PURGE_DELAY_HOURS`` ago. Reads the override
    inside the function so tests can patch ``settings`` per-test.
    """
    User = get_user_model()
    delay_hours = getattr(settings, "BOUNCE_PURGE_DELAY_HOURS", 24)
    cutoff = now - timedelta(hours=delay_hours)

    candidates = User.objects.filter(
        email_verified=False,
        bounce_state=User.BounceState.PERMANENT,
        bounce_recorded_at__lt=cutoff,
    )

    deleted = 0
    skipped = 0
    for user in candidates:
        safe, reason = _is_safe_to_eager_purge(user)
        if not safe:
            skipped += 1
            logger.warning(
                "Skipping eager-bounce purge of user %s (id=%s): blocked by %s",
                user.email,
                user.pk,
                reason,
            )
            continue
        user_pk = user.pk
        user_email = user.email
        recorded_at_iso = (
            user.bounce_recorded_at.isoformat()
            if user.bounce_recorded_at is not None
            else ""
        )
        diagnostic = user.last_bounce_diagnostic or ""
        user.delete()
        deleted += 1
        logger.info(
            "Eager-purged unverified bounced user email=%s (id=%s) "
            "bounce_recorded_at=%s last_bounce_diagnostic=%r",
            user_email,
            user_pk,
            recorded_at_iso,
            diagnostic,
        )
    return deleted, skipped


def purge_unverified_users():
    """Hard-delete expired unverified email-signup accounts.

    Runs the standard 7-day TTL bucket first (Pass A), then the
    eager-bounce bucket (Pass B). Each pass is independent: a failure
    in one bucket does not abort the other.

    Returns:
        dict: ``{
            "deleted": <total>,
            "deleted_standard": <int>,
            "deleted_eager": <int>,
            "skipped": <total>,
            "skipped_standard": <int>,
            "skipped_eager": <int>,
        }``.

        The legacy ``deleted`` / ``skipped`` keys equal the sum of the
        per-bucket counters so existing monitors keep working (issue
        #766).
    """
    now = timezone.now()

    deleted_standard, skipped_standard = _run_standard_pass(now)
    deleted_eager, skipped_eager = _run_eager_bounce_pass(now)

    deleted = deleted_standard + deleted_eager
    skipped = skipped_standard + skipped_eager

    if deleted or skipped:
        logger.info(
            "purge_unverified_users completed: deleted=%d "
            "(standard=%d eager=%d) skipped=%d (standard=%d eager=%d)",
            deleted,
            deleted_standard,
            deleted_eager,
            skipped,
            skipped_standard,
            skipped_eager,
        )
    return {
        "deleted": deleted,
        "deleted_standard": deleted_standard,
        "deleted_eager": deleted_eager,
        "skipped": skipped,
        "skipped_standard": skipped_standard,
        "skipped_eager": skipped_eager,
    }
