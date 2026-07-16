"""Best-effort notification fan-out with observable failures."""

import logging

from .notification_service import NotificationService

logger = logging.getLogger(__name__)


def notify_safely(kind, pk):
    """Fan out a notification without rolling back the caller's mutation."""
    try:
        NotificationService.notify(kind, pk)
    except Exception:
        logger.exception('Notification fan-out failed for %s %s', kind, pk)
