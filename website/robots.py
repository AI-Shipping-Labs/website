"""Environment-aware root robots response."""

from django.http import HttpResponse
from django.views.decorators.http import require_GET

from integrations.config import site_base_url
from website.search_indexing import production_search_indexing_enabled


@require_GET
def robots_txt(request):
    """Allow crawling everywhere and advertise the sitemap only on prod."""
    lines = [
        'User-agent: *',
        'Allow: /',
    ]
    if production_search_indexing_enabled():
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
