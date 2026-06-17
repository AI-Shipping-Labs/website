"""Service helpers for intentional production comment writes."""

from accounts.utils.activation import mark_activated
from comments.models import Comment


def create_comment(*, content_id, user, body, parent=None):
    """Create a comment or reply and activate the posting user.

    Validation and permission checks stay at the HTTP boundary. This helper
    owns the side effect tied to a successful platform comment action.
    """
    comment = Comment.objects.create(
        content_id=content_id,
        user=user,
        body=body,
        parent=parent,
    )
    mark_activated(user)
    return comment
