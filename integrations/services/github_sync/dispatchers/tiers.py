"""tiers.yaml sync dispatcher."""

import os

import yaml
from django.db import DatabaseError

from integrations.services.github_sync.common import logger


def _sync_tiers_yaml(repo_dir):
    """Sync tiers.yaml from the repo root into SiteConfig."""
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

    logger.info('tiers.yaml synced to SiteConfig (%d tiers)', len(tiers_data))
    return {'synced': True, 'count': len(tiers_data)}
