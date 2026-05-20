"""
Helper functions for enqueuing async jobs and scheduling recurring tasks.

Usage:
    from jobs.tasks import async_task, schedule

    # Enqueue a one-off async job
    async_task('myapp.tasks.send_email', user_id=42)

    # Enqueue with retry configuration
    async_task('myapp.tasks.send_email', user_id=42, max_retries=3)

    # Schedule a recurring job
    schedule('myapp.tasks.cleanup', cron='0 * * * *')  # every hour

Task naming for scheduled fires
-------------------------------

Django-Q 1.x's scheduler (``django_q.scheduler.scheduler``) reads
``kwargs.get('q_options', {})`` from the Schedule row at fire time and
forwards it to ``async_task``. ``async_task`` in turn reads
``q_options['task_name']`` and writes it onto the resulting ``Task.name``
(see ``django_q/tasks.py:async_task`` ``keywords.pop("task_name", None) or
q_options.pop("task_name", None) or tag[0]``). When no ``task_name`` is
provided, Django-Q falls back to a random codename like
``texas-texas-oscar-earth`` derived from the task UUID.

To give every scheduled fire a descriptive ``Task.name``, the ``schedule``
helper writes ``q_options['task_name'] = <schedule-name>`` into the
Schedule's stored ``kwargs``. Every fire of the schedule then carries the
schedule's name through to the worker history.

Naming convention is a STATIC schedule name (e.g.
``slack-membership-refresh``). Django-Q 1.x's scheduler does not perform
template expansion of ``{ts}`` or any other placeholder in
``q_options['task_name']`` (verified by reading
``django_q/scheduler.py``), so any ``{ts}`` placeholder would land
verbatim in ``Task.name``. The fire timestamp is already visible on
``Task.started``, so duplicating it inside the name buys nothing.
"""

import logging

from django.conf import settings
from django_q.models import Schedule
from django_q.tasks import async_task as q_async_task

logger = logging.getLogger(__name__)


def async_task(func, *args, max_retries=None, retry_backoff=None, **kwargs):
    """
    Enqueue an async job for background execution.

    Args:
        func: Dotted path to the function (e.g. 'jobs.tasks.cleanup.cleanup_old_webhook_logs')
              or a callable.
        *args: Positional arguments passed to the function.
        max_retries: Maximum number of retry attempts on failure (default from settings).
        retry_backoff: Base backoff in seconds for exponential retry (default from settings).
        **kwargs: Keyword arguments passed to the function.

    Returns:
        The task ID string if enqueued successfully, or None if sync mode is active.
    """
    q_options = {}

    # Apply retry configuration
    q_config = getattr(settings, 'Q_CLUSTER', {})
    if max_retries is not None:
        q_options['max_attempts'] = max_retries + 1  # Django-Q2 counts attempts, not retries
    elif q_config.get('max_attempts'):
        q_options['max_attempts'] = q_config['max_attempts']

    if retry_backoff is not None:
        q_options['retry'] = retry_backoff
    elif q_config.get('retry'):
        q_options['retry'] = q_config['retry']

    # Check if we are in sync mode (useful for testing)
    if q_config.get('sync', False):
        # In sync mode, Django-Q executes tasks inline
        pass

    logger.info("Enqueuing task: %s args=%s kwargs=%s", func, args, kwargs)

    task_id = q_async_task(func, *args, q_options=q_options, **kwargs)
    return task_id


def schedule(func, cron=None, name=None, repeats=-1, preserve_disabled=False, **kwargs):
    """
    Register a recurring job schedule.

    The schedule's ``kwargs`` carry ``q_options['task_name'] = <name>`` so
    every fire of the schedule lands a descriptive ``Task.name`` in the
    worker history instead of a Django-Q random codename. See module
    docstring for the rationale and limits of the static-name convention.

    Args:
        func: Dotted path to the function (e.g. 'jobs.tasks.cleanup.cleanup_old_webhook_logs').
        cron: Cron expression (e.g. '0 * * * *' for every hour).
        name: Human-readable name for the schedule. Defaults to the function path.
        repeats: Number of times to repeat. -1 means forever (default).
        preserve_disabled: Keep existing disabled schedules disabled when updating.
        **kwargs: Additional keyword arguments passed to the function.

    Returns:
        The Schedule object.
    """
    if cron is None:
        raise ValueError("cron expression is required for schedule()")

    schedule_name = name or str(func)
    existing_repeats = None
    if preserve_disabled:
        existing_repeats = Schedule.objects.filter(name=schedule_name).values_list('repeats', flat=True).first()

    effective_repeats = 0 if preserve_disabled and existing_repeats == 0 else repeats

    # Inject the schedule's name into q_options.task_name so each fire
    # carries it through to Task.name. See module docstring for details.
    stored_kwargs = dict(kwargs) if kwargs else {}
    existing_q_options = stored_kwargs.get('q_options') or {}
    q_options = dict(existing_q_options)
    q_options['task_name'] = schedule_name
    stored_kwargs['q_options'] = q_options

    # Update or create the schedule to avoid duplicates
    obj, created = Schedule.objects.update_or_create(
        name=schedule_name,
        defaults={
            'func': func if isinstance(func, str) else f'{func.__module__}.{func.__qualname__}',
            'schedule_type': Schedule.CRON,
            'cron': cron,
            'repeats': effective_repeats,
            'kwargs': stored_kwargs,
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s recurring schedule: %s (%s)", action, schedule_name, cron)
    return obj
