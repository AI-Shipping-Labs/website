import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from content.access import can_access, get_user_level
from voting.models import Poll, PollOption, PollVote


@require_POST
def vote_toggle(request, poll_id):
    """Toggle a vote on a poll option.

    POST /api/vote/{poll_id}/vote with JSON body: {"option_id": "uuid"}

    If the user already voted for this option, removes the vote.
    If the user has not voted, adds the vote (respecting max_votes_per_user).

    Returns:
        200 on success (with action: "voted" or "unvoted")
        400 if max votes exceeded or invalid option
        401 if not authenticated
        403 if poll is closed or user lacks access
        404 if poll or option not found
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    poll = get_object_or_404(Poll, id=poll_id)

    # Check if poll is closed
    if poll.is_closed:
        return JsonResponse({'error': 'Poll is closed'}, status=403)

    # Check access level
    user_level = get_user_level(request.user)
    if poll.required_level > user_level:
        return JsonResponse({'error': 'Insufficient access level'}, status=403)

    # Parse request body
    try:
        body = json.loads(request.body)
        option_id = body.get('option_id')
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid request body'}, status=400)

    if not option_id:
        return JsonResponse({'error': 'option_id is required'}, status=400)

    # Verify option belongs to this poll
    option = get_object_or_404(PollOption, id=option_id, poll=poll)

    # Check if vote already exists (toggle behavior)
    existing_vote = PollVote.objects.filter(
        poll=poll, option=option, user=request.user,
    ).first()

    if existing_vote:
        # Remove the vote
        existing_vote.delete()
        return JsonResponse({
            'status': 'success',
            'action': 'unvoted',
            'option_id': str(option.id),
            'vote_count': option.vote_count,
        })

    # Check max votes
    current_vote_count = PollVote.objects.filter(
        poll=poll, user=request.user,
    ).count()

    if current_vote_count >= poll.max_votes_per_user:
        return JsonResponse({
            'error': f'Maximum {poll.max_votes_per_user} votes per poll',
        }, status=400)

    # Create the vote
    PollVote.objects.create(
        poll=poll, option=option, user=request.user,
    )

    return JsonResponse({
        'status': 'success',
        'action': 'voted',
        'option_id': str(option.id),
        'vote_count': option.vote_count,
    })


@require_POST
def propose_option(request, poll_id):
    """Propose a new option for a poll.

    POST /api/vote/{poll_id}/propose with JSON body: {"title": "...", "description": "..."}

    Returns:
        201 on success
        400 if title is missing
        401 if not authenticated
        403 if proposals not allowed or poll is closed
        404 if poll not found
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    poll = get_object_or_404(Poll, id=poll_id)

    # Check if poll is closed
    if poll.is_closed:
        return JsonResponse({'error': 'Poll is closed'}, status=403)

    # Check if proposals are allowed
    if not poll.allow_proposals:
        return JsonResponse({'error': 'Proposals are not allowed for this poll'}, status=403)

    # Check access level
    user_level = get_user_level(request.user)
    if poll.required_level > user_level:
        return JsonResponse({'error': 'Insufficient access level'}, status=403)

    # Parse request body
    try:
        body = json.loads(request.body)
        title = body.get('title', '').strip()
        description = body.get('description', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid request body'}, status=400)

    if not title:
        return JsonResponse({'error': 'Title is required'}, status=400)

    # Create the option
    option = PollOption.objects.create(
        poll=poll,
        title=title,
        description=description,
        proposed_by=request.user,
    )

    return JsonResponse({
        'status': 'success',
        'option_id': str(option.id),
        'title': option.title,
        'description': option.description,
    }, status=201)
