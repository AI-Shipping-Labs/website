from django import template
from django_q.models import OrmQ

from studio.worker_health import get_worker_status

register = template.Library()


LIST_TABLE_WRAPPER_CLASS = 'bg-card border border-border rounded-lg overflow-x-auto'
LIST_TABLE_CLASS = 'w-full'
LIST_TABLE_HEAD_CLASS = 'bg-secondary'
LIST_TABLE_HEAD_CELL_CLASS = (
    'text-left px-6 py-3 text-xs font-medium text-muted-foreground '
    'uppercase tracking-wider'
)
LIST_TABLE_HEAD_CELL_RIGHT_CLASS = (
    'text-right px-6 py-3 text-xs font-medium text-muted-foreground '
    'uppercase tracking-wider'
)
LIST_TABLE_BODY_CLASS = 'divide-y divide-border'
LIST_TABLE_ROW_CLASS = 'hover:bg-secondary/50 transition-colors'

STATUS_BADGE_CLASSES = {
    'published': 'bg-green-500/20 text-green-400',
    'draft': 'bg-yellow-500/20 text-yellow-400',
    'upcoming': 'bg-blue-500/20 text-blue-400',
    'completed': 'bg-secondary text-muted-foreground',
    'cancelled': 'bg-red-500/20 text-red-400',
}

STATUS_OPTIONS = {
    'publication': [
        ('draft', 'Draft'),
        ('published', 'Published'),
    ],
    'event': [
        ('draft', 'Draft'),
        ('upcoming', 'Upcoming'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ],
}


@register.filter
def dict_get(dictionary, key):
    """Look up a key in a dictionary. Returns None if key is missing."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@register.simple_tag
def studio_list_class(part='wrapper', align='left'):
    """Return shared class names for Studio content list tables."""
    if part == 'wrapper':
        return LIST_TABLE_WRAPPER_CLASS
    if part == 'table':
        return LIST_TABLE_CLASS
    if part == 'thead':
        return LIST_TABLE_HEAD_CLASS
    if part == 'th':
        if align == 'right':
            return LIST_TABLE_HEAD_CELL_RIGHT_CLASS
        return LIST_TABLE_HEAD_CELL_CLASS
    if part == 'tbody':
        return LIST_TABLE_BODY_CLASS
    if part == 'row':
        return LIST_TABLE_ROW_CLASS
    return ''


@register.inclusion_tag('studio/includes/list_filter_form.html')
def studio_list_filter(
    search='',
    status_filter='',
    placeholder='Search...',
    status_kind='publication',
    auto_submit=True,
):
    """Render the shared Studio list search/status filter form."""
    return {
        'search': search,
        'status_filter': status_filter,
        'placeholder': placeholder,
        'status_options': STATUS_OPTIONS.get(status_kind, STATUS_OPTIONS['publication']),
        'auto_submit': auto_submit,
    }


@register.inclusion_tag('studio/includes/status_badge.html')
def studio_status_badge(status, label=''):
    """Render a centralized Studio list status badge."""
    return {
        'label': label or str(status).title(),
        'classes': STATUS_BADGE_CLASSES.get(status, STATUS_BADGE_CLASSES['draft']),
    }


@register.inclusion_tag('studio/includes/synced_badge.html')
def studio_synced_badge(source_repo):
    """Render the shared synced badge when a row is source-managed."""
    return {'source_repo': source_repo}


@register.inclusion_tag('studio/includes/list_action.html')
def studio_list_action(href, label, kind='secondary', new_tab=False, rel=''):
    """Render shared Studio list action links."""
    return {
        'href': href,
        'label': label,
        'kind': kind,
        'new_tab': new_tab,
        'rel': rel,
        'testid': 'view-on-site' if label == 'View on site' else '',
    }


@register.filter
def model_name(obj):
    """Return the lowercase Django model name for ``obj``.

    Templates can't read ``obj._meta.model_name`` directly (any attribute
    starting with an underscore is blocked by the template engine), so
    expose the value via a filter. Used by the synced banner's Re-sync
    source button (issue #281) to build the ``/studio/sync/object/<model>/``
    URL without each include site having to hand-pass the model name.
    Returns an empty string for ``None`` or anything without an ``_meta``.
    """
    if obj is None:
        return ''
    meta = getattr(obj, '_meta', None)
    if meta is None:
        return ''
    return getattr(meta, 'model_name', '') or ''


def _sync_status_label(status, error_count=0):
    """Return the human-readable label for a sync status.

    The DB enum keeps ``partial`` (avoid migrations + stable test contracts)
    but operators see ``Completed with N error(s)`` instead — the word
    "partial" reads as "in progress", which it isn't (it means
    "success with N errors"). See issue #245.
    """
    if status == 'success':
        return 'success'
    if status == 'failed':
        return 'failed'
    if status == 'running':
        return 'running'
    if status == 'queued':
        return 'queued'
    if status == 'skipped':
        return 'skipped'
    if status == 'partial':
        try:
            n = int(error_count or 0)
        except (TypeError, ValueError):
            n = 0
        if n == 1:
            return 'Completed with 1 error'
        if n > 1:
            return f'Completed with {n} errors'
        # Defensive: status said partial but no error count surfaced.
        return 'Completed with errors'
    return status or ''


@register.filter
def sync_status_label(status, error_count=0):
    """Filter form of the sync-status human label.

    Usage in a template:
        {{ status|sync_status_label:error_count }}
    """
    return _sync_status_label(status, error_count)


@register.inclusion_tag('studio/includes/sync_status_pill.html')
def sync_status_pill(status, error_count=0, size='sm'):
    """Render the standard sync-status pill.

    Used by the sync dashboard, sync history, and the legacy admin sync
    pages so every surface renders ``partial`` the same way (amber pill,
    "Completed with N errors" label). Centralising the render keeps the
    label and color in lock-step across templates — see issue #245.
    """
    return {
        'status': status,
        'error_count': error_count or 0,
        'label': _sync_status_label(status, error_count),
        'size': size,
    }


@register.inclusion_tag('studio/includes/worker_status_inline.html')
def worker_status_inline():
    """Render the subtle inline worker-status indicator.

    Suitable for studio pages that submit jobs to the queue (sync, campaigns,
    notifications). Calls ``get_worker_status()`` and counts queue depth via
    ``OrmQ`` so the template stays declarative.
    """
    info = get_worker_status()
    queue_depth = 0
    if info['alive']:
        try:
            queue_depth = OrmQ.objects.count()
        except Exception:
            queue_depth = 0
    return {
        'worker_status': {
            'alive': info['alive'],
            'expect_worker': info['expect_worker'],
            'queue_depth': queue_depth,
        },
    }
