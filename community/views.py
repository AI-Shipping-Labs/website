"""Community member-facing views."""

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render

from analytics.activity import record_activity
from analytics.models import UserActivity
from content.access import LEVEL_MAIN, get_user_level
from events.models import Event
from events.services.time_windows import past_window_q
from integrations.config import get_config

COMMUNITY_LAUNCH_TITLE = 'AI Shipping Labs Community Launch'
COMMUNITY_LAUNCH_SLUGS = (
    'ai-shipping-labs-community-launch',
    'community-launch',
)


def _published_launch_recap_events():
    return (
        Event.objects
        .filter(
            Q(slug__in=COMMUNITY_LAUNCH_SLUGS)
            | Q(title__iexact=COMMUNITY_LAUNCH_TITLE),
            published=True,
            status='completed',
        )
        .filter(past_window_q())
        .exclude(recap_html='')
    )


def _first_event_with_rendered_recap(queryset):
    for event in queryset:
        if (event.recap_html or '').strip():
            return event
    return None


def _resolve_community_launch_event():
    """Return the synced Community Launch event recap or raise 404."""
    base = _published_launch_recap_events()

    slug_match = _first_event_with_rendered_recap(
        base.filter(slug__in=COMMUNITY_LAUNCH_SLUGS).order_by(
            '-start_datetime',
            '-pk',
        )
    )
    if slug_match is not None:
        return slug_match

    title_match = _first_event_with_rendered_recap(
        base.filter(title__iexact=COMMUNITY_LAUNCH_TITLE).order_by(
            '-start_datetime',
            '-pk',
        )
    )
    if title_match is not None:
        return title_match

    raise Http404('Community launch recap not found.')


def community_landing(request):
    """Public landing page for the synced Community Launch recap."""
    event = _resolve_community_launch_event()
    return render(request, 'community/community_landing.html', {
        'event': event,
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
