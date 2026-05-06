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
"""

import logging

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


def _user_has_related_activity(user):
    """Return the name of the first reverse relation with rows, or None.

    Walks ``User._meta.get_fields()`` and checks every auto-created
    one-to-many or one-to-one back-reference. Skips relations in
    ``_PURGE_IGNORED_RELATIONS`` because those are populated as part
    of normal signup scaffolding rather than user-driven activity.
    """
    for field in user._meta.get_fields():
        if not field.auto_created:
            continue
        if not (field.one_to_many or field.one_to_one):
            continue
        accessor_name = field.get_accessor_name()
        if not accessor_name:
            continue
        if accessor_name in _PURGE_IGNORED_RELATIONS:
            continue
        try:
            if field.one_to_many:
                manager = getattr(user, accessor_name)
                if manager.exists():
                    return accessor_name
            else:
                # OneToOne reverse: descriptor raises DoesNotExist when
                # no related row exists. Any successful access means a
                # row is there and counts as activity.
                try:
                    getattr(user, accessor_name)
                except ObjectDoesNotExist:
                    continue
                return accessor_name
        except Exception:
            logger.exception(
                "Failed to check reverse relation %s on user %s; "
                "treating as blocked to be safe.",
                accessor_name,
                user.pk,
            )
            return accessor_name
    return None


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


def purge_unverified_users():
    """Hard-delete expired unverified email-signup accounts.

    Returns:
        dict: ``{"deleted": N, "skipped": M}`` summary for logging.
    """
    User = get_user_model()
    now = timezone.now()

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

    if deleted or skipped:
        logger.info(
            "purge_unverified_users completed: deleted=%d skipped=%d",
            deleted,
            skipped,
        )
    return {"deleted": deleted, "skipped": skipped}
