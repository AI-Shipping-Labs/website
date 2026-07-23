from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from content.access import build_gating_context, get_user_level
from voting.models import Poll


def poll_list(request):
    """List all open polls the user has access to."""
    user_level = get_user_level(request.user)

    # Get open polls the user has access to
    polls = Poll.objects.filter(status='open')

    # Annotate with vote counts for display
    accessible_polls = []
    for poll in polls:
        # Skip polls that have auto-closed
        if poll.is_closed:
            continue
        # Skip polls the user cannot access
        if poll.required_level > user_level:
            continue
        accessible_polls.append({
            'poll': poll,
            'options_count': poll.options_count,
            'total_votes': poll.total_votes,
        })

    context = {
        'polls': accessible_polls,
    }
    return render(request, 'voting/poll_list.html', context)


def poll_detail(request, poll_id):
    """Show poll detail with options sorted by vote count, vote/unvote toggle."""
    poll = get_object_or_404(Poll, id=poll_id)
    user_level = get_user_level(request.user)

    # Check access
    if poll.required_level > user_level:
        # Issue #1335: share the canonical gated-banner copy builder so the
        # poll paywall reads "Upgrade to {tier} to vote in this poll" with
        # the tier pill and Pricing CTA like every other gated surface.
        context = {
            'poll': poll,
            **build_gating_context(
                request.user,
                poll,
                'poll',
                resource_url=f'/vote/{poll.id}',
                gated_card_testid='poll-gated',
                gated_icon='lock',
                gated_cta_testid='poll-pricing-cta',
            ),
        }
        return render(request, 'voting/poll_detail.html', context)

    # Get options sorted by vote count descending
    options = poll.options.annotate(
        num_votes=Count('votes'),
    ).order_by('-num_votes', 'created_at')

    # Get the user's votes on this poll
    user_voted_option_ids = set()
    user_vote_count = 0
    if request.user.is_authenticated:
        user_votes = poll.votes.filter(user=request.user)
        user_voted_option_ids = set(user_votes.values_list('option_id', flat=True))
        user_vote_count = len(user_voted_option_ids)

    # Build annotated options for the template
    annotated_options = []
    for option in options:
        annotated_options.append({
            'option': option,
            'vote_count': option.num_votes,
            'user_voted': option.id in user_voted_option_ids,
        })

    is_closed = poll.is_closed
    can_vote = (
        request.user.is_authenticated
        and not is_closed
    )

    context = {
        'poll': poll,
        'options': annotated_options,
        'is_gated': False,
        'is_closed': is_closed,
        'can_vote': can_vote,
        'user_vote_count': user_vote_count,
        'max_votes': poll.max_votes_per_user,
        'votes_remaining': poll.max_votes_per_user - user_vote_count,
        'allow_proposals': poll.allow_proposals and not is_closed,
    }
    return render(request, 'voting/poll_detail.html', context)
