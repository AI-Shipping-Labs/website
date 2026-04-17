from django.urls import path

from events.views.api import register_for_event, unregister_from_event
from events.views.pages import (
    event_detail,
    event_join_redirect,
    event_recap,
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
    path('events/<slug:slug>/join', event_join_redirect, name='event_join'),
    path('events/<slug:slug>/recap', event_recap, name='event_recap'),
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
