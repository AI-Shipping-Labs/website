"""tiers.yaml sync dispatcher."""

import os

import yaml

from integrations.services.github_sync.common import logger


def _sync_tiers_yaml(repo_dir):
    """Sync tiers.yaml from the repo root into SiteConfig."""
    tiers_path = os.path.join(repo_dir, 'tiers.yaml')
    if not os.path.isfile(tiers_path):
        return {'synced': False, 'count': 0}
    try:
        from content.models import SiteConfig

        with open(tiers_path, encoding='utf-8') as f:
            tiers_data = yaml.safe_load(f) or []
        SiteConfig.objects.update_or_create(
            key='tiers',
            defaults={'data': tiers_data},
        )
        logger.info('tiers.yaml synced to SiteConfig (%d tiers)', len(tiers_data))
        return {'synced': True, 'count': len(tiers_data)}
    except Exception as e:
        logger.warning('Failed to sync tiers.yaml: %s', e)
        return {'synced': False, 'count': 0}
