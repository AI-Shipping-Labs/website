"""Environment-aware root robots response."""

from django.http import HttpResponse
from django.views.decorators.http import require_GET

from integrations.config import site_base_url
from website.search_indexing import (
    faceted_listing_robots_disallow_patterns,
    production_search_indexing_enabled,
    request_targets_dev_host,
)


@require_GET
def robots_txt(request):
    """Keep public pages crawlable while excluding private and facet URLs."""
    lines = [
        'User-agent: *',
        'Allow: /',
    ]
    # The shared Contabo container answers for both the production and
    # the dev hostnames with one configured origin. A dev-host request
    # must get the minimal non-production policy even when the
    # configuration still points at production; otherwise dev looks
    # indexable and advertises the production sitemap.
    if production_search_indexing_enabled() and not request_targets_dev_host(request):
        lines.extend([
            'Disallow: /accounts/',
            'Disallow: /register',
            'Disallow: /api/',
        ])
        lines.extend(
            f'Disallow: {pattern}'
            for pattern in faceted_listing_robots_disallow_patterns()
        )
        canonical_base = str(site_base_url()).strip().rstrip('/')
        lines.extend(('', f'Sitemap: {canonical_base}/sitemap.xml'))

    response = HttpResponse(
        '\n'.join(lines) + '\n',
        content_type='text/plain; charset=utf-8',
    )
    # SITE_BASE_URL can change at runtime through IntegrationSetting. Keep an
    # intermediary from retaining a robots response with the previous origin.
    response['Cache-Control'] = 'no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    return response
