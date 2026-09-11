"""Daily purge of expired unverified email-signup accounts.

The safety policy comes from issues #452 and #766. Issue #1522 changes only
the query plan: candidate fields are filtered in SQL, candidates are processed
in bounded primary-key windows, and every reverse relation is queried once per
batch instead of once per user.
"""

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from integrations.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_PURGE_UNVERIFIED_BATCH_SIZE = 500
DEFAULT_PURGE_UNVERIFIED_MAX_BATCHES = 50
PURGE_UNVERIFIED_RUN_BUDGET_SECONDS = 240

# Relations auto-populated for every user during signup. They are bookkeeping,
# not evidence of member activity. Any future reverse FK/O2O absent from this
# set automatically blocks deletion.
_PURGE_IGNORED_RELATIONS = frozenset({
    "logentry",
    "emailaddress",
    "socialaccount",
    "attribution",
    # payments.Membership (issue #1579): a OneToOne created by a
    # ``post_save`` receiver, so every user has exactly one. Its Stripe
    # identifiers are checked explicitly by the batched field gates, so the
    # row itself must not block.
    "membership",
    # analytics.UserActivity (issue #853): every user gets a ``signup``
    # row written by the same ``post_save`` chokepoint as ``attribution``.
    # For an unverified, never-logged-in email signup that is the ONLY
    # possible row (enroll / lesson / payment / event all require a
    # verified, authenticated session), so this relation is signup
    # bookkeeping — not user-driven activity — and must not block the
    # purge of an abandoned account.
    "activities",
})

# Verification mail and its SES event are evidence of a dead address in the
# eager-bounce pass, so only that pass ignores them.
_EAGER_PURGE_IGNORED_RELATIONS = _PURGE_IGNORED_RELATIONS | {
    "email_logs",
    "ses_events",
}


@dataclass
class _PassResult:
    deleted: int = 0
    skipped: int = 0
    batches: int = 0
    blocker_counts: Counter = field(default_factory=Counter)


def _positive_int_config(key, default):
    """Resolve a positive integer IntegrationSetting with a safe fallback."""
    raw = get_config(key, default)
    try:
        value = int(str(raw).strip(), 10)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _candidate_queryset(base_queryset):
    """Push every cheap, fail-closed field gate into the database query."""
    return base_queryset.select_related("membership").filter(
        last_login__isnull=True,
        membership__stripe_customer_id="",
        membership__subscription_id="",
    )


def _blocking_relations(User, ignored):
    """Yield reverse FK/O2O fields discovered from current User metadata."""
    for relation in User._meta.get_fields():
        if not relation.auto_created:
            continue
        if not (relation.one_to_many or relation.one_to_one):
            continue
        accessor_name = relation.get_accessor_name()
        if (
            not accessor_name
            or relation.name in ignored
            or accessor_name in ignored
        ):
            continue
        yield accessor_name, relation


def _related_user_ids(relation, batch_ids):
    """Return user ids referenced by one reverse relation in this batch."""
    foreign_key = relation.field
    return set(
        relation.related_model._base_manager.filter(
            **{f"{foreign_key.name}__in": batch_ids},
        ).values_list(foreign_key.attname, flat=True)
    )


def _batch_blockers(User, batch_ids, ignored):
    """Return each blocked id's first accessor, or a failed accessor name.

    A raised relation query means the batch was not fully inspected. The caller
    must therefore delete nobody from it.
    """
    blockers = {}
    for accessor_name, relation in _blocking_relations(User, ignored):
        try:
            related_ids = _related_user_ids(relation, batch_ids)
        except Exception:
            logger.exception(
                "Failed to query reverse relation %s for unverified-user "
                "purge batch; treating the whole batch as blocked.",
                accessor_name,
            )
            return None, accessor_name
        for user_id in related_ids:
            blockers.setdefault(user_id, accessor_name)
    return blockers, None


def _warn_skipped(user, reason, *, eager):
    if eager:
        logger.warning(
            "Skipping eager-bounce purge of user %s (id=%s): blocked by %s",
            user.email,
            user.pk,
            reason,
        )
    else:
        logger.warning(
            "Skipping purge of unverified user %s (id=%s): blocked by %s",
            user.email,
            user.pk,
            reason,
        )


def _field_blocker(user):
    if user.last_login is not None:
        return "last_login"
    if user.membership.stripe_customer_id:
        return "stripe_customer_id"
    if user.membership.subscription_id:
        return "subscription_id"
    return None


def _record_field_blockers(
    base_queryset,
    result,
    *,
    eager,
    warning_limit,
    deadline,
):
    """Preserve bounded warning/counter behavior for SQL-excluded blockers.

    Field-blocked rows are not relation-scan candidates. We still report a
    bounded window so existing operational greps for these safety gates remain
    useful, without walking or warning for an unprocessed backlog.
    """
    if time.monotonic() >= deadline:
        return
    blocker_filter = (
        Q(last_login__isnull=False)
        | ~Q(membership__stripe_customer_id="")
        | ~Q(membership__subscription_id="")
    )
    blocked_users = list(
        base_queryset.select_related("membership").filter(blocker_filter)
        .order_by("pk")[:warning_limit]
    )
    for user in blocked_users:
        reason = _field_blocker(user)
        result.skipped += 1
        result.blocker_counts[reason] += 1
        _warn_skipped(user, reason, eager=eager)


def _delete_batch(User, batch, blocked_by, result, *, eager):
    delete_ids = []
    for user in batch:
        reason = blocked_by.get(user.pk)
        if reason:
            result.skipped += 1
            result.blocker_counts[reason] += 1
            _warn_skipped(user, reason, eager=eager)
        else:
            delete_ids.append(user.pk)

    if not delete_ids:
        return

    # QuerySet.delete() uses Django's Collector, including cascades and delete
    # signals, while avoiding one collector traversal per user.
    User._base_manager.filter(pk__in=delete_ids).delete()
    result.deleted += len(delete_ids)

    for user in batch:
        if user.pk not in delete_ids:
            continue
        if eager:
            recorded_at_iso = (
                user.bounce_recorded_at.isoformat()
                if user.bounce_recorded_at is not None
                else ""
            )
            logger.info(
                "Eager-purged unverified bounced user email=%s (id=%s) "
                "bounce_recorded_at=%s last_bounce_diagnostic=%r",
                user.email,
                user.pk,
                recorded_at_iso,
                user.last_bounce_diagnostic or "",
            )
        else:
            logger.info(
                "Purged unverified user %s (id=%s) past verification_expires_at",
                user.email,
                user.pk,
            )


def _run_pass(
    base_queryset,
    ignored,
    *,
    eager,
    batch_size,
    max_batches,
    deadline,
):
    User = get_user_model()
    result = _PassResult()

    _record_field_blockers(
        base_queryset,
        result,
        eager=eager,
        warning_limit=batch_size,
        deadline=deadline,
    )

    candidates = _candidate_queryset(base_queryset)
    last_pk = 0
    while result.batches < max_batches and time.monotonic() < deadline:
        batch = list(
            candidates.filter(pk__gt=last_pk)
            .order_by("pk")[:batch_size]
        )
        if not batch:
            break
        result.batches += 1
        last_pk = batch[-1].pk
        batch_ids = [user.pk for user in batch]

        blocked_by, failed_accessor = _batch_blockers(User, batch_ids, ignored)
        if failed_accessor:
            result.skipped += len(batch)
            result.blocker_counts[failed_accessor] += len(batch)
            for user in batch:
                _warn_skipped(user, failed_accessor, eager=eager)
            continue

        # Relation inspection may itself consume the remaining time. Leave the
        # fully inspected batch for the next run instead of starting deletion
        # after the worker's safety budget.
        if time.monotonic() >= deadline:
            break
        _delete_batch(User, batch, blocked_by, result, eager=eager)

    return result


def _standard_base_queryset(now):
    User = get_user_model()
    return User._base_manager.filter(
        email_verified=False,
        verification_expires_at__isnull=False,
        verification_expires_at__lt=now,
    )


def _eager_base_queryset(now):
    User = get_user_model()
    delay_hours = getattr(settings, "BOUNCE_PURGE_DELAY_HOURS", 24)
    cutoff = now - timedelta(hours=delay_hours)
    return User._base_manager.filter(
        email_verified=False,
        bounce_state=User.BounceState.PERMANENT,
        bounce_recorded_at__lt=cutoff,
    )


def purge_unverified_users():
    """Hard-delete safe unverified accounts within bounded work limits."""
    now = timezone.now()
    batch_size = _positive_int_config(
        "PURGE_UNVERIFIED_BATCH_SIZE",
        DEFAULT_PURGE_UNVERIFIED_BATCH_SIZE,
    )
    max_batches = _positive_int_config(
        "PURGE_UNVERIFIED_MAX_BATCHES",
        DEFAULT_PURGE_UNVERIFIED_MAX_BATCHES,
    )
    deadline = time.monotonic() + PURGE_UNVERIFIED_RUN_BUDGET_SECONDS

    standard = _run_pass(
        _standard_base_queryset(now),
        _PURGE_IGNORED_RELATIONS,
        eager=False,
        batch_size=batch_size,
        max_batches=max_batches,
        deadline=deadline,
    )
    eager = _run_pass(
        _eager_base_queryset(now),
        _EAGER_PURGE_IGNORED_RELATIONS,
        eager=True,
        batch_size=batch_size,
        max_batches=max_batches,
        deadline=deadline,
    )

    remaining_standard = _candidate_queryset(_standard_base_queryset(now)).count()
    remaining_eager = _candidate_queryset(_eager_base_queryset(now)).count()
    deleted = standard.deleted + eager.deleted
    skipped = standard.skipped + eager.skipped
    blocker_counts = standard.blocker_counts + eager.blocker_counts
    blocker_summary = ",".join(
        f"{name}:{count}" for name, count in sorted(blocker_counts.items())
    ) or "none"

    logger.info(
        "purge_unverified_users completed: deleted=%d "
        "(standard=%d eager=%d) skipped=%d (standard=%d eager=%d) "
        "remaining=%d (standard=%d eager=%d) blockers=%s",
        deleted,
        standard.deleted,
        eager.deleted,
        skipped,
        standard.skipped,
        eager.skipped,
        remaining_standard + remaining_eager,
        remaining_standard,
        remaining_eager,
        blocker_summary,
    )
    return {
        "deleted": deleted,
        "deleted_standard": standard.deleted,
        "deleted_eager": eager.deleted,
        "skipped": skipped,
        "skipped_standard": standard.skipped,
        "skipped_eager": eager.skipped,
        "remaining_standard": remaining_standard,
        "remaining_eager": remaining_eager,
    }
