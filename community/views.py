"""Community member-facing views.

Currently a single gated redirect endpoint (issue #953) that hands an
eligible Main/Premium member into the Slack workspace without ever
exposing the raw ``SLACK_INVITE_URL`` to ineligible users. Mirrors the
``event_join_redirect`` pattern in ``events/views/pages.py``.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from analytics.activity import record_activity
from analytics.models import UserActivity
from content.access import LEVEL_MAIN, get_user_level
from integrations.config import get_config


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
