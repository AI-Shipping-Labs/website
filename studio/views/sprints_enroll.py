"""Studio bulk-enroll page for sprint members (issue #443).

A staff operator pastes a newline-or-comma-separated list of emails;
the page returns four buckets: enrolled, already enrolled, under-tier
(still enrolled with a warning), unknown emails.

Under-tier members ARE enrolled. Bulk-enroll is the backfill path for
people we have already committed to (a markdown plan exists). The
self-join path still hard-rejects under-tier members; only the bulk
path is permissive, and the warning bucket makes that explicit.
"""

import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, render

from content.access import LEVEL_TO_TIER_NAME, get_user_level
from plans.models import Sprint, SprintEnrollment
from studio.decorators import staff_required

User = get_user_model()

_SPLIT_RE = re.compile(r'[\s,]+')


def _split_emails(raw):
    """Parse the textarea blob into a deduplicated list of lowercase emails.

    Split on commas AND any whitespace (including newlines). Empty
    tokens are discarded; case is normalised so the operator can paste
    mixed-case lists from a spreadsheet.
    """
    if not raw:
        return []
    seen = set()
    out = []
    for token in _SPLIT_RE.split(raw):
        cleaned = token.strip().lower()
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _classify_and_enroll(emails, sprint, request_user):
    """Classify each email and persist enrollments where applicable.

    Returns a dict with four lists (the same shape the API endpoint
    returns and the template renders):

    - ``enrolled``: newly created rows (or under-tier rows -- they're
      also created and ALSO appear in ``under_tier``).
    - ``already_enrolled``: idempotent skips.
    - ``under_tier``: subset of either ``enrolled`` or
      ``already_enrolled`` whose user_level was below
      ``sprint.min_tier_level`` at classification time. Surfaces a
      warning to the operator.
    - ``unknown_emails``: addresses with no matching User row; not
      enrolled.
    """
    users_by_email = {
        u.email.lower(): u
        for u in User.objects.filter(email__in=emails)
    }
    existing = set(
        SprintEnrollment.objects.filter(
            sprint=sprint, user__email__in=emails,
        ).values_list('user__email', flat=True)
    )
    existing_lower = {e.lower() for e in existing}

    enrolled = []
    already_enrolled = []
    under_tier = []
    unknown_emails = []

    for email in emails:
        user = users_by_email.get(email)
        if user is None:
            unknown_emails.append(email)
            continue
        if email in existing_lower:
            already_enrolled.append(email)
        else:
            SprintEnrollment.objects.create(
                sprint=sprint, user=user, enrolled_by=request_user,
            )
            enrolled.append(email)
        if get_user_level(user) < sprint.min_tier_level:
            under_tier.append(email)

    return {
        'enrolled': enrolled,
        'already_enrolled': already_enrolled,
        'under_tier': under_tier,
        'unknown_emails': unknown_emails,
    }


@staff_required
def sprint_bulk_enroll(request, sprint_id):
    """Render the bulk-enroll form and process submissions."""
    sprint = get_object_or_404(Sprint, pk=sprint_id)

    raw = ''
    results = None

    if request.method == 'POST':
        raw = request.POST.get('emails', '') or ''
        emails = _split_emails(raw)
        if emails:
            results = _classify_and_enroll(emails, sprint, request.user)
            messages.success(
                request,
                'Enrolled {n} ({m} skipped already-enrolled, '
                '{k} with tier warning, {u} unknown).'.format(
                    n=len(results['enrolled']),
                    m=len(results['already_enrolled']),
                    k=len(results['under_tier']),
                    u=len(results['unknown_emails']),
                ),
            )
        else:
            messages.error(request, 'Paste at least one email address.')

    enrollment_count = SprintEnrollment.objects.filter(sprint=sprint).count()
    required_tier_name = LEVEL_TO_TIER_NAME.get(
        sprint.min_tier_level, 'Premium',
    )

    return render(
        request,
        'studio/sprints/enroll.html',
        {
            'sprint': sprint,
            'raw_emails': raw,
            'results': results,
            'enrollment_count': enrollment_count,
            'required_tier_name': required_tier_name,
        },
    )
