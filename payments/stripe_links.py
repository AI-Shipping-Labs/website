"""Runtime resolver for the public Stripe Payment Link matrix."""

import json
import logging

from django.conf import settings

from integrations.config import get_config

logger = logging.getLogger(__name__)

_TIERS = {'basic', 'main', 'premium'}
_PERIODS = {'monthly', 'annual'}


def _valid_payment_links(value):
    """Return whether *value* is the complete supported link matrix."""
    if not isinstance(value, dict) or set(value) != _TIERS:
        return False
    for links in value.values():
        if not isinstance(links, dict) or set(links) != _PERIODS:
            return False
        if any(not isinstance(link, str) or not link.strip() for link in links.values()):
            return False
    return True


def get_stripe_payment_links():
    """Resolve Stripe links through IntegrationSetting with a safe fallback."""
    resolved = get_config('STRIPE_PAYMENT_LINKS', settings.STRIPE_PAYMENT_LINKS)
    if isinstance(resolved, str):
        try:
            resolved = json.loads(resolved)
        except (TypeError, ValueError):
            resolved = None

    if _valid_payment_links(resolved):
        return resolved

    logger.warning(
        'Invalid STRIPE_PAYMENT_LINKS override; using Django settings fallback',
    )
    return settings.STRIPE_PAYMENT_LINKS
