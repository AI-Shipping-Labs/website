"""Service helpers for intentional production comment writes."""

import logging

from accounts.utils.activation import mark_activated
from comments.models import Comment

logger = logging.getLogger(__name__)


def create_comment(*, content_id, user, body, parent=None):
    """Create a comment or reply and activate the posting user.

    Validation and permission checks stay at the HTTP boundary. This helper
    owns the side effects tied to a successful platform comment action:
    activation and the content-author in-app notification (issue #1341).
    """
    comment = Comment.objects.create(
        content_id=content_id,
        user=user,
        body=body,
        parent=parent,
    )
    mark_activated(user)
    _notify_content_author(comment)
    return comment


def _notify_content_author(comment):
    """Best-effort in-app notification to content authors (issue #1341).

    Fires for both top-level comments and replies. A failure inside the
    notification path must never fail the comment POST, so the call is
    wrapped and any exception is logged via ``logger.exception``.
    """
    try:
        # Imported lazily to keep the generic comments app free of a static
        # dependency on notifications (mirrors the lazy plans import in
        # comments/views/api.py).
        from notifications.services import NotificationService  # noqa: PLC0415

        NotificationService.notify_content_comment(comment)
    except Exception:
        logger.exception(
            'content_comment notification failed for comment %s',
            comment.pk,
        )
