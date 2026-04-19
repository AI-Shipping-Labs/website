from django import template
from django_q.models import OrmQ

from studio.worker_health import get_worker_status

register = template.Library()


@register.filter
def dict_get(dictionary, key):
    """Look up a key in a dictionary. Returns None if key is missing."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


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
