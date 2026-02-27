"""
Task to expire tier overrides that have passed their expiry time.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def expire_tier_overrides():
    """Deactivate all TierOverride records whose expires_at has passed.

    Uses a bulk queryset.update() for efficiency. Safe for concurrent
    runs -- if an override was already deactivated by a previous run,
    the WHERE clause simply won't match it.

    Returns:
        dict with count of deactivated overrides.
    """
    from accounts.models import TierOverride

    now = timezone.now()
    count = TierOverride.objects.filter(
        is_active=True,
        expires_at__lte=now,
    ).update(is_active=False)

    if count:
        logger.info("Deactivated %d expired tier overrides", count)
    return {'deactivated': count}
