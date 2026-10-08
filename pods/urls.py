"""Member pod URLs under course Home (issue #1918).

Included from ``website/urls.py`` ahead of ``content.urls`` so these
specific paths win over the course unit catch-alls
(``courses/<course>/<module>/<unit>`` and the four/five-segment homework and
submodule routes).
"""

from django.urls import path

from pods.views.member import (
    availability_edit,
    pod_detail,
    pod_edit,
    pod_leave,
    pod_new,
    pod_request,
    pod_request_approve,
    pod_request_decline,
    pod_slack,
    pod_withdraw,
    pods_list,
)

urlpatterns = [
    path('courses/<slug:slug>/home/pods', pods_list, name='course_pods'),
    path('courses/<slug:slug>/home/pods/new', pod_new, name='pod_new'),
    path('courses/<slug:slug>/home/pods/availability', availability_edit, name='pod_availability'),
    path('courses/<slug:slug>/home/pods/<int:pod_id>', pod_detail, name='pod_detail'),
    path('courses/<slug:slug>/home/pods/<int:pod_id>/request', pod_request, name='pod_request'),
    path('courses/<slug:slug>/home/pods/<int:pod_id>/withdraw', pod_withdraw, name='pod_withdraw'),
    path('courses/<slug:slug>/home/pods/<int:pod_id>/leave', pod_leave, name='pod_leave'),
    path('courses/<slug:slug>/home/pods/<int:pod_id>/edit', pod_edit, name='pod_edit'),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/requests/<int:request_id>/approve',
        pod_request_approve,
        name='pod_request_approve',
    ),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/requests/<int:request_id>/decline',
        pod_request_decline,
        name='pod_request_decline',
    ),
    path('courses/<slug:slug>/home/pods/<int:pod_id>/slack', pod_slack, name='pod_slack'),
]
