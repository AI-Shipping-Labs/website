"""Studio mutation actions for study groups, meetings, chat, and artifacts."""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from pods.models import (
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_HELD,
    POD_SOURCE_STUDIO,
    Pod,
    PodArtifact,
    PodMeeting,
    PodMessage,
)
from pods.services import artifacts as artifact_svc
from pods.services import chat as chat_svc
from pods.services import meetings as mtg
from pods.services import membership as svc
from studio.decorators import staff_required
from studio.views.pod_detail import SCHEDULE_FORM_SESSION_KEY
from studio.views.pod_shared import store_results


def _detail_anchor(pod, anchor):
    return f"{reverse('studio_pod_detail', kwargs={'pod_id': pod.pk})}#{anchor}"


@staff_required
@require_POST
def studio_pod_meeting_schedule(request, pod_id):
    pod = get_object_or_404(Pod.objects.select_related("cohort__course"), pk=pod_id)
    form = {
        "date": request.POST.get("date", ""),
        "time": request.POST.get("time", ""),
        "timezone": request.POST.get("timezone", "") or "UTC",
        "repeat_weekly": bool(request.POST.get("repeat_weekly")),
    }
    try:
        zone_name = mtg.valid_zone(form["timezone"])
        start = mtg.parse_local(form["date"], form["time"], zone_name)
        created = mtg.schedule_meetings(
            pod,
            request.user,
            start,
            zone_name=zone_name,
            repeat_weekly=form["repeat_weekly"],
            created_via=POD_SOURCE_STUDIO,
        )
    except svc.PodError as exc:
        request.session[SCHEDULE_FORM_SESSION_KEY] = {**form, "error": exc.message}
    else:
        noun = "meetings"
        if len(created) == 1:
            noun = "meeting"
        messages.success(request, f"Scheduled {len(created)} {noun}.")
    return redirect(_detail_anchor(pod, "meetings"))


@staff_required
@require_POST
def studio_pod_meeting_status(request, pod_id, meeting_id):
    pod = get_object_or_404(Pod, pk=pod_id)
    meeting = get_object_or_404(PodMeeting, pk=meeting_id, pod=pod)
    status = request.POST.get("status", "")
    if status not in (MEETING_STATUS_HELD, MEETING_STATUS_CANCELLED):
        messages.error(request, "Choose held or cancelled.")
    else:
        try:
            mtg.set_meeting_status(meeting, request.user, status)
        except svc.PodError as exc:
            messages.error(request, exc.message)
        else:
            message = "Meeting cancelled."
            if status == MEETING_STATUS_HELD:
                message = "Meeting marked as held."
            messages.success(request, message)
    return redirect(_detail_anchor(pod, "meetings"))


@staff_required
@require_POST
def studio_pod_member_add(request, pod_id):
    pod = get_object_or_404(Pod.objects.select_related("cohort__course"), pk=pod_id)
    results = svc.add_members_by_email(pod, request.POST.get("emails", ""), actor=request.user, source="staff")
    store_results(request, pod, results)
    return redirect("studio_pod_detail", pod_id=pod.pk)


@staff_required
@require_POST
def studio_pod_member_remove(request, pod_id, user_id):
    pod = get_object_or_404(Pod.objects.select_related("cohort__course"), pk=pod_id)
    membership = get_object_or_404(pod.memberships.select_related("user"), user_id=user_id)
    svc.remove_member(pod, membership.user, actor=request.user)
    messages.success(request, f"Removed {membership.user.email}.")
    return redirect("studio_pod_detail", pod_id=pod.pk)


@staff_required
@require_POST
def studio_pod_request_retired(request, pod_id, request_id):
    return HttpResponse(svc.MSG_REQUESTS_RETIRED, status=410)


def _moderate(request, pod_id, message_id, action, success_message):
    pod = get_object_or_404(Pod, pk=pod_id)
    message = get_object_or_404(PodMessage, pk=message_id, pod=pod)
    action(message)
    messages.success(request, success_message)
    return redirect(_detail_anchor(pod, "chat"))


@staff_required
@require_POST
def studio_pod_message_hide(request, pod_id, message_id):
    return _moderate(
        request,
        pod_id,
        message_id,
        lambda message: chat_svc.hide_message(message, request.user),
        "Message hidden.",
    )


@staff_required
@require_POST
def studio_pod_message_restore(request, pod_id, message_id):
    return _moderate(request, pod_id, message_id, chat_svc.restore_message, "Message restored.")


@staff_required
@require_POST
def studio_pod_artifact_delete(request, pod_id, artifact_id):
    pod = get_object_or_404(Pod, pk=pod_id)
    artifact = get_object_or_404(PodArtifact, pk=artifact_id, pod=pod)
    artifact_svc.staff_delete(artifact)
    messages.success(request, artifact_svc.MSG_DELETED)
    return redirect(_detail_anchor(pod, "notes"))
