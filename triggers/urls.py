"""Front-end widget hydration + claim routes (issue #1070).

Mounted at the site root from ``website/urls.py`` so the JS hydration
fetches ``/widgets/<slug>/state`` and ``/widgets/<slug>/claim`` directly.
The Studio and API surfaces live in ``triggers/studio_urls.py`` and
``triggers/api_urls.py`` respectively.
"""

from django.urls import path

from triggers.views import widget_claim, widget_state

urlpatterns = [
    path("widgets/<slug:slug>/state", widget_state, name="event_widget_state"),
    path("widgets/<slug:slug>/claim", widget_claim, name="event_widget_claim"),
]
