from django.urls import path

from analytics.views import set_analytics_consent

urlpatterns = [
    path(
        'api/analytics/consent',
        set_analytics_consent,
        name='set_analytics_consent',
    ),
]
