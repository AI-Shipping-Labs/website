"""Community member-facing URLs (issue #953)."""

from django.urls import path
from django.views.generic import RedirectView

from community.views import slack_join_redirect

urlpatterns = [
    path(
        'community',
        RedirectView.as_view(pattern_name='home', permanent=True),
        name='community_landing',
    ),
    path('community/slack', slack_join_redirect, name='community_slack_join'),
]
