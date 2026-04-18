"""Studio view for django-q2 worker status dashboard.

Shows worker health, queue depth, recent tasks, and failed task details, and
exposes operational actions for queue/task recovery.

Liveness is determined by ``django_q.status.Stat.get_all()`` (cluster
heartbeat), not by recent task activity — see ``studio.worker_health`` for
the rationale.

Operational actions:

* Drain the queue (delete every pending ``OrmQ`` row).
* Inspect / delete a single queued task.
* Retry / delete a single failed task.
* Bulk retry / bulk delete failed tasks.

Content-sync triggers live on ``/studio/sync/`` — they don't belong on the
worker page.
"""

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_q.models import OrmQ, Task
from django_q.tasks import async_task

from studio.decorators import staff_required
from studio.worker_health import get_worker_status

logger = logging.getLogger(__name__)


def _safe_task_field(ormq, attr, default=None):
    """Return ``ormq.<attr>()`` swallowing pickle/signing errors.

    ``OrmQ.task`` decodes the signed pickle payload. If the SECRET_KEY rotated
    or the row is malformed, ``OrmQ.task`` returns ``{"id": "*<ExceptionName>"}``
    and field accessors return ``None``. We keep the dashboard rendering even
    in that edge case.
    """
    try:
        value = getattr(ormq, attr)
        return value() if callable(value) else value
    except Exception:  # pragma: no cover - defensive
        logger.exception('Failed to read OrmQ.%s for row id=%s', attr, ormq.pk)
        return default


def _ormq_summary(ormq, now=None):
    """Build a serialisable summary dict for a queued task.

    ``OrmQ.lock`` is the per-worker claim-expiry timestamp (set to
    ``timezone.now() + retry_after`` when a worker tries to claim the row),
    NOT a queued-at timestamp — django-q2's OrmQ schema doesn't persist when
    a row was first enqueued. We translate ``lock`` into one of three states
    so the template can render an honest "Lock expires" column:

    * ``future``   — ``lock > now``: a worker holds the claim; lock will
      expire in ``lock_seconds`` (positive countdown).
    * ``expired``  — ``lock <= now``: the claim expired ``lock_seconds`` ago
      and the row is awaiting reclaim by a worker.
    * ``unlocked`` — ``lock IS NULL``: nothing has touched the row yet.
    """
    now = now or timezone.now()
    lock_state = 'unlocked'
    lock_seconds = None
    if ormq.lock is not None:
        delta = (ormq.lock - now).total_seconds()
        if delta > 0:
            lock_state = 'future'
            lock_seconds = delta
        else:
            lock_state = 'expired'
            lock_seconds = -delta
    return {
        'id': ormq.pk,
        'key': ormq.key,
        'task_id': _safe_task_field(ormq, 'task_id'),
        'name': _safe_task_field(ormq, 'name'),
        'func': _safe_task_field(ormq, 'func'),
        'group': _safe_task_field(ormq, 'group'),
        'args': _safe_task_field(ormq, 'args'),
        'kwargs': _safe_task_field(ormq, 'kwargs'),
        'q_options': _safe_task_field(ormq, 'q_options'),
        'lock': ormq.lock,
        'lock_state': lock_state,
        'lock_seconds': lock_seconds,
    }


@staff_required
def worker_status(request):
    """Display django-q2 worker status and recent task history."""
    worker_info = get_worker_status()

    # Recent tasks (last 50)
    recent_tasks = Task.objects.order_by('-started')[:50]

    # Success/failure counts
    success_count = Task.objects.filter(success=True).count()
    failure_count = Task.objects.filter(success=False).count()

    # Queue depth + per-task summaries for the inspect / delete UI
    queued = OrmQ.objects.all().order_by('pk')
    queue_depth = queued.count()
    now = timezone.now()
    queued_summaries = [_ormq_summary(q, now=now) for q in queued]

    # Failed tasks with error details (last 20)
    failed_tasks = Task.objects.filter(success=False).order_by('-started')[:20]

    # Compute duration for recent tasks
    tasks_with_duration = []
    for task in recent_tasks:
        duration = None
        if task.started and task.stopped:
            duration = task.stopped - task.started
        error_message = None
        if not task.success and task.result is not None:
            error_message = str(task.result)
        tasks_with_duration.append({
            'task': task,
            'duration': duration,
            'error_message': error_message,
        })

    failed_with_details = []
    for task in failed_tasks:
        error_message = str(task.result) if task.result is not None else 'No error details'
        # Pick the most informative one-line summary for the collapsed row.
        # For traceback-shaped results (``traceback.format_exc()`` output) the
        # first line is the literal ``Traceback (most recent call last):``
        # banner, which is identical for every failure and tells operators
        # nothing — the exception class + message lives on the LAST non-blank
        # line. For other result strings (custom error wrappers, plain
        # messages) the first non-blank line is still the most useful summary.
        nonblank_lines = [line.strip() for line in error_message.splitlines() if line.strip()]
        if not nonblank_lines:
            summary_line = 'No error details'
        elif (
            error_message.startswith('Traceback')
            or '\nTraceback (most recent call last):' in error_message
        ):
            summary_line = nonblank_lines[-1]
        else:
            summary_line = nonblank_lines[0]
        if len(summary_line) > 160:
            summary_line = summary_line[:157] + '...'
        failed_with_details.append({
            'task': task,
            'error_message': error_message,
            'error_summary': summary_line,
        })

    return render(request, 'studio/worker.html', {
        'worker_info': worker_info,
        # Backwards-compatible aliases used by older template fragments / tests.
        'worker_alive': worker_info['alive'],
        'worker_idle': worker_info['idle'],
        'last_heartbeat_age': worker_info['last_heartbeat_age'],
        'cluster_count': worker_info['cluster_count'],
        'queue_depth': queue_depth,
        'queued_tasks': queued_summaries,
        'success_count': success_count,
        'failure_count': failure_count,
        'tasks_with_duration': tasks_with_duration,
        'failed_with_details': failed_with_details,
    })


@staff_required
def worker_inspect_task(request, ormq_id):
    """Show func/args/kwargs/lock state for a single queued task."""
    ormq = get_object_or_404(OrmQ, pk=ormq_id)
    summary = _ormq_summary(ormq)
    return render(request, 'studio/worker_inspect.html', {
        'task': summary,
    })


@staff_required
@require_POST
def worker_drain_queue(request):
    """Delete every pending ``OrmQ`` row.

    Used when the queue piled up with stale duplicates and we want a clean
    slate. Reports the number of rows that were deleted in the flash message.
    """
    deleted, _ = OrmQ.objects.all().delete()
    if deleted:
        messages.success(
            request,
            f'Drained queue: deleted {deleted} pending task'
            f'{"" if deleted == 1 else "s"}.',
        )
    else:
        messages.info(request, 'Queue is already empty.')
    return redirect('studio_worker')


@staff_required
@require_POST
def worker_delete_queued(request, ormq_id):
    """Delete a single queued ``OrmQ`` row."""
    ormq = get_object_or_404(OrmQ, pk=ormq_id)
    name = _safe_task_field(ormq, 'name') or f'#{ormq.pk}'
    ormq.delete()
    messages.success(request, f'Deleted queued task: {name}.')
    return redirect('studio_worker')


def _resubmit_failed(task):
    """Re-enqueue a failed Task with the same func/args/kwargs.

    Mirrors django-q's own admin ``resubmit_task`` action: enqueues a fresh
    job with the same func/args/kwargs, then deletes the failed Task row so
    it doesn't keep reappearing in the failed list.
    """
    async_task(
        task.func,
        *(task.args or ()),
        hook=task.hook,
        group=task.group,
        cluster=task.cluster,
        **(task.kwargs or {}),
    )
    task.delete()


@staff_required
@require_POST
def worker_retry_failed(request, task_id):
    """Re-enqueue a single failed task and delete the failure row."""
    task = Task.objects.filter(pk=task_id, success=False).first()
    if task is None:
        raise Http404('Failed task not found')
    name = task.name or task_id
    try:
        _resubmit_failed(task)
    except Exception as exc:
        logger.exception('Retry failed for task %s', task_id)
        messages.error(request, f'Could not retry {name}: {exc}')
        return redirect('studio_worker')
    messages.success(request, f'Re-queued failed task: {name}.')
    return redirect('studio_worker')


@staff_required
@require_POST
def worker_delete_failed(request, task_id):
    """Delete a single failed task row."""
    task = Task.objects.filter(pk=task_id, success=False).first()
    if task is None:
        raise Http404('Failed task not found')
    name = task.name or task_id
    task.delete()
    messages.success(request, f'Deleted failed task: {name}.')
    return redirect('studio_worker')


@staff_required
@require_POST
def worker_bulk_retry_failed(request):
    """Re-enqueue every failed task and delete the corresponding failure rows."""
    failed = list(Task.objects.filter(success=False))
    if not failed:
        messages.info(request, 'No failed tasks to retry.')
        return redirect('studio_worker')
    requeued = 0
    errors = 0
    for task in failed:
        try:
            _resubmit_failed(task)
            requeued += 1
        except Exception:
            logger.exception('Bulk retry failed for task %s', task.pk)
            errors += 1
    if errors:
        messages.warning(
            request,
            f'Re-queued {requeued} failed task'
            f'{"" if requeued == 1 else "s"}; {errors} could not be re-queued.',
        )
    else:
        messages.success(
            request,
            f'Re-queued {requeued} failed task'
            f'{"" if requeued == 1 else "s"}.',
        )
    return redirect('studio_worker')


@staff_required
@require_POST
def worker_bulk_delete_failed(request):
    """Delete every failed task row."""
    deleted, _ = Task.objects.filter(success=False).delete()
    if deleted:
        messages.success(
            request,
            f'Deleted {deleted} failed task'
            f'{"" if deleted == 1 else "s"}.',
        )
    else:
        messages.info(request, 'No failed tasks to delete.')
    return redirect('studio_worker')
