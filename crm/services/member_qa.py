"""Member Q&A context for the CRM contact record (issue #1949).

Staff reviewing a contact see what the member has been asking and
answering across the shared public threads (course units, homework
steps, workshop pages, book club notes). Read-only CRM context: it
reuses the notification service's thread resolvers so CRM links can
never diverge from the deep links the notifications deliver. No
member-facing surface may render its output.
"""

from comments.models import Comment
from notifications.services.notification_service import (
    HomeworkStepCommentContext,
)
from notifications.services.staff_comment_alerts import comment_preview

#: Latest comments shown in the record's Q&A section (issue #1949 AC11).
QA_LIMIT = 20


def _surface_label(content):
    """Short context label for the surface a comment thread lives on."""
    from bookclub.models import Note
    from content.models import Unit, WorkshopPage

    if isinstance(content, HomeworkStepCommentContext):
        return 'Homework step'
    if isinstance(content, Unit):
        return 'Course unit'
    if isinstance(content, WorkshopPage):
        return 'Workshop'
    if isinstance(content, Note):
        return 'Book club note'
    return ''


def recent_member_qa(user, limit=QA_LIMIT):
    """Return the member's latest visible public-thread comments.

    Returns ``{"rows": [...], "total": N}``. Hidden comments
    (``hidden_at`` set) are excluded from rows and the total (AC11);
    comments whose thread does not resolve to a public surface --
    deleted content, unknown uuids, and member-owned sprint-plan
    threads -- stay out of the section entirely. Each row carries the
    question-vs-reply indicator (``is_question``), the surface context
    label (``kind_label``), a whitespace-collapsed body ``snippet``,
    the thread ``title``/``url`` (deep link to the public thread), and
    the timestamp.
    """
    comments = list(
        Comment.objects
        .filter(user=user, hidden_at__isnull=True)
        .select_related('parent', 'parent__user')
        .order_by('-created_at')[:limit]
    )
    total = Comment.objects.filter(
        user=user, hidden_at__isnull=True,
    ).count()
    if not comments:
        return {'rows': [], 'total': total}

    # Local import keeps the ordinary CRM page import graph free of the
    # notification service's content-app lookups until they are needed.
    from notifications.services.notification_service import (
        _resolve_commented_content,
        content_comment_urls,
    )
    from plans.models import Plan

    rows = []
    for comment in comments:
        content, content_title = _resolve_commented_content(
            comment.content_id,
        )
        if content is None or isinstance(content, Plan):
            continue
        urls = content_comment_urls(content)
        thread_url = urls[-1] if urls else ''
        rows.append({
            'comment': comment,
            'is_question': comment.parent_id is None,
            'kind_label': _surface_label(content),
            'snippet': comment_preview(comment.body),
            'thread_url': thread_url,
            'thread_title': content_title or '',
            'created_at': comment.created_at,
        })
    return {'rows': rows, 'total': total}
