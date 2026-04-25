"""Utility functions for Studio views."""

import logging

logger = logging.getLogger(__name__)

# Maps Django model name (Python class name) to ContentSource.content_type.
# Used to look up the content_path prefix for GitHub URLs and to resolve the
# matching ContentSource for the per-object Re-sync button (issue #281).
MODEL_CONTENT_TYPE_MAP = {
    'Article': 'article',
    'Course': 'course',
    'Module': 'course',
    'Unit': 'course',
    'Project': 'project',
    'Download': 'resource',
    'CuratedLink': 'resource',
    'Event': 'event',
    'InterviewCategory': 'interview_question',
    # Workshop and WorkshopPage share one ContentSource entry
    # (content_type='workshop' on the workshops-content repo). The map lets
    # ``get_github_edit_url`` resolve the right ContentSource for both —
    # the content_path is empty in production but the lookup still needs the
    # mapping to find the right source row. See issue #297.
    'Workshop': 'workshop',
    'WorkshopPage': 'workshop',
}

# Backwards-compatibility alias: existing code (and one test) imports the
# private name ``_MODEL_CONTENT_TYPE_MAP``. Keep both bound to the same dict.
_MODEL_CONTENT_TYPE_MAP = MODEL_CONTENT_TYPE_MAP


def is_synced(obj):
    """Return True if the object is synced from a GitHub repo.

    An object is considered synced if its source_repo field is not
    None and not empty.
    """
    return bool(getattr(obj, 'source_repo', None))


def _get_content_path(obj):
    """Look up the content_path prefix for a synced object.

    Queries ContentSource to find the subdirectory prefix (e.g. 'blog')
    that should be prepended to source_path to form the full repo path.

    Returns the content_path string (may be empty), or empty string
    if the ContentSource cannot be found.
    """
    from integrations.models import ContentSource

    model_name = obj.__class__.__name__
    content_type = _MODEL_CONTENT_TYPE_MAP.get(model_name)
    if not content_type:
        logger.warning(
            'No content_type mapping for model %s; '
            'GitHub URL will use source_path as-is.',
            model_name,
        )
        return ''

    source_repo = getattr(obj, 'source_repo', None)
    if not source_repo:
        return ''

    try:
        source = ContentSource.objects.get(
            repo_name=source_repo,
            content_type=content_type,
        )
        return source.content_path or ''
    except ContentSource.DoesNotExist:
        logger.warning(
            'ContentSource not found for repo=%s content_type=%s; '
            'GitHub URL will use source_path as-is.',
            source_repo, content_type,
        )
        return ''


def get_github_edit_url(obj):
    """Build the GitHub edit URL for a synced object.

    Returns the URL to view/edit the source file on GitHub, or None
    if the object is not synced.

    The URL is constructed by combining:
    - source_repo: the GitHub org/repo (e.g. 'AI-Shipping-Labs/content')
    - content_path: subdirectory from ContentSource (e.g. 'blog')
    - source_path: file path relative to content_path (e.g. 'my-article.md')

    Result: https://github.com/AI-Shipping-Labs/content/blob/main/blog/my-article.md
    """
    if not is_synced(obj):
        return None
    source_path = getattr(obj, 'source_path', None)
    if not source_path:
        return None

    content_path = _get_content_path(obj)
    if content_path:
        full_path = f'{content_path}/{source_path}'
    else:
        full_path = source_path

    return f'https://github.com/{obj.source_repo}/blob/main/{full_path}'
