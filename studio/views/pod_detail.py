"""Studio study-group settings and operational detail page."""

from zoneinfo import ZoneInfo

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.services.timezones import build_timezone_options
from pods.models import (
    MEETING_MINUTES_CHOICES,
    MEETING_RESPONSE_CANT,
    MEETING_RESPONSE_GOING,
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_HELD,
    MEETING_STATUS_PROPOSED,
    MEETING_STATUS_SCHEDULED,
    POD_STATUS_CHOICES,
    Pod,
    PodMeeting,
    PodMeetingResponse,
)
from pods.services import artifacts as artifact_svc
from pods.services import chat as chat_svc
from pods.services import config as pods_config
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.services.activity import activity_for_pod
from pods.services.meeting_rules import meeting_state
from pods.services.people import display_name
from pods.services.stuck_proposals import proposal_waits
from pods.services.suggestions import load_member_availability, suggest_slots
from studio.decorators import staff_required
from studio.views.pod_shared import pop_results

SCHEDULE_FORM_SESSION_KEY = "studio_pod_schedule_form"
STUDIO_MEETING_STATUS = {
    MEETING_STATUS_PROPOSED: ("pending", "Proposed"),
    MEETING_STATUS_SCHEDULED: ("upcoming", "Scheduled"),
    MEETING_STATUS_HELD: ("completed", "Held"),
    MEETING_STATUS_CANCELLED: ("cancelled", "Cancelled"),
}


def _pod_update_data(request, pod):
    keys = ("name", "purpose", "scheduling_note", "internal_notes", "meeting_count", "meeting_minutes", "status")
    data = {key: request.POST.get(key, "") for key in keys}
    if "meeting_url" in request.POST:
        data["meeting_url"] = mtg.clean_call_link(request.POST["meeting_url"])
    owner_raw = request.POST.get("owner", "")
    if owner_raw == "":
        data["owner"] = None
    elif owner_raw.isdigit():
        membership = pod.memberships.select_related("user").filter(user_id=int(owner_raw)).first()
        data["owner"] = -1
        if membership is not None:
            data["owner"] = membership.user
    return data


def _save_pod(request, pod):
    try:
        data = _pod_update_data(request, pod)
        if data.get("owner") == -1:
            raise svc.PodError("The owner must be a member of the pod.")
        svc.update_pod(pod, data, actor=request.user, staff=True)
    except (ValueError, svc.PodError) as exc:
        return str(exc)
    messages.success(request, "Pod saved.")
    return ""


def _slot_rows(slots):
    rows = []
    for slot in slots:
        local = [
            {
                "name": value["member"].user.email,
                "time": slot.start.astimezone(ZoneInfo(value["timezone"])).strftime("%a %H:%M"),
                "timezone": value["timezone"],
            }
            for value in slot.local_starts
        ]
        rows.append(
            {
                "slot": slot,
                "utc_label": f"{slot.start:%Y-%m-%d %H:%M}–{slot.end:%H:%M} UTC",
                "local": local,
                "missing": [member.user.email for member in slot.missing],
            }
        )
    return rows


def _member_context(pod):
    memberships = list(pod.memberships.select_related("user", "added_by").order_by("joined_at", "pk"))
    users = [membership.user for membership in memberships]
    availability = load_member_availability(users)
    member_avail = [availability[user.pk] for user in users]
    slots = suggest_slots(member_avail, pod.meeting_minutes, all_members=member_avail)
    member_rows = [
        {
            "membership": membership,
            "timezone": availability[membership.user_id].timezone_name,
            "has_windows": availability[membership.user_id].has_windows,
        }
        for membership in memberships
    ]
    return memberships, users, member_rows, _slot_rows(slots)


def _answer_names(meeting, answers, names):
    going = []
    cant = []
    for user_id, name in names.items():
        response = answers.get(user_id)
        if response == MEETING_RESPONSE_CANT:
            cant.append(name)
        elif meeting.status != MEETING_STATUS_PROPOSED or response == MEETING_RESPONSE_GOING:
            going.append(name)
    return going, cant


def _meeting_badge(meeting, state):
    if state.is_expired(meeting):
        return "expired", "Not confirmed"
    return STUDIO_MEETING_STATUS.get(meeting.status, ("draft", meeting.status))


def _meeting_row(meeting, state, waits, responses, names, now):
    answers = responses.get(meeting.pk, {})
    wait = waits.get(meeting.pk)
    badge = _meeting_badge(meeting, state)
    going, cant = _answer_names(meeting, answers, names)
    moved_by = ""
    if meeting.moved_by_id:
        moved_by = display_name(meeting.moved_by)
    series_short = ""
    if meeting.series_id:
        series_short = str(meeting.series_id)[:8]
    no_answer = []
    if wait:
        no_answer = [display_name(user) for user in wait.waiting]
    return {
        "meeting": meeting,
        "number": state.numbers.get(meeting.pk),
        "status_key": badge[0],
        "status_label": badge[1],
        "going": going,
        "cant": cant,
        "no_answer": no_answer,
        "stuck": bool(wait and wait.stuck),
        "unconfirmable": bool(wait and wait.unconfirmable),
        "moved_by": moved_by,
        "series_short": series_short,
        "can_hold": meeting.status == MEETING_STATUS_SCHEDULED and meeting.starts_at <= now,
        "can_cancel": meeting.status != MEETING_STATUS_CANCELLED,
    }


def _meeting_rows(pod, users, state, now):
    names = {user.pk: display_name(user) for user in users}
    response_rows = list(PodMeetingResponse.objects.filter(meeting__pod=pod))
    responses = {}
    for response in response_rows:
        responses.setdefault(response.meeting_id, {})[response.user_id] = response.response
    waits = proposal_waits(pod, state, users, response_rows, hours=pods_config.stuck_proposal_hours())
    return [_meeting_row(meeting, state, waits, responses, names, now) for meeting in state.meetings]


def _meetings_context(pod, users, schedule_form):
    now = timezone.now()
    meetings = list(PodMeeting.objects.filter(pod=pod).select_related("moved_by"))
    state = meeting_state(pod, now, meetings=meetings)
    form = schedule_form or {}
    return {
        "meeting_rows": _meeting_rows(pod, users, state, now),
        "meetings_used": state.used,
        "meetings_held": state.held,
        "schedule_form": {
            "date": form.get("date", ""),
            "time": form.get("time", ""),
            "timezone": form.get("timezone", "UTC"),
            "repeat_weekly": form.get("repeat_weekly", False),
            "error": form.get("error", ""),
        },
        "timezone_options": build_timezone_options(),
    }


def _detail_context(request, pod, activity, error):
    memberships, users, member_rows, slot_rows = _member_context(pod)
    now = timezone.now()
    sprint_edit_url = ""
    if pod.sprint_id:
        sprint_edit_url = reverse("studio_sprint_edit", kwargs={"sprint_id": pod.sprint_id})
    return {
        "pod": pod,
        "error": error,
        **_meetings_context(pod, users, request.session.pop(SCHEDULE_FORM_SESSION_KEY, None)),
        **chat_svc.studio_chat(pod),
        "artifact_rows": artifact_svc.studio_rows(pod, meeting_state(pod, now).numbers),
        "activity": activity.label,
        "activity_kind": activity.kind,
        "activity_noun": activity.noun,
        "memberships": memberships,
        "member_rows": member_rows,
        "slot_rows": slot_rows,
        "status_choices": POD_STATUS_CHOICES,
        "meeting_minute_choices": MEETING_MINUTES_CHOICES,
        "add_results": pop_results(request, pod),
        "member_view_url": svc.pod_url(pod),
        "enabled_for_members": activity.member_pages_enabled(),
        "sprint_edit_url": sprint_edit_url,
    }


@staff_required
def studio_pod_detail(request, pod_id):
    pod = get_object_or_404(Pod.objects.select_related("cohort__course", "sprint", "owner"), pk=pod_id)
    activity = activity_for_pod(pod)
    error = ""
    if request.method == "POST":
        error = _save_pod(request, pod)
        if not error:
            return redirect("studio_pod_detail", pod_id=pod.pk)
        pod.refresh_from_db()
    status = 200
    if error:
        status = 400
    return render(request, "studio/pods/detail.html", _detail_context(request, pod, activity, error), status=status)
