"""Shared cache for public marketing-page route membership."""

from content.models.marketing_page import MarketingPage
from integrations.shared_cache import delete_shared_cache, get_shared_cache, set_shared_cache

PUBLISHED_MARKETING_PATHS_CACHE_KEY = 'content:published_marketing_paths:v1'
PUBLISHED_MARKETING_PATHS_CACHE_TTL = 300
_MISSING = object()


def published_marketing_page_paths():
    """Return published marketing paths, refreshing the shared cache as needed."""
    paths = get_shared_cache(PUBLISHED_MARKETING_PATHS_CACHE_KEY, _MISSING)
    if paths is not _MISSING:
        return frozenset(paths)
    paths = tuple(
        MarketingPage.objects.filter(status='published').values_list(
            'public_path', flat=True,
        )
    )
    set_shared_cache(
        PUBLISHED_MARKETING_PATHS_CACHE_KEY,
        paths,
        PUBLISHED_MARKETING_PATHS_CACHE_TTL,
    )
    return frozenset(paths)


def invalidate_published_marketing_page_paths():
    """Drop published path membership after a page changes."""
    delete_shared_cache(PUBLISHED_MARKETING_PATHS_CACHE_KEY)
