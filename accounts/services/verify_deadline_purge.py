"""Delete accounts that ignored a verify-or-delete campaign.

An ``unverified_only`` campaign with ``verify_deadline_days`` promises that an
account still unverified that many days after its email was sent gets deleted.
This module keeps that promise and nothing broader:

- only recipients with a confirmed ``SENT`` delivery of THAT campaign are
  candidates, so nobody is deleted without having been told;
- every candidate is re-checked inside the delete transaction (anyone who
  verified, logged in, paid, or otherwise engaged since the send is skipped);
- a backup row is written to ``DeletedUserArchive`` in the same transaction;
- a dry run walks the identical code path and writes nothing.

The reverse-relation gate is shared with the daily unverified purge, with a
wider ignore list for rows that only record that mail was sent or an event was
registered for. Any other reverse relation blocks deletion.
"""

import json
import logging
from collections import Counter
from datetime import timedelta
from time import monotonic

from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from accounts.models import DeletedUserArchive
from accounts.tasks.purge_unverified_users import (
    _PURGE_IGNORED_RELATIONS,
    _batch_blockers,
)
from accounts.tier_audience import effective_level_at_least_q
from email_app.models import CampaignDelivery, EmailCampaign

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 500
BATCH_SIZE = 100
RUN_BUDGET_SECONDS = 20

# Rows that only record that mail went out or that the address registered for
# an event. They are deleted with the account (the backup keeps a summary).
_VERIFY_PURGE_IGNORED_RELATIONS = _PURGE_IGNORED_RELATIONS | {
    "email_logs",
    "ses_events",
    "campaign_deliveries",
    "resolved_campaign_deliveries",
    "released_campaign_waves",
    "community_base_email_deliveries",
    "event_registrations",
    "series_registrations",
    "series_occurrence_opt_outs",
    "event_join_clicks",
    "event_reminder_logs",
    "campaign_visits",
    "event_emissions",
}

_SNAPSHOT_EXCLUDED_FIELDS = {"password"}


class VerifyDeadlineError(ValueError):
    """The campaign is not a verify-or-delete campaign that can be purged."""


def _due_deliveries(campaign, now):
    cutoff = now - timedelta(days=campaign.verify_deadline_days)
    return campaign.deliveries.filter(
        state=CampaignDelivery.State.SENT,
        completed_at__isnull=False,
        completed_at__lte=cutoff,
    )


def _snapshot(user, delivery):
    fields = {}
    for field in user._meta.concrete_fields:
        if field.name in _SNAPSHOT_EXCLUDED_FIELDS:
            continue
        fields[field.attname] = field.value_from_object(user)
    return json.loads(json.dumps({
        "fields": fields,
        "contact_tags": list(
            user.member_extra.contact_tags.values_list("slug", flat=True)
        ) if hasattr(user, "member_extra") else [],
        "event_registration_event_ids": list(
            user.event_registrations.values_list("event_id", flat=True)
        ),
        "email_log_count": user.email_logs.count(),
        "delivery": {
            "id": delivery.pk,
            "recipient_email": delivery.recipient_email,
            "sent_at": delivery.completed_at,
        },
    }, cls=DjangoJSONEncoder))


def _classify(User, deliveries, now):
    """Return ``{delivery_id: (reason, user_or_None)}``; reason ``""`` = delete."""
    users = {
        user.pk: user
        for user in User._base_manager.select_related("membership").filter(
            pk__in=[d.recipient_user_pk for d in deliveries],
        )
    }
    paid_ids = set(
        User._base_manager.filter(
            pk__in=list(users),
        ).filter(effective_level_at_least_q(1)).values_list("pk", flat=True)
    )
    blockers, failed = _batch_blockers(
        User, list(users), _VERIFY_PURGE_IGNORED_RELATIONS,
    )
    result = {}
    for delivery in deliveries:
        user = users.get(delivery.recipient_user_pk)
        log = delivery.email_log
        if user is None:
            reason = "user_missing"
        elif user.email_verified:
            reason = "verified"
        elif not user.is_active:
            reason = "inactive"
        elif user.is_staff or user.is_superuser:
            reason = "staff"
        elif user.email.lower() != delivery.recipient_email.lower():
            reason = "email_changed"
        elif log is None or log.bounced_at or log.complained_at:
            reason = "bounced_or_complained"
        elif user.bounce_state == User.BounceState.PERMANENT:
            reason = "permanent_bounce"
        elif user.unsubscribed:
            reason = "unsubscribed"
        elif user.last_login is not None:
            reason = "logged_in"
        elif (
            user.membership.stripe_customer_id
            or user.membership.subscription_id
        ):
            reason = "stripe"
        elif user.pk in paid_ids:
            reason = "paid_tier"
        elif failed:
            reason = f"relation_check_failed:{failed}"
        elif user.pk in blockers:
            reason = f"blocked:{blockers[user.pk]}"
        else:
            reason = ""
        result[delivery.pk] = (reason, user)
    return result


def purge_unverified_after_deadline(
    campaign_id, *, dry_run=True, limit=DEFAULT_LIMIT, after=0, now=None,
):
    """Delete (or count) accounts still unverified past the campaign deadline.

    Work is bounded per call: pass the returned ``last_delivery_id`` as
    ``after`` until ``complete`` is true.
    """
    now = now or timezone.now()
    User = get_user_model()
    campaign = EmailCampaign.objects.get(pk=campaign_id)
    if (
        not campaign.verify_deadline_days
        or campaign.audience_verification
        != EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY
    ):
        raise VerifyDeadlineError(
            "Campaign is not an unverified_only verify-or-delete campaign."
        )

    due = _due_deliveries(campaign, now)
    summary = {
        "campaign_id": campaign.pk,
        "dry_run": dry_run,
        "deadline_days": campaign.verify_deadline_days,
        "sent_deliveries": campaign.deliveries.filter(
            state=CampaignDelivery.State.SENT,
        ).count(),
        "due_deliveries": due.count(),
    }
    reasons = Counter()
    deleted = 0
    processed = 0
    deadline = monotonic() + RUN_BUDGET_SECONDS
    last_pk = after
    while processed < limit and monotonic() < deadline:
        chunk = list(
            due.filter(pk__gt=last_pk).select_related("email_log").order_by("pk")
            [: min(BATCH_SIZE, limit - processed)]
        )
        if not chunk:
            break
        last_pk = chunk[-1].pk
        processed += len(chunk)
        if dry_run:
            for reason, _user in _classify(User, chunk, now).values():
                reasons[reason or "deletable"] += 1
            continue
        with transaction.atomic():
            # Lock first so a verification click cannot land between the
            # re-check and the delete.
            list(
                User._base_manager.select_for_update(of=("self",)).filter(
                    pk__in=[d.recipient_user_pk for d in chunk],
                ).values_list("pk", flat=True)
            )
            classified = _classify(User, chunk, now)
            archives, delete_ids = [], []
            for delivery in chunk:
                reason, user = classified[delivery.pk]
                reasons[reason or "deleted"] += 1
                if reason:
                    continue
                archives.append(DeletedUserArchive(
                    original_user_id=user.pk,
                    email=user.email,
                    campaign_id=campaign.pk,
                    snapshot=_snapshot(user, delivery),
                ))
                delete_ids.append(user.pk)
            if delete_ids:
                DeletedUserArchive.objects.bulk_create(archives)
                User._base_manager.filter(
                    pk__in=delete_ids, email_verified=False,
                ).delete()
                deleted += len(delete_ids)
    summary.update({
        "processed": processed,
        "deleted": deleted,
        "reasons": dict(sorted(reasons.items())),
        "archived_total": DeletedUserArchive.objects.filter(
            campaign_id=campaign.pk,
        ).count(),
        "last_delivery_id": last_pk,
        "complete": not due.filter(pk__gt=last_pk).exists(),
    })
    logger.info("verify_deadline_purge %s", summary)
    return summary


_AGE_BUCKETS = (
    ("under_30_days", None, 30),
    ("30_to_90_days", 30, 90),
    ("90_to_180_days", 90, 180),
    ("180_to_365_days", 180, 365),
    ("1_to_2_years", 365, 730),
    ("over_2_years", 730, None),
)


def unverified_audit(now=None):
    """Read-only counts of unverified accounts by signup age and segment."""
    from django.db.models import Count, Q

    now = now or timezone.now()
    User = get_user_model()
    unverified = User._base_manager.filter(email_verified=False)

    def counts(queryset):
        segments = {
            "total": Q(),
            "emailable": Q(is_active=True, unsubscribed=False)
            & ~Q(bounce_state=User.BounceState.PERMANENT),
            "unsubscribed": Q(unsubscribed=True),
            "permanent_bounce": Q(bounce_state=User.BounceState.PERMANENT),
            "inactive": Q(is_active=False),
            "logged_in_ever": Q(last_login__isnull=False),
            "staff": Q(is_staff=True) | Q(is_superuser=True),
            "stripe": ~Q(membership__stripe_customer_id="")
            | ~Q(membership__subscription_id=""),
            "paid_tier": effective_level_at_least_q(1),
        }
        return queryset.aggregate(**{
            name: Count("pk", filter=condition, distinct=True)
            for name, condition in segments.items()
        })

    by_age = {}
    for label, low, high in _AGE_BUCKETS:
        bucket = unverified
        if low is not None:
            bucket = bucket.filter(date_joined__lte=now - timedelta(days=low))
        if high is not None:
            bucket = bucket.filter(date_joined__gt=now - timedelta(days=high))
        by_age[label] = counts(bucket)
    return {
        "generated_at": now.isoformat(),
        "unverified": counts(unverified),
        "verified_total": User._base_manager.filter(email_verified=True).count(),
        "by_signup_age": by_age,
    }
