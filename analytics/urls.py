from django.urls import path

from analytics.views import analytics_consent_csrf, set_analytics_consent

urlpatterns = [
    path('api/analytics/consent/csrf', analytics_consent_csrf),
    path(
        'api/analytics/consent',
        set_analytics_consent,
        name='set_analytics_consent',
    ),
]
