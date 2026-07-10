"""Community member-facing URLs (issue #953)."""

from django.urls import path

from community.views import community_landing, slack_join_redirect

urlpatterns = [
    path('community', community_landing, name='community_landing'),
    path('community/slack', slack_join_redirect, name='community_slack_join'),
]
