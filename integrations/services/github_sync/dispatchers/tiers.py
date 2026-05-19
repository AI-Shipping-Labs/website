"""tiers.yaml sync dispatcher."""

import os

import yaml
from django.db import DatabaseError

from integrations.services.github_sync.common import logger

# Per-tier fields synced onto payments.Tier rows (matched by stripe_key == Tier.slug).
# yaml-wins semantics: a non-empty yaml value overwrites the DB row, even if a
# different value was set via admin. An empty/missing yaml value leaves the DB
# row alone (no destructive overwrite). tiers.yaml is the source of truth from
# this point on.
_TIER_PRICE_ID_FIELDS = ('stripe_price_id_monthly', 'stripe_price_id_yearly')


def _sync_tier_price_ids(tiers_data):
    """Apply Stripe price IDs from tiers.yaml entries onto payments.Tier rows.

    For each yaml entry, look up payments.Tier by ``slug == entry['stripe_key']``
    and copy ``stripe_price_id_monthly`` / ``stripe_price_id_yearly`` when the
    yaml value is a non-empty string. Missing or empty yaml values leave the DB
    column unchanged. Unknown ``stripe_key`` values log a WARNING and are
    skipped without raising.

    Wrapped per-entry in try/except for DatabaseError so a transient DB hiccup
    on one row does not block the rest of the sync.
    """
    from payments.models import Tier

    for entry in tiers_data:
        if not isinstance(entry, dict):
            continue
        stripe_key = entry.get('stripe_key')
        if not stripe_key:
            continue

        try:
            tier = Tier.objects.filter(slug=stripe_key).first()
            if tier is None:
                logger.warning(
                    'tiers.yaml: stripe_key %r has no matching payments.Tier row',
                    stripe_key,
                )
                continue

            changed_fields = []
            for field in _TIER_PRICE_ID_FIELDS:
                value = entry.get(field)
                if not isinstance(value, str) or value == '':
                    continue
                if getattr(tier, field) != value:
                    setattr(tier, field, value)
                    changed_fields.append(field)

            if changed_fields:
                tier.save(update_fields=changed_fields)
        except DatabaseError as e:
            logger.warning(
                'tiers.yaml: failed to sync Tier %r price IDs: %s',
                stripe_key,
                e,
            )


def _sync_tiers_yaml(repo_dir):
    """Sync tiers.yaml from the repo root into SiteConfig and payments.Tier.

    The yaml list is written verbatim into SiteConfig['tiers'] (used by the
    pricing page renderer). Additionally, for each entry that carries a
    ``stripe_key`` matching a ``payments.Tier.slug``, the optional
    ``stripe_price_id_monthly`` / ``stripe_price_id_yearly`` fields are copied
    onto the Tier row. yaml-wins on collision with admin edits; empty/missing
    yaml values leave the DB row alone.
    """
    tiers_path = os.path.join(repo_dir, 'tiers.yaml')
    if not os.path.isfile(tiers_path):
        return {'synced': False, 'count': 0}

    from content.models import SiteConfig

    try:
        with open(tiers_path, encoding='utf-8') as f:
            tiers_data = yaml.safe_load(f) or []
    except OSError as e:
        logger.warning('Failed to read tiers.yaml: %s', e)
        return {'synced': False, 'count': 0}
    except yaml.YAMLError as e:
        logger.warning('Failed to parse tiers.yaml: %s', e)
        return {'synced': False, 'count': 0}

    if not isinstance(tiers_data, list):
        logger.warning(
            'Failed to sync tiers.yaml: expected a list, got %s',
            type(tiers_data).__name__,
        )
        return {'synced': False, 'count': 0}

    try:
        SiteConfig.objects.update_or_create(
            key='tiers',
            defaults={'data': tiers_data},
        )
    except DatabaseError as e:
        logger.warning('Failed to write tiers.yaml to SiteConfig: %s', e)
        return {'synced': False, 'count': 0}

    _sync_tier_price_ids(tiers_data)

    logger.info('tiers.yaml synced to SiteConfig (%d tiers)', len(tiers_data))
    return {'synced': True, 'count': len(tiers_data)}
