"""Cached public navigation availability flags for content surfaces."""

from django.apps import apps
from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

_CACHE_ALIAS = 'django_q'
_PUBLIC_DOWNLOADS_CACHE_KEY = 'content:public_downloads_available:v1'
_MARKETING_NAV_CACHE_KEY = 'content:marketing_nav:v1'
_MARKETING_NAV_SECTIONS = ('about', 'community', 'resources')
_UNSET = object()

_has_published_downloads = _UNSET
_marketing_nav = _UNSET


def _read_shared_flag():
    try:
        return caches[_CACHE_ALIAS].get(_PUBLIC_DOWNLOADS_CACHE_KEY, _UNSET)
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        return _UNSET


def _write_shared_flag(value):
    try:
        caches[_CACHE_ALIAS].set(_PUBLIC_DOWNLOADS_CACHE_KEY, bool(value), None)
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        pass


def _empty_marketing_nav():
    return {section: [] for section in _MARKETING_NAV_SECTIONS}


def _normalize_marketing_nav(nav):
    return {
        section: list(nav.get(section, []))
        for section in _MARKETING_NAV_SECTIONS
    }


def _read_shared_marketing_nav():
    try:
        return caches[_CACHE_ALIAS].get(_MARKETING_NAV_CACHE_KEY, _UNSET)
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        return _UNSET


def _write_shared_marketing_nav(value):
    try:
        caches[_CACHE_ALIAS].set(
            _MARKETING_NAV_CACHE_KEY,
            _normalize_marketing_nav(value),
            None,
        )
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        pass


def set_published_downloads_nav_available(value):
    """Set the cached public Downloads nav availability flag."""
    global _has_published_downloads
    _has_published_downloads = bool(value)
    _write_shared_flag(_has_published_downloads)
    return _has_published_downloads


def refresh_published_downloads_nav_cache():
    """Refresh the public Downloads nav flag from the Download table."""
    download_model = apps.get_model('content', 'Download')
    has_downloads = download_model.objects.filter(published=True).exists()
    return set_published_downloads_nav_available(has_downloads)


def has_published_downloads_for_nav():
    """Return whether the public header should expose the Downloads link.

    This never queries the Download table. Writers refresh the shared flag
    when published downloads change; public renders only read that flag.
    """
    global _has_published_downloads
    cached = _read_shared_flag()
    if cached is not _UNSET:
        _has_published_downloads = bool(cached)
        return _has_published_downloads
    if _has_published_downloads is _UNSET:
        _has_published_downloads = False
    return _has_published_downloads


def set_marketing_pages_nav(value):
    """Set the cached public marketing-page navigation groups."""
    global _marketing_nav
    _marketing_nav = _normalize_marketing_nav(value)
    _write_shared_marketing_nav(_marketing_nav)
    return _marketing_nav


def refresh_marketing_pages_nav_cache():
    """Refresh marketing-page navigation groups from published pages."""
    page_model = apps.get_model('content', 'MarketingPage')
    nav = _empty_marketing_nav()
    for page in page_model.objects.filter(
        status='published',
    ).exclude(nav_section='none').order_by('nav_section', 'nav_order', 'title'):
        if page.nav_section in nav:
            nav[page.nav_section].append(page)
    return set_marketing_pages_nav(nav)


def get_marketing_pages_nav():
    """Return cached marketing-page navigation groups for public headers.

    Public renders should not query ``MarketingPage``. Writers and
    post-migrate warmers refresh the shared cache whenever rows change.
    """
    global _marketing_nav
    cached = _read_shared_marketing_nav()
    if cached is not _UNSET:
        _marketing_nav = _normalize_marketing_nav(cached)
        return _marketing_nav
    if _marketing_nav is _UNSET:
        _marketing_nav = _empty_marketing_nav()
    return _marketing_nav
