"""Member-facing "Request a call" page (#870, Phase 1).

Authenticated members who have completed onboarding can request a 1:1
call with a host (Alexey or Valeria). Each host links out to their own
external scheduler. Availability is derived from the host's capacity
setting; unavailable hosts stay visible with a status, never hidden.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from community.models import CallHost
from questionnaires.onboarding import (
    can_access_onboarding,
    has_completed_onboarding,
)


@login_required
def request_a_call(request):
    """Render the request-a-call page.

    Gating:
    - Anonymous -> ``login_required`` redirects to login.
    - Authenticated but onboarding NOT complete -> show the onboarding
      gate with no booking links.
    - Authenticated AND onboarding complete -> show host cards.
    """
    onboarded = has_completed_onboarding(request.user)

    hosts = []
    any_available = False
    if onboarded:
        hosts = list(CallHost.objects.filter(is_active=True))
        any_available = any(host.is_available for host in hosts)

    # Issue #982: onboarding is paid-only. Only hand a "Finish onboarding"
    # CTA to a member who can actually enter the flow (effective tier >=
    # LEVEL_BASIC). A Free / expired-override member must never be pointed
    # at a flow they cannot use.
    context = {
        'onboarded': onboarded,
        'can_access_onboarding': can_access_onboarding(request.user),
        'hosts': hosts,
        'any_available': any_available,
    }
    return render(request, 'content/request_a_call.html', context)
