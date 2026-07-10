"""Utility functions for Studio views."""

import logging

from django.core.paginator import Paginator

logger = logging.getLogger(__name__)

STUDIO_LIST_PAGE_SIZE = 25


def coerce_page_number(raw, num_pages):
    """Clamp a raw ``?page=`` value to a valid 1-based page number."""
    try:
        page_num = int(raw)
    except (TypeError, ValueError):
        return 1

    last_page = max(int(num_pages), 1)
    if page_num < 1:
        return 1
    if page_num > last_page:
        return last_page
    return page_num


def studio_pager_querystring(request, page_number, *, page_param='page'):
    """Build a pager query string while preserving active list filters."""
    params = request.GET.copy()
    params[page_param] = str(page_number)
    return '?' + params.urlencode()


def studio_pagination_context(
    request,
    queryset,
    *,
    per_page=STUDIO_LIST_PAGE_SIZE,
    page_param='page',
):
    """Return a clamped Page and generic pager context for Studio lists."""
    paginator = Paginator(queryset, per_page)
    page_number = coerce_page_number(
        request.GET.get(page_param),
        paginator.num_pages or 1,
    )
    page = paginator.page(page_number)

    if page.has_previous():
        first_url = studio_pager_querystring(
            request, 1, page_param=page_param,
        )
        prev_url = studio_pager_querystring(
            request, page.previous_page_number(), page_param=page_param,
        )
    else:
        first_url = None
        prev_url = None

    if page.has_next():
        next_url = studio_pager_querystring(
            request, page.next_page_number(), page_param=page_param,
        )
        last_url = studio_pager_querystring(
            request, paginator.num_pages, page_param=page_param,
        )
    else:
        next_url = None
        last_url = None

    return {
        'page': page,
        'paginator': paginator,
        'show_pager': paginator.num_pages > 1,
        'pager_first_url': first_url,
        'pager_prev_url': prev_url,
        'pager_next_url': next_url,
        'pager_last_url': last_url,
        'page_start_index': page.start_index(),
        'page_end_index': page.end_index(),
        'filtered_total': paginator.count,
    }


def is_synced(obj):
    """Return True if the object is synced from a GitHub repo.

    For models that carry an explicit ``origin`` field (issue #564, e.g.
    ``Event``) the value of that field is authoritative — ``github``
    means synced, ``studio`` means database-native. For every other
    model the legacy ``source_repo`` fallback applies: an object is
    considered synced iff its ``source_repo`` is non-empty.
    """
    origin = getattr(obj, 'origin', None)
    if origin in ('github', 'studio'):
        return origin == 'github'
    return bool(getattr(obj, 'source_repo', None))


def get_github_edit_url(obj):
    """Build the GitHub edit URL for a synced object.

    Returns the URL to view/edit the source file on GitHub, or None
    if the object is not synced.

    Issue #310: with ``ContentSource.content_path`` removed, the model's
    ``source_path`` already carries the full repo-relative path, so the
    URL is simply ``https://github.com/<repo>/blob/main/<source_path>``.
    """
    if not is_synced(obj):
        return None
    source_path = getattr(obj, 'source_path', None)
    if not source_path:
        return None

    return f'https://github.com/{obj.source_repo}/blob/main/{source_path}'
