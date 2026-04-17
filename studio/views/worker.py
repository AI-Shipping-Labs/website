"""Studio view for django-q2 worker status dashboard.

Shows worker health, queue depth, recent tasks, and failed task details.

Liveness is determined by ``django_q.status.Stat.get_all()`` (cluster
heartbeat), not by recent task activity — see ``studio.worker_health`` for
the rationale.
"""

from django.shortcuts import render
from django_q.models import OrmQ, Task

from studio.decorators import staff_required
from studio.worker_health import get_worker_status


@staff_required
def worker_status(request):
    """Display django-q2 worker status and recent task history."""
    worker_info = get_worker_status()

    # Recent tasks (last 50)
    recent_tasks = Task.objects.order_by('-started')[:50]

    # Success/failure counts
    success_count = Task.objects.filter(success=True).count()
    failure_count = Task.objects.filter(success=False).count()

    # Queue depth
    queue_depth = OrmQ.objects.count()

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
        failed_with_details.append({
            'task': task,
            'error_message': error_message,
        })

    return render(request, 'studio/worker.html', {
        'worker_info': worker_info,
        # Backwards-compatible aliases used by older template fragments / tests.
        'worker_alive': worker_info['alive'],
        'worker_idle': worker_info['idle'],
        'last_heartbeat_age': worker_info['last_heartbeat_age'],
        'cluster_count': worker_info['cluster_count'],
        'queue_depth': queue_depth,
        'success_count': success_count,
        'failure_count': failure_count,
        'tasks_with_duration': tasks_with_duration,
        'failed_with_details': failed_with_details,
    })
