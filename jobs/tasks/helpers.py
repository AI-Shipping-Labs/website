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
"""

import logging

from django.conf import settings

from django_q.tasks import async_task as q_async_task
from django_q.models import Schedule

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


def schedule(func, cron=None, name=None, repeats=-1, **kwargs):
    """
    Register a recurring job schedule.

    Args:
        func: Dotted path to the function (e.g. 'jobs.tasks.cleanup.cleanup_old_webhook_logs').
        cron: Cron expression (e.g. '0 * * * *' for every hour).
        name: Human-readable name for the schedule. Defaults to the function path.
        repeats: Number of times to repeat. -1 means forever (default).
        **kwargs: Additional keyword arguments passed to the function.

    Returns:
        The Schedule object.
    """
    if cron is None:
        raise ValueError("cron expression is required for schedule()")

    schedule_name = name or str(func)

    # Update or create the schedule to avoid duplicates
    obj, created = Schedule.objects.update_or_create(
        name=schedule_name,
        defaults={
            'func': func if isinstance(func, str) else f'{func.__module__}.{func.__qualname__}',
            'schedule_type': Schedule.CRON,
            'cron': cron,
            'repeats': repeats,
            'kwargs': kwargs if kwargs else None,
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s recurring schedule: %s (%s)", action, schedule_name, cron)
    return obj
