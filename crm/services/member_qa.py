"""Member Q&A context for the CRM contact record (issue #1949).

Staff reviewing a contact see what the member has been asking and
answering across the shared public threads (course units, homework
steps, workshop pages, book club notes). Read-only CRM context: it
reuses the notification service's thread resolvers so CRM links can
never diverge from the deep links the notifications deliver. No
member-facing surface may render its output.
"""

from django.db import models

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
        return "Homework step"
    if isinstance(content, Unit):
        return "Course unit"
    if isinstance(content, WorkshopPage):
        return "Workshop"
    if isinstance(content, Note):
        return "Book club note"
    return ""


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
    return qa_context_for_members([user], limit=limit).get(
        user.pk,
        {"rows": [], "total": 0},
    )


def qa_context_for_members(users, limit=QA_LIMIT):
    """Batched ``{user_id: {'rows': [...], 'total': n}}`` for a page.

    Same semantics as :func:`recent_member_qa` but resolved with one
    comments query and one count query for the whole page, so the CRM
    export's query budget stays flat as the member count grows.
    """
    users = list(users)
    if not users:
        return {}
    totals = dict(
        Comment.objects.filter(user__in=users, hidden_at__isnull=True)
        .values_list("user_id")
        .annotate(n=models.Count("id"))
    )
    contexts = {user.pk: {"rows": [], "total": totals.get(user.pk, 0)} for user in users}
    if not any(totals.values()):
        return contexts

    # Local import keeps the ordinary CRM page import graph free of the
    # notification service's content-app lookups until they are needed.
    from notifications.services.notification_service import (
        _resolve_commented_content,
        content_comment_urls,
    )
    from plans.models import Plan

    pending = {pk for pk, count in totals.items() if count}
    comments = (
        Comment.objects.filter(user__in=users, hidden_at__isnull=True)
        .select_related("parent", "parent__user")
        .order_by("-created_at")
    )
    for comment in comments:
        context = contexts.get(comment.user_id)
        if context is None or len(context["rows"]) >= limit:
            pending.discard(comment.user_id)
            if not pending:
                break
            continue
        content, content_title = _resolve_commented_content(
            comment.content_id,
        )
        if content is None or isinstance(content, Plan):
            continue
        urls = content_comment_urls(content)
        thread_url = urls[-1] if urls else ""
        context["rows"].append(
            {
                "comment": comment,
                "is_question": comment.parent_id is None,
                "kind_label": _surface_label(content),
                "snippet": comment_preview(comment.body),
                "thread_url": thread_url,
                "thread_title": content_title or "",
                "created_at": comment.created_at,
            }
        )
    return contexts
