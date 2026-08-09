"""Sitemap view rooted at the runtime canonical site URL."""

from types import SimpleNamespace
from urllib.parse import urlsplit

from django.contrib.sitemaps.views import _get_latest_lastmod, x_robots_tag
from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import EmptyPage, PageNotAnInteger
from django.http import Http404
from django.template.response import TemplateResponse
from django.utils.http import http_date

from integrations.config import site_base_url


def _canonical_site():
    """Return the protocol and Site-shaped domain for sitemap URL joins."""
    configured_url = str(site_base_url()).strip().rstrip('/')
    parsed = urlsplit(configured_url)
    if not parsed.scheme or not parsed.netloc:
        raise ImproperlyConfigured(
            'SITE_BASE_URL must be an absolute URL with a scheme and host.',
        )
    return parsed.scheme, SimpleNamespace(domain=parsed.netloc)


@x_robots_tag
def sitemap(
    request,
    sitemaps,
    section=None,
    template_name='sitemap.xml',
    content_type='application/xml',
):
    """Render Django sitemaps using ``SITE_BASE_URL`` as their origin.

    This intentionally mirrors Django's stock sitemap view. The only changed
    inputs are the protocol and domain passed to ``Sitemap.get_urls()``; item
    membership, paths, pagination, metadata, templates, and response headers
    retain Django's existing behavior.
    """
    protocol, canonical_site = _canonical_site()

    if section is not None:
        if section not in sitemaps:
            raise Http404(f'No sitemap available for section: {section!r}')
        maps = [sitemaps[section]]
    else:
        maps = sitemaps.values()
    page = request.GET.get('p', 1)

    lastmod = None
    all_sites_lastmod = True
    urls = []
    for site in maps:
        try:
            if callable(site):
                site = site()
            urls.extend(
                site.get_urls(
                    page=page,
                    site=canonical_site,
                    protocol=protocol,
                ),
            )
            if all_sites_lastmod:
                site_lastmod = getattr(site, 'latest_lastmod', None)
                if site_lastmod is not None:
                    lastmod = _get_latest_lastmod(lastmod, site_lastmod)
                else:
                    all_sites_lastmod = False
        except EmptyPage as exc:
            raise Http404(f'Page {page} empty') from exc
        except PageNotAnInteger as exc:
            raise Http404(f"No page {page!r}") from exc

    if all_sites_lastmod:
        headers = {'Last-Modified': http_date(lastmod.timestamp())} if lastmod else None
    else:
        headers = None
    return TemplateResponse(
        request,
        template_name,
        {'urlset': urls},
        content_type=content_type,
        headers=headers,
    )
