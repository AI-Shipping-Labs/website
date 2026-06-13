"""Community member-facing URLs (issue #953)."""

from django.urls import path

from community.views import slack_join_redirect

urlpatterns = [
    path('community/slack', slack_join_redirect, name='community_slack_join'),
]
