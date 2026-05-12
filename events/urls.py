from django.urls import path

from events.views.api import register_for_event, unregister_from_event
from events.views.pages import (
    event_calendar_ics,
    event_detail,
    event_join_redirect,
    event_series_public,
    events_calendar,
    events_list,
)

urlpatterns = [
    path('events', events_list, name='events_list'),
    path('events/calendar', events_calendar, name='events_calendar'),
    path(
        'events/calendar/<int:year>/<int:month>',
        events_calendar,
        name='events_calendar_month',
    ),
    # Issue #564: ``events/groups/<slug>`` is registered BEFORE
    # ``events/<slug>`` so the literal ``groups`` prefix isn't swallowed
    # by the slug converter on the event-detail route below. Same
    # pattern as ``workshops/resync/``.
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
    path('events/<slug:slug>/join', event_join_redirect, name='event_join'),
    path(
        'events/<slug:slug>/calendar.ics',
        event_calendar_ics,
        name='event_calendar_ics',
    ),
    path('events/<slug:slug>', event_detail, name='event_detail'),
    # API endpoints for registration
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
]
