"""HTTP endpoints for the shared comments app.

The comments app is generic: it stores ``Comment`` rows keyed by an
opaque ``content_id`` UUID and serves them through three endpoints
that have been live since the course Q&A surface shipped. Plan
discussion (issue #499) reuses these same endpoints by passing
``Plan.comment_content_id`` as the ``content_id``.

To keep the comments app oblivious to specific content kinds, the
plan-specific permission hook is imported lazily through a small
helper at the top of each request -- never at module load -- so the
comments app does not have a static dependency on the plans app and
non-plan ``content_id`` UUIDs (course units, workshop pages) keep
their original public-read / authenticated-write behaviour exactly.
"""

import json

from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from comments.models import Comment, CommentVote


def _resolve_plan_permissions(content_id):
    """Return a small dict describing plan-specific access for ``content_id``.

    Returns ``None`` when the ``content_id`` is not a plan thread --
    callers must treat that as "fall through to the default comments
    behaviour". Otherwise returns a dict with the plan and the
    boolean read/write predicates already evaluated for
    ``viewer``.

    The import lives inside the function so the ``comments`` app does
    not pull in ``plans`` at module load (and so a future test that
    swaps in a stub plans app stays cheap).
    """
    from plans.comments_permissions import resolve_plan_for_content_id  # noqa: PLC0415

    return resolve_plan_for_content_id(content_id)


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

    For plan threads (UUID matches ``Plan.comment_content_id``) the
    viewer must satisfy the plan's visibility predicate -- otherwise
    the request is rejected with 404 so the existence of a private
    plan does not leak. Non-plan UUIDs preserve existing behaviour.
    """
    plan = _resolve_plan_permissions(content_id)
    if plan is not None:
        from plans.comments_permissions import viewer_can_read_plan_thread  # noqa: PLC0415

        if not viewer_can_read_plan_thread(plan, request.user):
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
                'user_name': reply.user.first_name or reply.user.email.split('@')[0],
                'created_at': reply.created_at.isoformat(),
            })

        comments_data.append({
            'id': comment.id,
            'body': comment.body,
            'user_name': comment.user.first_name or comment.user.email.split('@')[0],
            'created_at': comment.created_at.isoformat(),
            'vote_count': comment.vote_count,
            'user_voted': comment.id in user_voted_ids,
            'replies': replies_data,
        })

    return JsonResponse({'comments': comments_data})


@require_POST
def create_comment(request, content_id):
    """POST /api/comments/<content_id> - create a top-level comment (question).

    For plan threads, write access is restricted: private plans
    accept writes only from staff; cohort plans accept writes from
    anyone who can view the plan. Non-plan UUIDs keep the original
    "any authenticated user" rule.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    plan = _resolve_plan_permissions(content_id)
    if plan is not None:
        from plans.comments_permissions import viewer_can_write_plan_thread  # noqa: PLC0415

        if not viewer_can_write_plan_thread(plan, request.user):
            return JsonResponse({'error': 'Not allowed'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    body = data.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'Body is required'}, status=400)

    comment = Comment.objects.create(
        content_id=content_id,
        user=request.user,
        body=body,
    )

    return JsonResponse({
        'id': comment.id,
        'body': comment.body,
        'user_name': request.user.first_name or request.user.email.split('@')[0],
        'created_at': comment.created_at.isoformat(),
        'vote_count': 0,
        'user_voted': False,
        'replies': [],
    }, status=201)


@require_POST
def reply_to_comment(request, comment_id):
    """POST /api/comments/<comment_id>/reply - create a reply to a comment.

    Plan threads (parent's ``content_id`` matches a
    ``Plan.comment_content_id``) inherit the same write rules as
    ``create_comment``: private plans need staff; cohort plans need
    visibility.
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

    plan = _resolve_plan_permissions(parent.content_id)
    if plan is not None:
        from plans.comments_permissions import viewer_can_write_plan_thread  # noqa: PLC0415

        if not viewer_can_write_plan_thread(plan, request.user):
            return JsonResponse({'error': 'Not allowed'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    body = data.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'Body is required'}, status=400)

    reply = Comment.objects.create(
        content_id=parent.content_id,
        user=request.user,
        parent=parent,
        body=body,
    )

    return JsonResponse({
        'id': reply.id,
        'body': reply.body,
        'user_name': request.user.first_name or request.user.email.split('@')[0],
        'created_at': reply.created_at.isoformat(),
    }, status=201)


@require_POST
def toggle_vote(request, comment_id):
    """POST /api/comments/<comment_id>/vote - toggle upvote on a top-level comment.

    Plan threads inherit the plan write rules: private-plan votes
    require staff, cohort-plan votes require visibility. Non-plan
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

    plan = _resolve_plan_permissions(comment.content_id)
    if plan is not None:
        from plans.comments_permissions import viewer_can_write_plan_thread  # noqa: PLC0415

        if not viewer_can_write_plan_thread(plan, request.user):
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
