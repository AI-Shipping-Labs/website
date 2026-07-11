"""Community member-facing views."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from analytics.activity import record_activity
from analytics.models import UserActivity
from content.access import LEVEL_MAIN, get_user_level
from content.tier_config import get_activities
from integrations.config import get_config

COMMUNITY_TIER_SUMMARIES = (
    {
        'name': 'Basic',
        'slug': 'basic',
        'icon': 'book-open',
        'fit': 'Self-directed learning',
        'summary': 'Use the content library and practical resources at your own pace.',
    },
    {
        'name': 'Main',
        'slug': 'main',
        'icon': 'users',
        'fit': 'Structure + accountability',
        'summary': (
            'Add the private community layer, live work, topic voting, '
            'and shipping support.'
        ),
    },
    {
        'name': 'Premium',
        'slug': 'premium',
        'icon': 'sparkles',
        'fit': 'Courses + profile feedback',
        'summary': (
            'Add mini-courses and focused resume, LinkedIn, and GitHub feedback.'
        ),
    },
)


def _build_tier_activity_summaries(activities):
    return [
        {
            **tier,
            'activities': [
                activity for activity in activities
                if tier['slug'] in activity.get('tiers', [])
            ],
        }
        for tier in COMMUNITY_TIER_SUMMARIES
    ]


def community_landing(request):
    """Public post-launch orientation page for the AI Shipping Labs community."""
    activities = get_activities()
    return render(request, 'community/community_landing.html', {
        'activities': activities,
        'tier_activity_summaries': _build_tier_activity_summaries(activities),
    })


@login_required
def slack_join_redirect(request):
    """Gate the Slack invite behind an authenticated, eligible session.

    Eligible (``get_user_level >= LEVEL_MAIN`` — covers Main, Premium,
    staff/superuser, and active ``TierOverride`` grants) members are
    302-redirected to ``SLACK_INVITE_URL`` and the click is recorded on
    their CRM timeline (#853). Everyone else (Free / Basic / expired)
    gets a 200 deny page and never sees the real invite URL.

    Anonymous users are bounced to login by ``@login_required`` and the
    eligibility check re-runs on the post-login request.
    """
    if get_user_level(request.user) >= LEVEL_MAIN:
        target = get_config('SLACK_INVITE_URL')
        if target:
            # Defensive — record_activity never raises into the redirect.
            record_activity(
                request.user,
                UserActivity.EVENT_SLACK_JOIN,
                label='Clicked Join Slack',
                object_type='community',
                object_id='slack',
            )
            return redirect(target)
        # Eligible but the invite URL is not configured — show the deny
        # page rather than 302 to an empty URL.
        return render(request, 'community/slack_join_denied.html', {
            'reason': 'unavailable',
        })

    return render(request, 'community/slack_join_denied.html', {
        'reason': 'ineligible',
    })
