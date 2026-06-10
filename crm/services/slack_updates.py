"""Read-only selectors for surfacing `#plan-sprints` ingest data (issue #889).

Staff-only. No member-facing code may call these — the ingest data is
CRM-grade context like the rest of the app.
"""

from crm.models import SlackThread

# Prefetch the auto-applied progress event + its change rows alongside each
# thread so the Phase-2 panel renders the summary/blockers/undo controls
# without N+1 queries. There is at most one current event per thread.
_PROGRESS_PREFETCH = (
    'progress_events',
    'progress_events__changes',
    'progress_events__changes__checkpoint',
    'progress_events__changes__deliverable',
    'progress_events__changes__next_step',
)


def threads_for_member(user):
    """Captured `#plan-sprints` threads for a member, newest-first.

    Returns a queryset of :class:`SlackThread` with messages and the
    auto-applied progress event prefetched so templates can render the full
    thread + Phase-2 controls without N+1 queries.
    """
    return (
        SlackThread.objects
        .filter(member=user)
        .prefetch_related('messages', *_PROGRESS_PREFETCH)
        .order_by('-posted_at')
    )


def threads_for_plan(plan):
    """Captured `#plan-sprints` threads linked to a specific plan."""
    return (
        SlackThread.objects
        .filter(plan=plan)
        .prefetch_related('messages', *_PROGRESS_PREFETCH)
        .order_by('-posted_at')
    )


def progress_event_for_thread(thread):
    """The current auto-applied :class:`IngestedProgressEvent`, or None.

    Read-only selector. Honours a prefetched ``progress_events`` cache to
    avoid an extra query per thread when the thread came from
    :func:`threads_for_member` / :func:`threads_for_plan`.
    """
    events = list(thread.progress_events.all())
    return events[0] if events else None


def unmatched_threads():
    """Threads whose root author we could not match to a member, newest-first."""
    return (
        SlackThread.objects
        .filter(member__isnull=True)
        .prefetch_related('messages')
        .order_by('-posted_at')
    )
