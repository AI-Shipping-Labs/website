"""HTTP endpoints for the shared comments app.

The comments app is generic: it stores ``Comment`` rows keyed by an
opaque ``content_id`` UUID and serves them through three endpoints
that have been live since the course Q&A surface shipped. Plan
discussion (issue #499) and Book Club note discussion (issue #1365)
reuse these same endpoints by passing ``Plan.comment_content_id`` /
``bookclub.Note.comment_content_id`` as the ``content_id``.

To keep the comments app oblivious to specific content kinds, the
domain-specific permission hooks are imported lazily inside a single
dispatch helper at the top of each request -- never at module load --
so the comments app does not have a static dependency on the plans or
bookclub apps and non-gated ``content_id`` UUIDs (course units,
workshop pages) keep their original public-read / authenticated-write
behaviour exactly.
"""

import json
from collections import namedtuple

from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.utils.display import display_name
from comments import services as comment_services
from comments.models import Comment, CommentVote

# A resolved gated thread: two predicates ``can_read(viewer)`` /
# ``can_write(viewer)`` already bound to the underlying domain object.
_GatedThread = namedtuple('_GatedThread', ['can_read', 'can_write'])


def _resolve_gated_thread(content_id):
    """Return a :class:`_GatedThread` for ``content_id``, or ``None``.

    A ``content_id`` may resolve to a plan thread (issue #499) or a Book Club
    note thread (issue #1365); both carry tier / visibility gating that the
    otherwise-generic comments app must honour. Non-gated UUIDs (course units,
    workshop pages) return ``None`` -- callers must treat that as "fall through
    to the default comments behaviour" (public read, authenticated write).

    The imports live inside the function so the ``comments`` app does not pull
    in ``plans`` / ``bookclub`` at module load.
    """
    from plans.comments_permissions import (  # noqa: PLC0415
        resolve_plan_for_content_id,
        viewer_can_read_plan_thread,
        viewer_can_write_plan_thread,
    )

    plan = resolve_plan_for_content_id(content_id)
    if plan is not None:
        return _GatedThread(
            can_read=lambda viewer: viewer_can_read_plan_thread(plan, viewer),
            can_write=lambda viewer: viewer_can_write_plan_thread(plan, viewer),
        )

    from bookclub.comments_permissions import (  # noqa: PLC0415
        resolve_book_note_for_content_id,
        viewer_can_read_book_note_thread,
        viewer_can_write_book_note_thread,
    )

    note = resolve_book_note_for_content_id(content_id)
    if note is not None:
        return _GatedThread(
            can_read=lambda viewer: viewer_can_read_book_note_thread(note, viewer),
            can_write=lambda viewer: viewer_can_write_book_note_thread(note, viewer),
        )

    return None


def comments_endpoint(request, content_id):
    """Dispatch GET/POST on /api/comments/<content_id>."""
    if request.method == 'GET':
        return list_comments(request, content_id)
    elif request.method == 'POST':
        return create_comment(request, content_id)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


def list_comments(request, content_id):
    """GET /api/comments/<content_id> - list comments for a content item.

    Returns top-level comments sorted by vote count desc, then created_at desc.
    Each comment includes its replies sorted by created_at asc.

    For gated threads (a UUID matching ``Plan.comment_content_id`` or
    ``bookclub.Note.comment_content_id``) the viewer must satisfy the
    thread's read predicate -- otherwise the request is rejected with
    404 so the existence of a private plan / tier-gated note does not
    leak. Non-gated UUIDs preserve existing behaviour.
    """
    gate = _resolve_gated_thread(content_id)
    if gate is not None and not gate.can_read(request.user):
        return JsonResponse({'error': 'Not found'}, status=404)

    top_level = (
        Comment.objects
        .filter(content_id=content_id, parent__isnull=True)
        .select_related('user')
        .annotate(vote_count=Count('votes'))
        .order_by('-vote_count', '-created_at')
    )

    # Collect voted comment IDs for the current user
    user_voted_ids = set()
    if request.user.is_authenticated:
        user_voted_ids = set(
            CommentVote.objects
            .filter(user=request.user, comment__content_id=content_id)
            .values_list('comment_id', flat=True)
        )

    comments_data = []
    for comment in top_level:
        replies = (
            comment.replies
            .select_related('user')
            .order_by('created_at')
        )
        replies_data = []
        for reply in replies:
            replies_data.append({
                'id': reply.id,
                'body': reply.body,
                'user_name': display_name(reply.user),
                'created_at': reply.created_at.isoformat(),
            })

        comments_data.append({
            'id': comment.id,
            'body': comment.body,
            'user_name': display_name(comment.user),
            'created_at': comment.created_at.isoformat(),
            'vote_count': comment.vote_count,
            'user_voted': comment.id in user_voted_ids,
            'replies': replies_data,
        })

    return JsonResponse({'comments': comments_data})


@require_POST
def create_comment(request, content_id):
    """POST /api/comments/<content_id> - create a top-level comment (question).

    For gated threads, write access is restricted: plan threads follow
    the plan visibility rules; Book Club note threads require the book's
    tier. Non-gated UUIDs keep the original "any authenticated user"
    rule. Anonymous writes are rejected with 401 before the gate runs.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    gate = _resolve_gated_thread(content_id)
    if gate is not None and not gate.can_write(request.user):
        return JsonResponse({'error': 'Not allowed'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    body = data.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'Body is required'}, status=400)

    comment = comment_services.create_comment(
        content_id=content_id,
        user=request.user,
        body=body,
    )

    return JsonResponse({
        'id': comment.id,
        'body': comment.body,
        'user_name': display_name(request.user),
        'created_at': comment.created_at.isoformat(),
        'vote_count': 0,
        'user_voted': False,
        'replies': [],
    }, status=201)


@require_POST
def reply_to_comment(request, comment_id):
    """POST /api/comments/<comment_id>/reply - create a reply to a comment.

    Gated threads (parent's ``content_id`` matches a plan or Book Club
    note) inherit the same write rules as ``create_comment``.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    try:
        parent = Comment.objects.get(pk=comment_id)
    except Comment.DoesNotExist:
        return JsonResponse({'error': 'Comment not found'}, status=404)

    # No nested replies: parent must be a top-level comment
    if parent.parent is not None:
        return JsonResponse({'error': 'Cannot reply to a reply'}, status=400)

    gate = _resolve_gated_thread(parent.content_id)
    if gate is not None and not gate.can_write(request.user):
        return JsonResponse({'error': 'Not allowed'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    body = data.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'Body is required'}, status=400)

    reply = comment_services.create_comment(
        content_id=parent.content_id,
        user=request.user,
        parent=parent,
        body=body,
    )

    return JsonResponse({
        'id': reply.id,
        'body': reply.body,
        'user_name': display_name(request.user),
        'created_at': reply.created_at.isoformat(),
    }, status=201)


@require_POST
def toggle_vote(request, comment_id):
    """POST /api/comments/<comment_id>/vote - toggle upvote on a top-level comment.

    Gated threads inherit their write rules: plan votes follow plan
    visibility, Book Club note votes require the book's tier. Non-gated
    threads keep the existing "authenticated user" rule.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    try:
        comment = Comment.objects.get(pk=comment_id)
    except Comment.DoesNotExist:
        return JsonResponse({'error': 'Comment not found'}, status=404)

    # Only top-level comments can be upvoted
    if comment.parent is not None:
        return JsonResponse({'error': 'Cannot vote on a reply'}, status=400)

    gate = _resolve_gated_thread(comment.content_id)
    if gate is not None and not gate.can_write(request.user):
        return JsonResponse({'error': 'Not allowed'}, status=403)

    vote, created = CommentVote.objects.get_or_create(
        comment=comment,
        user=request.user,
    )

    if not created:
        # Toggle off
        vote.delete()
        voted = False
    else:
        voted = True

    vote_count = CommentVote.objects.filter(comment=comment).count()

    return JsonResponse({'voted': voted, 'vote_count': vote_count})
