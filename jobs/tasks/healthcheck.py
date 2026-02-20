"""
Health check task for verifying the job queue is working.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def health_check():
    """
    A no-op health check task that confirms the job queue is processing.

    Returns:
        dict with status and timestamp.
    """
    now = timezone.now().isoformat()
    logger.info("Health check task executed at %s", now)
    return {'status': 'ok', 'timestamp': now}
