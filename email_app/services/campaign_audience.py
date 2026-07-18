"""Canonical campaign audience eligibility and count planning."""

from django.contrib.auth import get_user_model

from accounts.tier_audience import effective_level_at_least_q


def eligible_campaign_recipients(
    *,
    target_min_level=0,
    target_tags_any=None,
    target_tags_none=None,
    slack_filter="any",
    audience_verification="verified_only",
    target_event_id=None,
):
    """Return the exact recipient queryset used by preview and send paths."""
    User = get_user_model()
    if target_event_id is None:
        user_qs = User.objects.all()
    else:
        user_qs = User.objects.filter(
            event_registrations__event_id=target_event_id,
        )

    verification_filter = {}
    if audience_verification != "everyone":
        verification_filter["email_verified"] = True
    base_qs = (
        user_qs.filter(unsubscribed=False, **verification_filter)
        .filter(effective_level_at_least_q(target_min_level))
    )
    if slack_filter == "yes":
        base_qs = base_qs.filter(slack_member=True)
    elif slack_filter == "no":
        base_qs = base_qs.filter(slack_member=False)
    base_qs = base_qs.distinct()

    include_set = set(target_tags_any or [])
    exclude_set = set(target_tags_none or [])
    if not include_set and not exclude_set:
        return base_qs

    eligible_ids = []
    for pk, tags in base_qs.values_list("pk", "tags"):
        user_tags = set(tags or [])
        if include_set and not (user_tags & include_set):
            continue
        if exclude_set and user_tags & exclude_set:
            continue
        eligible_ids.append(pk)
    return User.objects.filter(pk__in=eligible_ids)


def campaign_recipient_count(**audience):
    """Count recipients without exposing identities to preview callers."""
    return eligible_campaign_recipients(**audience).count()
