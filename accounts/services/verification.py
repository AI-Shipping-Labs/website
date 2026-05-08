"""Shared helpers for the email-verification / unverified-account flow.

Issue #513: both ``accounts.views.auth`` (email+password signup) and
``email_app.views.newsletter`` (newsletter subscribe) create unverified
``User`` rows that are subject to the daily auto-purge job introduced by
issue #452. Both paths must use the same TTL resolution rules so the
purge window is consistent regardless of how the user landed on the
site. This module is the single source of truth — never re-implement
``_resolve_unverified_ttl_days`` in a view.
"""

from integrations.config import get_config

# Default grace period before an unverified email-only account is
# hard-deleted by the daily purge task. Operators override per
# environment via the ``UNVERIFIED_USER_TTL_DAYS`` integration setting.
DEFAULT_UNVERIFIED_USER_TTL_DAYS = 7


def resolve_unverified_ttl_days():
    """Resolve the unverified-account grace period from operator config.

    Reads ``UNVERIFIED_USER_TTL_DAYS`` (Studio > Settings > Auth) with
    a 7-day fallback. Non-positive or non-numeric values fall back to
    the default so a typo cannot disable the feature accidentally.
    """
    raw = get_config(
        "UNVERIFIED_USER_TTL_DAYS",
        str(DEFAULT_UNVERIFIED_USER_TTL_DAYS),
    )
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_UNVERIFIED_USER_TTL_DAYS
    if days <= 0:
        return DEFAULT_UNVERIFIED_USER_TTL_DAYS
    return days
