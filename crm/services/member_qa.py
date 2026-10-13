"""Member Q&A context for the CRM contact record (issue #1949).

Staff reviewing a contact want to see what the member has been asking
and answering across the shared comment threads (course lessons,
homework steps, workshop pages, book club notes, sprint plans). This
module is read-only CRM context: it reuses the notification service's
thread resolvers so CRM links can never diverge from the deep links
the notification emails use, and no member-facing surface may render
its output.
"""

from comments.models import Comment
from notifications.services.notification_service import (
    HomeworkStepCommentContext,
)
from notifications.services.staff_comment_alerts import comment_preview

#: Latest comments shown in the record's Q&A section (issue #1949 AC10).
QA_LIMIT = 20


def _surface_label(content):
    """Short CRM label for the surface a comment thread lives on."""
    from bookclub.models import Note
    from content.models import Unit, WorkshopPage
    from plans.models import Plan

    if isinstance(content, HomeworkStepCommentContext):
        return 'Homework step'
    if isinstance(content, Unit):
        return 'Course lesson'
    if isinstance(content, WorkshopPage):
        return 'Workshop page'
    if isinstance(content, Note):
        return 'Book club note'
    if isinstance(content, Plan):
        return 'Sprint plan'
    return ''


def recent_member_qa(user, limit=QA_LIMIT):
    """Return the member's latest visible Q&A as CRM section context.

    Returns ``{'rows': [...], 'total': int}``. Hidden comments are
    excluded (moderation lives in the operator comments API); threads
    outside the three content families are skipped.
    """
    visible = (
        Comment.objects
        .filter(user=user, hidden_at__isnull=True)
        .order_by('-created_at', '-id')
    )
    total = visible.count()
    comments = list(visible.select_related('parent')[:limit])
    if not comments:
        return {'rows': [], 'total': total}

    from api.views.comments import _resolve_threads

    resolved = _resolve_threads({c.content_id for c in comments})
    rows = []
    for comment in comments:
        thread = resolved.get(comment.content_id)
        if thread is None or thread.content_type not in QA_THREAD_KINDS:
            continue
        rows.append({
            'comment': comment,
            'is_question': comment.parent_id is None,
            'snippet': _snippet(comment.body),
            'kind_label': _kind_label(thread),
            'thread_title': thread.context.get('title') or '',
            'thread_url': thread.context.get('url') or '',
            'created_at': comment.created_at,
        })
    return {'rows': rows, 'total': total}
