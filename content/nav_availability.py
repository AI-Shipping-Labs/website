"""Cached public navigation availability flags for content surfaces."""

from django.apps import apps
from django.core.cache.backends.base import InvalidCacheBackendError
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

from integrations.shared_cache import (
    get_shared_cache,
    set_shared_cache,
)

_PUBLIC_DOWNLOADS_CACHE_KEY = 'content:public_downloads_available:v1'
_MARKETING_NAV_CACHE_KEY = 'content:marketing_nav:v1'
_MARKETING_NAV_SECTIONS = ('about', 'community', 'resources')
_KB_WIKI_CACHE_KEY = 'content:kb_wiki_available:v1'
_KB_DOCS_CACHE_KEY = 'content:kb_docs_available:v1'
_UNSET = object()

_has_published_downloads = _UNSET
_marketing_nav = _UNSET
_kb_section_cache_keys = {
    'wiki': _KB_WIKI_CACHE_KEY,
    'docs': _KB_DOCS_CACHE_KEY,
}
_kb_nav = {
    'wiki': False,
    'docs': False,
}


def _read_shared_flag():
    try:
        return get_shared_cache(_PUBLIC_DOWNLOADS_CACHE_KEY, _UNSET)
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        return _UNSET


def _write_shared_flag(value):
    try:
        set_shared_cache(_PUBLIC_DOWNLOADS_CACHE_KEY, bool(value), None)
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
        return get_shared_cache(_MARKETING_NAV_CACHE_KEY, _UNSET)
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        return _UNSET


def _write_shared_marketing_nav(value):
    try:
        set_shared_cache(
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


def _read_kb_flag(section):
    try:
        return get_shared_cache(_kb_section_cache_keys[section], _UNSET)
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        return _UNSET


def _write_kb_flag(section, value):
    try:
        set_shared_cache(_kb_section_cache_keys[section], bool(value), None)
    except (InvalidCacheBackendError, ImproperlyConfigured, DatabaseError):
        pass


def refresh_knowledge_base_nav_cache():
    """Refresh the cached Wiki/Docs nav flags from the package pages.

    One refresh covers both sections so any writer (sync cleanup, Studio
    edits, post-migrate warmer) can keep the pair consistent. A missing
    knowledge base table (partial deploy before the package migration)
    reads as "no pages" instead of breaking the write path.
    """
    from community_base.knowledge_base.models import (  # noqa: PLC0415
        KnowledgeBasePage,
    )

    global _kb_nav
    for section in ('wiki', 'docs'):
        try:
            available = KnowledgeBasePage.objects.filter(
                section=section,
                status='published',
            ).exists()
        except DatabaseError:
            available = False
        _kb_nav[section] = available
        _write_kb_flag(section, available)
    return dict(_kb_nav)


def has_published_kb_pages_for_nav(section):
    """Return whether the public nav should expose one KB section's link.

    Like the Downloads flag this never queries the pages table; writers
    refresh the shared flags when published pages change.
    """
    cached = _read_kb_flag(section)
    if cached is not _UNSET:
        _kb_nav[section] = bool(cached)
        return _kb_nav[section]
    return _kb_nav[section]
