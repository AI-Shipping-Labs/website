from django.urls import path

from events.views.api import (
    cancel_registration_action,
    register_for_event,
    series_registration,
    unregister_from_event,
)
from events.views.pages import (
    cancel_registration_page,
    event_calendar_ics,
    event_detail,
    event_detail_no_slug_redirect,
    event_feedback_submit,
    event_join_redirect,
    event_series_public,
    events_calendar,
    events_calendar_feed,
    events_list,
)

urlpatterns = [
    path('events', events_list, name='events_list'),
    # Issue #578: ``events/calendar.ics`` is registered BEFORE the
    # ``events/<slug>`` join/calendar.ics/cancel routes below so the
    # literal ``calendar.ics`` isn't swallowed by the slug converter.
    # Same pattern as ``events/groups/<slug>``.
    path(
        'events/calendar.ics',
        events_calendar_feed,
        name='events_calendar_feed',
    ),
    path('events/calendar', events_calendar, name='events_calendar'),
    path(
        'events/calendar/<int:year>/<int:month>',
        events_calendar,
        name='events_calendar_month',
    ),
    # Issue #564: ``events/groups/<slug>`` is registered BEFORE the
    # other slug routes so the literal ``groups`` prefix isn't swallowed
    # by the slug converter. Same pattern as ``workshops/resync/``.
    #
    # TODO(#575): the public URL still uses ``/events/groups/<slug>`` to
    # avoid breaking external bookmarks during the EventGroup ->
    # EventSeries rename. A follow-up issue can flip this to
    # ``/events/series/<slug>`` once we have data on whether external
    # links to the old path exist.
    path(
        'events/groups/<slug:slug>',
        event_series_public,
        name='event_series_public',
    ),
    path(
        'events/groups/<slug:slug>/',
        event_series_public,
        name='event_series_public_trailing',
    ),
    # Issue #673: slug-keyed sibling routes for join, .ics download, and
    # cancel-registration intentionally stay on ``events/<slug>/<verb>``.
    # They are not user-bookmarkable surfaces (URLs minted server-side
    # into emails and the registration card), so the cost of moving them
    # to id+slug is higher than the benefit. They must be registered
    # BEFORE the new ``events/<int:event_id>/<slug:slug>`` canonical
    # route below — a 3-segment URL like ``/events/foo/join`` should
    # always reach the join redirect, not be interpreted as an
    # event-detail attempt.
    path('events/<slug:slug>/join', event_join_redirect, name='event_join'),
    path(
        'events/<slug:slug>/calendar.ics',
        event_calendar_ics,
        name='event_calendar_ics',
    ),
    path(
        'events/<slug:slug>/cancel-registration',
        cancel_registration_page,
        name='event_cancel_registration',
    ),
    # Issue #679: post-event feedback submission. Registered BEFORE the
    # canonical ``events/<int:event_id>/<slug:slug>`` route below so the
    # literal ``feedback`` segment is not swallowed by the slug
    # converter. Same pattern as ``events/<slug>/join``.
    path(
        'events/<int:event_id>/<slug:slug>/feedback',
        event_feedback_submit,
        name='event_feedback_submit',
    ),
    # Issue #673: canonical event detail. ``event_id`` is the lookup key;
    # ``slug`` is purely cosmetic. The view verifies the slug matches the
    # stored value and 301s to the canonical form on a mismatch.
    path(
        'events/<int:event_id>/<slug:slug>',
        event_detail,
        name='event_detail',
    ),
    # Issue #673: id-only route redirects to the canonical id+slug URL.
    # ``/events/<id>/`` (with the trailing slash) is normalised first
    # by the site-wide ``RemoveTrailingSlashMiddleware`` (a 301 to
    # ``/events/<id>``), then this view 301s on to the canonical
    # id+slug URL — two cheap permanent redirects rather than a custom
    # trailing-slash variant.
    path(
        'events/<int:event_id>',
        event_detail_no_slug_redirect,
        name='event_detail_no_slug',
    ),
    # API endpoints for registration
    # Issue #857: series registration. Registered BEFORE the per-event
    # ``api/events/<slug>/register`` route so the literal ``series``
    # segment is never swallowed by the slug converter. POST registers
    # for the whole series (fan-out); DELETE drops the standing flag and
    # all future occurrences.
    path(
        'api/events/series/<slug:series_slug>/register',
        series_registration,
        name='event_series_register',
    ),
    path(
        'api/events/<slug:slug>/register',
        register_for_event,
        name='event_register',
    ),
    path(
        'api/events/<slug:slug>/unregister',
        unregister_from_event,
        name='event_unregister',
    ),
    path(
        'api/events/<slug:slug>/cancel-registration',
        cancel_registration_action,
        name='event_cancel_registration_action',
    ),
]
