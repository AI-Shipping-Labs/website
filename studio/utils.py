"""Utility functions for Studio views."""

import logging

logger = logging.getLogger(__name__)


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
