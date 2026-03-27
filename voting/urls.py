from django.urls import path

from voting.views.api import propose_option, vote_toggle
from voting.views.pages import poll_detail, poll_list

urlpatterns = [
    # Page views
    path('vote', poll_list, name='poll_list'),
    path('vote/<uuid:poll_id>', poll_detail, name='poll_detail'),
    # API endpoints
    path('api/vote/<uuid:poll_id>/vote', vote_toggle, name='vote_toggle'),
    path('api/vote/<uuid:poll_id>/propose', propose_option, name='propose_option'),
]
