"""Read-only selectors for surfacing `#plan-sprints` ingest data (issue #889).

Staff-only. No member-facing code may call these — the ingest data is
CRM-grade context like the rest of the app.
"""

from crm.models import SlackThread


def threads_for_member(user):
    """Captured `#plan-sprints` threads for a member, newest-first.

    Returns a queryset of :class:`SlackThread` with messages prefetched
    so templates can render the full thread without N+1 queries.
    """
    return (
        SlackThread.objects
        .filter(member=user)
        .prefetch_related('messages')
        .order_by('-posted_at')
    )


def threads_for_plan(plan):
    """Captured `#plan-sprints` threads linked to a specific plan."""
    return (
        SlackThread.objects
        .filter(plan=plan)
        .prefetch_related('messages')
        .order_by('-posted_at')
    )


def unmatched_threads():
    """Threads whose root author we could not match to a member, newest-first."""
    return (
        SlackThread.objects
        .filter(member__isnull=True)
        .prefetch_related('messages')
        .order_by('-posted_at')
    )
