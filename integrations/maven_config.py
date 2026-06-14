"""Resolved Maven integration config (issue #960).

Thin wrappers over ``integrations.config.get_config`` / ``is_enabled`` so
the webhook view, the handler, and the replay command all read the same
validated values. Never read raw env / ``settings`` for these — go through
here so Studio settings overrides take effect with no redeploy.
"""

import logging

from integrations.config import get_config, is_enabled

logger = logging.getLogger(__name__)

DEFAULT_OVERRIDE_TIER_SLUG = "main"
DEFAULT_OVERRIDE_DURATION_DAYS = 3650


def maven_enabled():
    """Return True when the Maven auto-onboarding flow is switched on."""
    return is_enabled("MAVEN_ENROLLMENT_ENABLED")


def maven_shared_secret():
    """Return the configured shared secret (empty string when unset)."""
    return (get_config("MAVEN_WEBHOOK_SHARED_SECRET", "") or "").strip()


def maven_override_tier_slug():
    """Return the validated override tier slug.

    Falls back to ``main`` (with a log) when the configured slug is blank,
    unknown, or a free / level-0 tier — overrides only ever upgrade.
    """
    # Inline import: this module lives in ``integrations`` alongside
    # ``config.py`` which is imported very early during settings resolution;
    # importing ``payments.models`` at module top risks an app-loading cycle.
    from payments.models import Tier  # noqa: PLC0415

    slug = (get_config("MAVEN_OVERRIDE_TIER_SLUG", DEFAULT_OVERRIDE_TIER_SLUG) or "").strip()
    if not slug:
        return DEFAULT_OVERRIDE_TIER_SLUG

    tier = Tier.objects.filter(slug=slug).first()
    if tier is None:
        logger.warning(
            "MAVEN_OVERRIDE_TIER_SLUG=%r is not a known Tier; falling back to %r",
            slug,
            DEFAULT_OVERRIDE_TIER_SLUG,
        )
        return DEFAULT_OVERRIDE_TIER_SLUG
    if tier.level <= 0:
        logger.warning(
            "MAVEN_OVERRIDE_TIER_SLUG=%r is a free / level-0 tier and cannot be "
            "granted as an override; falling back to %r",
            slug,
            DEFAULT_OVERRIDE_TIER_SLUG,
        )
        return DEFAULT_OVERRIDE_TIER_SLUG
    return slug


def maven_override_duration_days():
    """Return the override lifetime in days (positive int)."""
    raw = get_config("MAVEN_OVERRIDE_DURATION_DAYS", DEFAULT_OVERRIDE_DURATION_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "MAVEN_OVERRIDE_DURATION_DAYS=%r is not an integer; using %d",
            raw,
            DEFAULT_OVERRIDE_DURATION_DAYS,
        )
        return DEFAULT_OVERRIDE_DURATION_DAYS
    if days <= 0:
        logger.warning(
            "MAVEN_OVERRIDE_DURATION_DAYS=%r must be positive; using %d",
            raw,
            DEFAULT_OVERRIDE_DURATION_DAYS,
        )
        return DEFAULT_OVERRIDE_DURATION_DAYS
    return days
