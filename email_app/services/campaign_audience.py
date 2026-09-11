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
    if audience_verification == "unverified_only":
        verification_filter["email_verified"] = False
    elif audience_verification != "everyone":
        verification_filter["email_verified"] = True
    base_qs = (
        user_qs.filter(
            is_active=True,
            unsubscribed=False,
            **verification_filter,
        )
        .filter(effective_level_at_least_q(target_min_level))
    )
    if audience_verification == "unverified_only":
        base_qs = base_qs.exclude(bounce_state="permanent")
    if slack_filter == "yes":
        base_qs = base_qs.filter(slack_member=True)
    elif slack_filter == "no":
        base_qs = base_qs.filter(slack_member=False)
    base_qs = base_qs.distinct()

    include_set = set(target_tags_any or [])
    exclude_set = set(target_tags_none or [])
    if not include_set and not exclude_set:
        return base_qs

    if include_set:
        base_qs = base_qs.filter(contact_tags__slug__in=include_set)
    if exclude_set:
        base_qs = base_qs.exclude(contact_tags__slug__in=exclude_set)
    return base_qs.distinct()


def campaign_recipient_count(**audience):
    """Count recipients without exposing identities to preview callers."""
    return eligible_campaign_recipients(**audience).count()
