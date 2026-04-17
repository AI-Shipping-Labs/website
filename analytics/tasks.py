"""Background tasks for the analytics app."""

import logging

from django.core.cache import cache

from analytics.models import CampaignVisit
from integrations.models import UtmCampaign

logger = logging.getLogger(__name__)


CAMPAIGN_CACHE_TTL = 60 * 5      # 5 minutes for hits
CAMPAIGN_CACHE_MISS_TTL = 60     # 1 minute for misses (None values)
CAMPAIGN_CACHE_KEY = 'analytics:campaign:{slug}'
# Sentinel value for negative caching — django.core.cache treats `None`
# returned by `cache.get` as "key not present", so we cannot store None to
# represent "we already looked this up and it doesn't exist". Instead store
# this string, then translate it back to None when reading.
_CAMPAIGN_MISS_SENTINEL = '__campaign_miss__'


def _resolve_campaign_id(utm_campaign_slug):
    """Look up UtmCampaign.id by slug, with positive AND negative caching.

    Returns the integer PK if a UtmCampaign with that slug exists, else None.
    Both hits and misses are cached so the middleware can run on every request
    without hammering the DB.
    """
    if not utm_campaign_slug:
        return None
    key = CAMPAIGN_CACHE_KEY.format(slug=utm_campaign_slug)
    cached = cache.get(key)
    if cached == _CAMPAIGN_MISS_SENTINEL:
        return None
    if cached is not None:
        return cached
    # Cache miss — query the DB
    campaign_id = (
        UtmCampaign.objects
        .filter(slug=utm_campaign_slug)
        .values_list('id', flat=True)
        .first()
    )
    if campaign_id is None:
        cache.set(key, _CAMPAIGN_MISS_SENTINEL, CAMPAIGN_CACHE_MISS_TTL)
        return None
    cache.set(key, campaign_id, CAMPAIGN_CACHE_TTL)
    return campaign_id


def record_visit(
    utm_source='',
    utm_medium='',
    utm_campaign='',
    utm_content='',
    utm_term='',
    path='',
    referrer='',
    user_agent='',
    ip_hash='',
    anonymous_id='',
    user_id=None,
):
    """Persist a CampaignVisit row. Designed to be enqueued via jobs.async_task.

    All positional/keyword arguments are JSON-serializable so django-q2 can
    pickle them across processes.
    """
    campaign_id = _resolve_campaign_id(utm_campaign)
    visit = CampaignVisit.objects.create(
        campaign_id=campaign_id,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        utm_content=utm_content,
        utm_term=utm_term,
        path=path,
        referrer=referrer,
        user_agent=user_agent,
        ip_hash=ip_hash,
        anonymous_id=anonymous_id,
        user_id=user_id,
    )
    logger.debug('Recorded CampaignVisit id=%s utm_campaign=%s campaign_id=%s',
                 visit.id, utm_campaign, campaign_id)
    return visit.id
