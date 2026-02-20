from django.urls import path

from email_app.views.newsletter import (
    subscribe_api,
    subscribe_page,
    unsubscribe_api,
)

# API endpoints (mounted at /api/ in project urls.py)
api_urlpatterns = [
    path('subscribe', subscribe_api, name='api_subscribe'),
    path('unsubscribe', unsubscribe_api, name='api_unsubscribe'),
]

# Page endpoints (mounted at root in project urls.py)
urlpatterns = [
    path('subscribe', subscribe_page, name='subscribe_page'),
]
