"""Shared audience-selection predicate for effective tier level (issue #966).

A user is eligible at level N if their *effective* tier level is >= N, where
the effective level is the higher of:

- their real subscription tier (``user.tier.level``), OR
- an active, non-expired ``TierOverride`` whose ``override_tier.level >= N``.

This is the canonical recipient/audience predicate. It is the same OR-clause
the live email send path uses (``EmailCampaign.get_eligible_recipients``) and
the Slack-membership refresh uses (``slack_membership.main_plus_q``). Centralized
here — where ``TierOverride`` lives — so every caller (email_app, notifications,
community, studio) shares one definition that cannot drift again.

IMPORTANT: any queryset filtered with this Q object MUST call ``.distinct()``,
because the ``tier_overrides`` join can duplicate user rows when a user holds
more than one override.
"""

from django.db.models import Q
from django.utils import timezone


def effective_level_at_least_q(min_level):
    """Return a Q matching users whose effective tier level >= ``min_level``.

    Matches users who reach ``min_level`` either by their real ``tier`` row OR
    by an active, non-expired ``TierOverride`` to a tier at/above ``min_level``.

    ``timezone.now()`` is evaluated at call time (not import time) so the
    expiry comparison is always current, matching ``get_eligible_recipients``
    and ``main_plus_q``.

    Querysets using this MUST ``.distinct()`` — the override join can duplicate
    rows.
    """
    now = timezone.now()
    return Q(tier__level__gte=min_level) | Q(
        tier_overrides__is_active=True,
        tier_overrides__expires_at__gt=now,
        tier_overrides__override_tier__level__gte=min_level,
    )
