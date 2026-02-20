from django.urls import path

from events.views.pages import events_list, event_detail
from events.views.api import register_for_event, unregister_from_event

urlpatterns = [
    path('events', events_list, name='events_list'),
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
