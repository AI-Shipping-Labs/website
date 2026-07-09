"""Cached public navigation availability flags for content surfaces."""

from django.apps import apps
from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

_CACHE_ALIAS = 'django_q'
_PUBLIC_DOWNLOADS_CACHE_KEY = 'content:public_downloads_available:v1'
_UNSET = object()

_has_published_downloads = _UNSET


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
