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
