"""Member pod URLs under course Home (issue #1918) and sprints (issue #1921).

Included from ``website/urls.py`` ahead of ``content.urls`` so these
specific paths win over the course unit catch-alls
(``courses/<course>/<module>/<unit>`` and the four/five-segment homework and
submodule routes), and ahead of ``plans.urls`` so the sprint pod paths are
registered before the generic ``sprints/<slug:sprint_slug>`` route.

Every course route has a sprint twin under ``sprints/<sprint_slug>/pods``
named ``sprint_<name>`` (the list is ``sprint_pods``); the same views serve
both and read ``slug`` or ``sprint_slug`` from the URL.
"""

from django.urls import path

from pods.views.artifacts import artifact_delete, artifact_detail, artifact_new
from pods.views.meetings import (
    meeting_cancel,
    meeting_held,
    meeting_move,
    meeting_new,
    meeting_respond,
    pod_call_link,
)
from pods.views.member import (
    availability_edit,
    pod_detail,
    pod_edit,
    pod_leave,
    pod_message_create,
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
    # Issue #1920: the pod chat.
    path('courses/<slug:slug>/home/pods/<int:pod_id>/messages', pod_message_create, name='pod_message_create'),
    # Issue #1922: meeting notes and transcripts.
    path('courses/<slug:slug>/home/pods/<int:pod_id>/notes/new', artifact_new, name='pod_artifact_new'),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/notes/<int:artifact_id>',
        artifact_detail,
        name='pod_artifact_detail',
    ),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/notes/<int:artifact_id>/delete',
        artifact_delete,
        name='pod_artifact_delete',
    ),
    # Issue #1919: agreed pod meetings and the pod call link.
    path('courses/<slug:slug>/home/pods/<int:pod_id>/call-link', pod_call_link, name='pod_call_link'),
    path('courses/<slug:slug>/home/pods/<int:pod_id>/meetings/new', meeting_new, name='pod_meeting_new'),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/meetings/<int:meeting_id>/respond',
        meeting_respond,
        name='pod_meeting_respond',
    ),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/meetings/<int:meeting_id>/move',
        meeting_move,
        name='pod_meeting_move',
    ),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/meetings/<int:meeting_id>/cancel',
        meeting_cancel,
        name='pod_meeting_cancel',
    ),
    path(
        'courses/<slug:slug>/home/pods/<int:pod_id>/meetings/<int:meeting_id>/held',
        meeting_held,
        name='pod_meeting_held',
    ),
    # Issue #1921: sprint pods, the same views under the sprint.
    path('sprints/<slug:sprint_slug>/pods', pods_list, name='sprint_pods'),
    path('sprints/<slug:sprint_slug>/pods/new', pod_new, name='sprint_pod_new'),
    path('sprints/<slug:sprint_slug>/pods/availability', availability_edit, name='sprint_pod_availability'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>', pod_detail, name='sprint_pod_detail'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/request', pod_request, name='sprint_pod_request'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/withdraw', pod_withdraw, name='sprint_pod_withdraw'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/leave', pod_leave, name='sprint_pod_leave'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/edit', pod_edit, name='sprint_pod_edit'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/requests/<int:request_id>/approve', pod_request_approve, name='sprint_pod_request_approve'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/requests/<int:request_id>/decline', pod_request_decline, name='sprint_pod_request_decline'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/slack', pod_slack, name='sprint_pod_slack'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/messages', pod_message_create, name='sprint_pod_message_create'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/call-link', pod_call_link, name='sprint_pod_call_link'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/meetings/new', meeting_new, name='sprint_pod_meeting_new'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/meetings/<int:meeting_id>/respond', meeting_respond, name='sprint_pod_meeting_respond'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/meetings/<int:meeting_id>/move', meeting_move, name='sprint_pod_meeting_move'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/meetings/<int:meeting_id>/cancel', meeting_cancel, name='sprint_pod_meeting_cancel'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/meetings/<int:meeting_id>/held', meeting_held, name='sprint_pod_meeting_held'),
    path('sprints/<slug:sprint_slug>/pods/<int:pod_id>/notes/new', artifact_new, name='sprint_pod_artifact_new'),
    path(
        'sprints/<slug:sprint_slug>/pods/<int:pod_id>/notes/<int:artifact_id>',
        artifact_detail,
        name='sprint_pod_artifact_detail',
    ),
    path(
        'sprints/<slug:sprint_slug>/pods/<int:pod_id>/notes/<int:artifact_id>/delete',
        artifact_delete,
        name='sprint_pod_artifact_delete',
    ),
]
