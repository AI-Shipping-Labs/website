from django.urls import path

from email_app.views.newsletter import (
    maven_email_opt_out,
    subscribe_api,
    subscribe_confirm_page,
    subscribe_page,
    unsubscribe_api,
    verify_and_subscribe_api,
)

# API endpoints (mounted at /api/ in project urls.py)
api_urlpatterns = [
    path('subscribe', subscribe_api, name='api_subscribe'),
    path('unsubscribe', unsubscribe_api, name='api_unsubscribe'),
    path('maven-email-opt-out', maven_email_opt_out, name='api_maven_email_opt_out'),
    # Issue #1593: verify + newsletter opt-in in one click. Deliberately a
    # sibling of /api/verify-email, which never subscribes anyone.
    path('verify-and-subscribe', verify_and_subscribe_api, name='api_verify_and_subscribe'),
]

# Page endpoints (mounted at root in project urls.py)
urlpatterns = [
    path('subscribe', subscribe_page, name='subscribe_page'),
    # A6.2 step 4: Relay's double opt-in confirm URL lands here. Relay's
    # SUBSCRIPTION_CONFIRM_BASE_URL must point at this page.
    path('subscribe/confirm', subscribe_confirm_page, name='subscribe_confirm'),
]
