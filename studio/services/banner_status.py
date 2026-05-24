"""Banner-generator task-status helper for Studio edit pages (issue #790).

Surfaces the most recent banner-render task per content record so the
operator can see whether the last attempt succeeded, failed, or is still
running. Read-on-render — no polling, no JS, no new model fields.

Lookup uses ``Task.name__icontains`` to match the per-record fragment
baked into ``build_task_name`` calls in
``integrations.services.banner_generator.dispatch``. ``Task.args`` is a
``PickledObjectField`` so ``args__contains`` does not work as a filter.
"""

import logging

from django.urls import reverse
from django_q.models import OrmQ, Task
from django_q.signing import SignedPackage

logger = logging.getLogger(__name__)

RENDER_TASK_PATH = (
    'integrations.services.banner_generator.tasks.render_banner_for_content'
)

RESULT_EXCERPT_MAX_LENGTH = 200


def _decode_ormq_payload(ormq):
    try:
        return SignedPackage.loads(ormq.payload)
    except Exception:  # pragma: no cover - defensive against signing-key drift
        logger.exception('Failed to decode OrmQ payload for row id=%s', ormq.pk)
        return None


def _args_match(args, content_type, content_pk):
    if not args or len(args) < 2:
        return False
    return args[0] == content_type and args[1] == content_pk


def _find_in_progress(content_type, content_pk):
    for ormq in OrmQ.objects.all():
        payload = _decode_ormq_payload(ormq)
        if not payload:
            continue
        if payload.get('func') != RENDER_TASK_PATH:
            continue
        if _args_match(payload.get('args'), content_type, content_pk):
            return ormq
    return None


def _clamp_excerpt(value):
    text = str(value or '').strip()
    if not text:
        return ''
    text = ' '.join(text.split())
    if len(text) > RESULT_EXCERPT_MAX_LENGTH:
        text = text[:RESULT_EXCERPT_MAX_LENGTH]
    return text


def get_last_banner_task(content_type, content_pk):
    """Return a small dict describing the most recent banner render task.

    Shape::

        {
            "state": "none" | "in_progress" | "success" | "failed",
            "started_at": datetime | None,
            "result_excerpt": str | None,
            "task_detail_url": str | None,
        }

    Lookup precedence: in-progress (``OrmQ``) overrides terminal history;
    the most recent ``Task`` row wins between ``success`` and ``failed``.
    """
    in_flight = _find_in_progress(content_type, content_pk)
    if in_flight is not None:
        return {
            'state': 'in_progress',
            'started_at': in_flight.lock,
            'result_excerpt': None,
            'task_detail_url': None,
        }

    fragment = f'{content_type} #{content_pk}'
    task = (
        Task.objects.filter(
            func=RENDER_TASK_PATH,
            name__icontains=fragment,
        )
        .order_by('-started')
        .first()
    )
    if task is None:
        return {
            'state': 'none',
            'started_at': None,
            'result_excerpt': None,
            'task_detail_url': None,
        }

    detail_url = reverse(
        'studio_worker_task_detail', kwargs={'task_id': task.id},
    )
    if task.success:
        return {
            'state': 'success',
            'started_at': task.started,
            'result_excerpt': None,
            'task_detail_url': detail_url,
        }
    return {
        'state': 'failed',
        'started_at': task.started,
        'result_excerpt': _clamp_excerpt(task.result) or None,
        'task_detail_url': detail_url,
    }
