"""Resolved Maven integration config (issue #960).

Thin wrappers over ``integrations.config.get_config`` / ``is_enabled`` so
the webhook view, the handler, and the replay command all read the same
validated values. Never read raw env / ``settings`` for these — go through
here so Studio settings overrides take effect with no redeploy.
"""

import json
import logging

from integrations.config import get_config, is_enabled

logger = logging.getLogger(__name__)

DEFAULT_OVERRIDE_TIER_SLUG = "main"
DEFAULT_OVERRIDE_DURATION_DAYS = 1825

# Course-key -> contact-tag prefix. The broad ``maven`` tag is a module
# constant in ``integrations.services.maven`` because it is true of every
# Maven enrollee; this map is configuration because a second Maven course
# would need its own prefix (issue #1732).
DEFAULT_COURSE_TAG_PREFIXES = {"from-rag-to-agents": "ai-buildcamp"}


def maven_enabled():
    """Return True when the Maven auto-onboarding flow is switched on."""
    return is_enabled("MAVEN_ENROLLMENT_ENABLED")


def maven_shared_secret():
    """Return the configured shared secret (empty string when unset)."""
    return (get_config("MAVEN_WEBHOOK_SHARED_SECRET", "") or "").strip()


def maven_course_slack_channel():
    """Return the Slack channel name shown in the welcome email.

    Empty string when unset — the ``maven_welcome`` template branches on it
    and reads cleanly without a channel (issue #1565 / brief update). Read
    through ``get_config`` so it is editable from Studio with no redeploy.
    """
    return (get_config("MAVEN_COURSE_SLACK_CHANNEL", "") or "").strip()


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


def maven_course_tag_prefixes():
    """Return the ``course_key`` -> tag-prefix map, lowercased and validated.

    Reads the raw ``MAVEN_COURSE_TAG_PREFIXES`` value through ``get_config``
    (the config shim hands back a dict-typed DB value as a JSON string) and
    parses it. Invalid JSON, a non-object payload, or non-string members are
    logged once and fall back to the built-in default map rather than
    leaving enrollees untagged — same defensive shape as
    :func:`maven_override_duration_days`.
    """
    raw = get_config("MAVEN_COURSE_TAG_PREFIXES", "")
    if raw in ("", None):
        return dict(DEFAULT_COURSE_TAG_PREFIXES)
    parsed = raw
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except ValueError:
            logger.warning(
                "MAVEN_COURSE_TAG_PREFIXES is not valid JSON; using the "
                "built-in default course tag prefixes",
            )
            return dict(DEFAULT_COURSE_TAG_PREFIXES)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in parsed.items()
    ):
        logger.warning(
            "MAVEN_COURSE_TAG_PREFIXES must be a JSON object of "
            "course_key -> tag prefix strings; using the built-in default "
            "course tag prefixes",
        )
        return dict(DEFAULT_COURSE_TAG_PREFIXES)
    return {
        key.strip().lower(): value.strip()
        for key, value in parsed.items()
        if key.strip()
    }


def maven_course_tag_prefix(course_key):
    """Return the configured tag prefix for ``course_key``, or ``""``.

    ``course_key`` is matched case-insensitively, as Maven delivers it. An
    unmapped course is not an error: the caller still applies the broad
    ``maven`` tag and records that the mapping is missing.
    """
    key = (course_key or "").strip().lower()
    if not key:
        return ""
    return maven_course_tag_prefixes().get(key, "")
