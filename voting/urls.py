from django.urls import path

from voting.views.pages import poll_list, poll_detail
from voting.views.api import vote_toggle, propose_option

urlpatterns = [
    # Page views
    path('vote', poll_list, name='poll_list'),
    path('vote/<uuid:poll_id>', poll_detail, name='poll_detail'),
    # API endpoints
    path('api/vote/<uuid:poll_id>/vote', vote_toggle, name='vote_toggle'),
    path('api/vote/<uuid:poll_id>/propose', propose_option, name='propose_option'),
]
