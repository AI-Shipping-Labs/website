"""Owner and staff study-group settings page."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from pods.services import membership as svc
from pods.services.activity import activity_for_pod
from pods.services.pod_summary import public_pod
from pods.services.tab import pods_frame_context
from pods.views.member import LOGIN_URL, _form_status, _pod_for_viewer, _settings_form_context


def _initial_form(pod):
    return {
        "name": pod.name,
        "purpose": pod.purpose,
        "scheduling_note": pod.scheduling_note,
        "meeting_count": pod.meeting_count,
        "meeting_minutes": pod.meeting_minutes,
        "status": pod.status,
    }


def _submitted_form(request, pod):
    form = {key: request.POST.get(key, "") for key in _initial_form(pod)}
    if form["status"] not in ("open", "closed"):
        form["status"] = "open"
        if pod.status in ("open", "closed"):
            form["status"] = pod.status
    return form


def _save(request, pod, form):
    try:
        svc.update_pod(pod, form, actor=request.user, staff=False)
    except svc.PodError as exc:
        return exc.message, exc.field, None
    messages.success(request, "Study group updated.")
    return "", "", redirect(svc.pod_url(pod))


@login_required(login_url=LOGIN_URL)
def pod_edit(request, pod_id, slug=None, sprint_slug=None):
    """GET/POST ``.../pods/<pod_id>/edit`` -- owner or staff."""
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    if not svc.can_manage(pod, request.user):
        return HttpResponseForbidden("Only the study-group owner or staff can edit this group.")
    form = _initial_form(pod)
    error = error_field = ""
    if request.method == "POST":
        form = _submitted_form(request, pod)
        error, error_field, response = _save(request, pod, form)
        if response is not None:
            return response
    context = pods_frame_context(activity_for_pod(pod), request.user)
    context.update(_settings_form_context(form, error, error_field))
    context.update({"pod": public_pod(pod), "cancel_url": svc.pod_url(pod)})
    return render(request, "pods/edit.html", context, status=_form_status(error))
