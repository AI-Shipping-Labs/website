"""Service helpers for intentional production comment writes."""

import logging

from django.utils import timezone

from accounts.utils.activation import mark_activated
from comments.models import Comment

logger = logging.getLogger(__name__)

#: Public values for ``Comment.moderation_state`` (issue #1894).
MODERATION_VISIBLE = 'visible'
MODERATION_HIDDEN = 'hidden'
MODERATION_STATES = (MODERATION_VISIBLE, MODERATION_HIDDEN)


def create_comment(*, content_id, user, body, parent=None):
    """Create a comment or reply and activate the posting user.

    Validation and permission checks stay at the HTTP boundary. This helper
    owns the side effects tied to a successful platform comment action:
    activation, the shared-thread in-app notifications (issues #1341,
    #1361, and #1365), and the staff Slack comment alert (issue #1926).
    """
    comment = Comment.objects.create(
        content_id=content_id,
        user=user,
        body=body,
        parent=parent,
    )
    mark_activated(user)
    _notify_comment_recipients(comment)
    _notify_staff_channel(comment)
    return comment


def _notify_staff_channel(comment):
    """Best-effort staff Slack alert for member comments (issue #1926).

    Runs separately from ``_notify_comment_recipients`` because the owner
    notifier returns early when a thread has no linked owner, while the
    team alert must fire regardless. Slack failures never affect the
    already-saved comment.
    """
    try:
        # Lazy for the same reason as ``_notify_comment_recipients``: the
        # generic comments app keeps no static dependency on notifications.
        from notifications.services.staff_comment_alerts import (  # noqa: PLC0415
            notify_staff_of_comment,
        )

        notify_staff_of_comment(comment)
    except Exception:
        logger.exception(
            'staff comment Slack alert failed for comment %s',
            comment.pk,
        )


def _notify_comment_recipients(comment):
    """Best-effort in-app notification for shared-thread recipients.

    Fires for both top-level comments and replies. Recipient resolution is
    centralized in :meth:`NotificationService.notify_content_comment`; this
    wrapper makes every resolver, URL, or notification failure non-fatal to
    the already-created comment and logs the affected comment id.
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


def edit_comment_body(comment, *, body):
    """Rewrite a comment body in place and stamp ``edited_at`` (issue #1894).

    Staff-only moderation write: no new row, no notification, and votes,
    authorship, and parentage are untouched. Body validation (trimmed,
    non-empty, at most 10000 code points) stays at the HTTP boundary,
    matching ``create_comment``.
    """
    comment.body = body
    comment.edited_at = timezone.now()
    comment.save(update_fields=['body', 'edited_at', 'updated_at'])
    logger.info(
        'comment %s edited by staff; edited_at=%s',
        comment.pk,
        comment.edited_at.isoformat(),
    )
    return comment


def hide_comment(comment):
    """Hide a comment from every public listing; idempotent (issue #1894).

    Soft delete: the row, its votes, and its replies remain untouched so
    ``restore_comment`` can bring them back. Sending no notification is a
    deliberate property of moderation, not an omission.
    """
    if comment.hidden_at is None:
        comment.hidden_at = timezone.now()
        comment.save(update_fields=['hidden_at', 'updated_at'])
        logger.info('comment %s hidden by staff', comment.pk)
    return comment


def restore_comment(comment):
    """Make a hidden comment visible again; idempotent (issue #1894).

    Restoring the parent brings back replies that were not independently
    hidden. Does not clear ``edited_at`` and does not notify anyone.
    """
    if comment.hidden_at is not None:
        comment.hidden_at = None
        comment.save(update_fields=['hidden_at', 'updated_at'])
        logger.info('comment %s restored by staff', comment.pk)
    return comment
