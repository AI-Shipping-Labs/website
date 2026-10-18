"""Member mutations for study groups."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from pods.services import chat as chat_svc
from pods.services import membership as svc
from pods.services.people import is_slack_eligible
from pods.services.slack import clean_channel_url
from pods.views.member import DRAFT_MAX_CHARS, LOGIN_URL, _chat_session_key, _pod_for_viewer

# --- Actions -----------------------------------------------------------------


def _redirect_with_error(request, pod, message):
    messages.error(request, message)
    return redirect(svc.pod_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_join(request, pod_id, slug=None, sprint_slug=None):
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    try:
        svc.join_pod(pod, request.user)
    except svc.PodError as exc:
        return _redirect_with_error(request, pod, exc.message)
    messages.success(request, f"You joined {pod.name}.")
    return redirect(svc.pods_list_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def retired_request_action(request, *args, **kwargs):
    """Keep stale request forms harmless while clients move to direct Join."""
    return HttpResponse(svc.MSG_REQUESTS_RETIRED, status=410)


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_leave(request, pod_id, slug=None, sprint_slug=None):
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    try:
        svc.remove_member(pod, request.user, actor=request.user)
    except svc.PodError as exc:
        return _redirect_with_error(request, pod, exc.message)
    messages.success(request, f"You left {pod.name}.")
    return redirect(svc.pods_list_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_slack(request, pod_id, slug=None, sprint_slug=None):
    """Set or clear the pod's Slack channel link (pod members at Main+)."""
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    if not svc.is_member(pod, request.user) or not is_slack_eligible(request.user):
        return HttpResponseForbidden("Only pod members on Main or Premium can set the Slack link.")
    try:
        value = clean_channel_url(request.POST.get("slack_channel_url", ""))
    except ValueError as exc:
        request.session[f"pod_slack_error_{pod.pk}"] = str(exc)
        return redirect(f"{svc.pod_url(pod)}#slack")
    pod.slack_channel_url = value
    pod.save(update_fields=["slack_channel_url", "updated_at"])
    success = "Slack channel link removed."
    if value:
        success = "Slack channel saved."
    messages.success(request, success)
    return redirect(f"{svc.pod_url(pod)}#slack")


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_message_create(request, pod_id, slug=None, sprint_slug=None):
    """POST ``.../pods/<pod_id>/messages`` -- send a chat message."""
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    if not chat_svc.chat_enabled():
        raise Http404("Pod chat is not available")
    chat_url = f"{svc.pod_url(pod)}#chat"
    raw_body = request.POST.get("body", "")
    try:
        chat_svc.post_message(pod, request.user, raw_body)
    except chat_svc.ChatError as exc:
        if exc.code not in chat_svc.DRAFT_CODES:
            return HttpResponseForbidden(chat_svc.MSG_NOT_A_MEMBER)
        # Keep the draft (capped) so the member can fix it after the redirect.
        request.session[_chat_session_key(pod)] = {"message": exc.message, "body": raw_body[:DRAFT_MAX_CHARS]}
    return redirect(chat_url)
