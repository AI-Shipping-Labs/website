"""Dispatcher for the banner-generator auto-render pipeline (issue #788).

Called from the content-sync dispatchers (articles, courses, projects,
downloads, workshops) when a new record is created or its title changes.
Resolves the record, applies the short-circuit rules
(cover-image-wins, unchanged-title), and enqueues the actual render task
on django-q2 as fire-and-forget.

The enqueue path never raises and never blocks the caller — sync stays
fast even when banner-generator is misconfigured or the Lambda is
unreachable. Failures inside the rendered task itself are logged at
WARNING; see :mod:`integrations.services.banner_generator.tasks`.
"""

import hashlib
import logging

from integrations.services.banner_generator import is_enabled
from jobs.tasks.helpers import async_task
from jobs.tasks.names import build_task_name

logger = logging.getLogger(__name__)

# Content type slug -> ``content.models`` class lookup. Lazy import so
# this module can be imported during AppConfig wiring before content
# models are ready.
SUPPORTED_CONTENT_TYPES = (
    'article', 'course', 'project', 'download', 'workshop',
)

RENDER_TASK_PATH = (
    'integrations.services.banner_generator.tasks.render_banner_for_content'
)


def _get_model(content_type):
    """Resolve a content_type slug to its model class.

    Deferred import keeps ``integrations`` decoupled from content app
    readiness — same pattern as the github_sync dispatchers.
    """
    from content.models import Article, Course, Download, Project, Workshop

    mapping = {
        'article': Article,
        'course': Course,
        'project': Project,
        'download': Download,
        'workshop': Workshop,
    }
    return mapping.get(content_type)


def title_hash(title):
    """Return a stable sha256 hex digest of a title string.

    Used to detect title drift between syncs so we only re-render the
    banner when the title actually changed.
    """
    return hashlib.sha256((title or '').encode('utf-8')).hexdigest()


def enqueue_if_missing(content_type, content_pk):
    """Enqueue a banner render for a content record when needed.

    Short-circuit rules (any of these means we skip enqueuing):

    1. banner-generator is not configured (``is_enabled()`` False).
    2. Unknown ``content_type``.
    3. Record does not exist.
    4. Record already has a frontmatter-supplied ``cover_image_url``.
    5. Record already has an ``auto_banner_url`` AND its
       ``auto_banner_title_hash`` matches the current title.

    Otherwise enqueues
    ``integrations.services.banner_generator.tasks.render_banner_for_content``
    via :func:`jobs.tasks.helpers.async_task` and returns the task id.

    Never raises. Returns ``None`` on any short-circuit. The dispatcher
    hot path treats both branches the same — the sync is never blocked
    by banner state.
    """
    if not is_enabled():
        return None

    model = _get_model(content_type)
    if model is None:
        logger.warning(
            'banner_generator.enqueue_if_missing: unknown content_type=%r',
            content_type,
        )
        return None

    record = model.objects.filter(pk=content_pk).first()
    if record is None:
        logger.warning(
            'banner_generator.enqueue_if_missing: %s pk=%s not found',
            content_type, content_pk,
        )
        return None

    # Frontmatter wins — operator-shipped covers are never overwritten.
    if getattr(record, 'cover_image_url', '') or '':
        return None

    current_hash = title_hash(getattr(record, 'title', '') or '')
    existing_url = getattr(record, 'auto_banner_url', '') or ''
    existing_hash = getattr(record, 'auto_banner_title_hash', '') or ''
    if existing_url and existing_hash == current_hash:
        # Already rendered for this exact title; nothing to do.
        return None

    return async_task(
        RENDER_TASK_PATH,
        content_type,
        content_pk,
        task_name=build_task_name(
            'Render banner',
            f'{content_type} #{content_pk}',
            'content sync auto-banner',
        ),
    )


def enqueue_force(content_type, content_pk):
    """Enqueue a banner render regardless of cover/hash state.

    Used by the Studio "Regenerate banner" buttons — the operator
    explicitly asked for a re-render, so all short-circuits are
    bypassed except the "banner-generator not configured" guard and
    the unknown-content-type guard. Returns the task id or ``None``.
    """
    if not is_enabled():
        return None

    model = _get_model(content_type)
    if model is None:
        logger.warning(
            'banner_generator.enqueue_force: unknown content_type=%r',
            content_type,
        )
        return None

    if not model.objects.filter(pk=content_pk).exists():
        logger.warning(
            'banner_generator.enqueue_force: %s pk=%s not found',
            content_type, content_pk,
        )
        return None

    return async_task(
        RENDER_TASK_PATH,
        content_type,
        content_pk,
        task_name=build_task_name(
            'Render banner',
            f'{content_type} #{content_pk}',
            'studio regenerate button',
        ),
    )
