"""Worker liveness detection helpers.

Wraps ``django_q.status.Stat.get_all()`` so views and templates can answer
"is the django-q cluster alive right now?" without relying on the misleading
"any task completed in the last N minutes" proxy.

A running cluster writes a Stat record to the broker every few seconds. An
empty list from ``Stat.get_all()`` means no cluster process is running,
regardless of recent task activity.
"""

import logging
import os

from django.utils import timezone
from django_q.status import Stat

logger = logging.getLogger(__name__)


def expect_worker():
    """Return True if the deployment expects an async worker to be running.

    Set ``EXPECT_WORKER=false`` (env var) for one-off scripts or environments
    that don't need async — the banner and warnings are suppressed.
    Default: True.
    """
    return os.environ.get('EXPECT_WORKER', 'true').lower() != 'false'


def get_worker_status():
    """Return a dict describing the django-q cluster status.

    Keys:
        alive: bool                — at least one cluster heartbeat was found
        cluster_count: int         — number of running clusters
        last_heartbeat_age: float  — seconds since the most recent heartbeat,
                                     or None if no clusters
        idle: bool                 — cluster running but no busy workers
        clusters: list[dict]       — per-cluster summary with id, host,
                                     workers, status, heartbeat_age
        expect_worker: bool        — whether the deployment expects a worker

    Errors talking to the broker are logged and surfaced as ``alive=False``
    with ``error`` set, so the dashboard can show a clear failure state.
    """
    info = {
        'alive': False,
        'cluster_count': 0,
        'last_heartbeat_age': None,
        'idle': False,
        'clusters': [],
        'expect_worker': expect_worker(),
        'error': None,
    }

    try:
        clusters = Stat.get_all()
    except Exception as exc:
        logger.warning('Failed to query django-q cluster status: %s', exc)
        info['error'] = str(exc)
        return info

    if not clusters:
        return info

    now = timezone.now()
    cluster_summaries = []
    heartbeat_ages = []
    any_busy = False

    for cluster in clusters:
        timestamp = getattr(cluster, 'timestamp', None)
        if timestamp is not None:
            heartbeat_age = (now - timestamp).total_seconds()
        else:
            heartbeat_age = None
        if heartbeat_age is not None:
            heartbeat_ages.append(heartbeat_age)

        try:
            uptime = cluster.uptime() if cluster.tob else None
        except Exception:
            uptime = None

        worker_count = len(getattr(cluster, 'workers', []) or [])
        task_q_size = getattr(cluster, 'task_q_size', 0) or 0
        done_q_size = getattr(cluster, 'done_q_size', 0) or 0
        if task_q_size > 0 or done_q_size > 0:
            any_busy = True

        cluster_summaries.append({
            'cluster_id': getattr(cluster, 'cluster_id', ''),
            'host': getattr(cluster, 'host', ''),
            'pid': getattr(cluster, 'pid', None),
            'worker_count': worker_count,
            'status': getattr(cluster, 'status', ''),
            'heartbeat_age': heartbeat_age,
            'uptime': uptime,
            'task_q_size': task_q_size,
            'done_q_size': done_q_size,
        })

    info['alive'] = True
    info['cluster_count'] = len(clusters)
    info['last_heartbeat_age'] = (
        min(heartbeat_ages) if heartbeat_ages else None
    )
    info['idle'] = not any_busy
    info['clusters'] = cluster_summaries
    return info


def worker_is_alive():
    """Convenience wrapper: True if at least one cluster heartbeat is present."""
    return get_worker_status()['alive']
